"""Logix client (PLAN 2.2, ADR-0002): OpenFactory exchanges its I/O table with a ControlLogix or
CompactLogix controller, physical or emulated, over EtherNet/IP with pycomm3.

Behavior spec
-------------
:class:`LogixClient` is a CIP *client*: the controller is the server, and OpenFactory polls it
(PLAN §6.2). The controller holds one controller-scoped tag per scene tag, named exactly as in
the scene (``export --format l5x`` writes them), plus ``SIM_HEARTBEAT_DI``.

Each cycle, every ``period_s`` (0.05 s, the 20 Hz of §6.2):

1. **Write** the simulator's tags (DI and AI, the heartbeat included) that changed since the last
   successful write, all in **one** ``write()`` call; after every (re)connect, all of them, since
   the controller may hold stale values.
2. **Read** every PLC tag (DO and AO) in **one** ``read()`` call and put the values into the I/O
   table as the PLC (``Writer.PLC``), in one atomic ``set_many``.

pycomm3 splits a large call into as many CIP multi-service packets as it needs; "one call" is
OpenFactory's batching. The blocking pycomm3 calls run in a worker thread, so the event loop
(the viewer, the other servers) never waits on the network.

**Types.** A ``bool`` tag is a Logix ``BOOL``, a ``float32`` tag a ``REAL``, an ``int16`` tag an
``INT``. On connect the client checks every scene tag against the controller's uploaded tag list
(pycomm3 ``LogixDriver.tags``, ``data_type_name``): a tag the controller lacks is **missing**, one
of another type **mismatched**; both are logged once per connection, listed in :meth:`status`, and
left out of the batches, and the rest keep running. A tag the controller rejects in a read or
write (pycomm3 returns a ``Tag`` whose ``error`` is set) is treated the same way.

**Link loss, the outputs** (``on_link_loss``, per connection; owner's ruling 2026-11-10). With
``"off"``, the default, once no exchange with the controller has succeeded for
``link_loss_timeout_s`` (1.0 s), every PLC-written tag (DO, AO) goes to its off state (False, 0.0,
0) in one atomic write, logged once per outage, as a real output module goes to its fault state
on a comms fault; the controller's values return with the first read after reconnecting. A
watchdog task measures from the last successful exchange, so a cycle stuck in pycomm3's socket
timeout trips it on time too. With ``"hold"`` the I/O table keeps the PLC's last values.

**Link loss, the connection.** Any pycomm3 communication error (``PycommError``) or socket
error (and any other error in a cycle, also logged with its traceback) closes the driver, logs
one line, and reconnects after a backoff of 0.5, 1, 2, 4 then 5 s per attempt, until it
succeeds, with no restart of either side. The PLC detects the simulator's absence from
``SIM_HEARTBEAT_DI`` (§6.1), which stops changing.

**Path.** The CIP path is a named connection in ``openfactory.toml`` /
``openfactory.local.toml`` (``[connections.<name>]``, ``protocol = "logix"``, ``path``), never in
a scene (ADR-0002: a bench 5380 is ``<ip>``; an Echo 5580 ``<ip>/<slot>``; Logix Emulate
``<host>/<slot>``). ``docs/logix.md`` says how to point it at each.

The driver is injected (``driver_factory``) so tests run it against a fake controller; the
default is pycomm3's ``LogixDriver(path, init_program_tags=False)`` from the ``logix`` extra.
Training and demo use only; not a safety-rated system.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from openfactory.core.iotable import IOTable, Writer

log = logging.getLogger(__name__)

#: OpenFactory tag dtype -> Logix atomic data type.
LOGIX_TYPE = {"bool": "BOOL", "float32": "REAL", "int16": "INT"}
#: Seconds to wait before reconnect attempt 1, 2, 3, ...; the last repeats.
BACKOFF_S = (0.5, 1.0, 2.0, 4.0, 5.0)
PERIOD_S = 0.05
#: A PLC-written tag's off state, by dtype (on_link_loss = "off").
OFF_VALUE: dict[str, Any] = {"bool": False, "float32": 0.0, "int16": 0}


class Driver(Protocol):
    """The part of pycomm3's ``LogixDriver`` the client uses."""

    tags: dict[str, Any]

    def open(self) -> Any: ...
    def close(self) -> Any: ...
    def read(self, *tags: str) -> Any: ...
    def write(self, *tags_values: tuple[str, Any]) -> Any: ...


def _comm_errors() -> tuple[type[BaseException], ...]:
    try:
        from pycomm3.exceptions import PycommError
    except ImportError:  # the logix extra is not installed: only a fake driver can be used
        return (OSError,)
    return (PycommError, OSError)


def default_driver(path: str) -> Driver:
    from pycomm3 import LogixDriver  # the logix extra

    return LogixDriver(path, init_program_tags=False)


def _as_list(result: Any) -> list[Any]:
    """pycomm3 returns one ``Tag`` for one tag and a list for several. A ``Tag`` is a
    NamedTuple, so "is it a tuple" cannot tell them apart: only a list is several."""
    return result if isinstance(result, list) else [result]


@dataclass
class LogixStatus:
    path: str
    connected: bool = False
    connects: int = 0
    reconnects: int = 0
    cycles: int = 0
    reads: int = 0
    writes: int = 0
    last_error: str | None = None
    outputs_off: bool = False  # the PLC's outputs were set off for this outage
    output_drops: int = 0  # outages that set them off
    missing: list[str] = field(default_factory=list)
    mismatched: dict[str, str] = field(default_factory=dict)  # tag -> "REAL, expected BOOL"


class LogixClient:
    def __init__(
        self,
        io: IOTable,
        path: str,
        *,
        period_s: float = PERIOD_S,
        backoff_s: tuple[float, ...] = BACKOFF_S,
        driver_factory: Callable[[str], Driver] = default_driver,
        on_link_loss: str = "off",
        link_loss_timeout_s: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if on_link_loss not in ("off", "hold"):
            raise ValueError(f"on_link_loss must be 'off' or 'hold', not {on_link_loss!r}")
        self.on_link_loss = on_link_loss
        self.link_loss_timeout_s = link_loss_timeout_s
        self._clock = clock
        self.io = io
        self.path = path
        self.period_s = period_s
        self.backoff_s = backoff_s
        self._factory = driver_factory
        self._errors = _comm_errors()
        tags = io.tags()
        self._sim = [t.name for t in tags if t.kind in ("DI", "AI")]  # we write these
        self._plc = [t.name for t in tags if t.kind in ("DO", "AO")]  # we read these
        self._off = {t.name: OFF_VALUE[t.dtype] for t in tags if t.kind in ("DO", "AO")}
        self._last_ok = clock()  # the last successful exchange (or the start)
        self._type = {t.name: LOGIX_TYPE[t.dtype] for t in tags}
        self._sent: dict[str, Any] = {}  # last value the controller acknowledged, per sim tag
        self._skip: set[str] = set()
        self._driver: Driver | None = None
        self._stop = asyncio.Event()
        self._ready = asyncio.Event()
        self.state = LogixStatus(path)

    # -- the cycle --------------------------------------------------------------------------------

    def _check_tags(self, driver: Driver) -> None:
        """Missing and mismatched tags against the controller's tag list (once per connect)."""
        listed = getattr(driver, "tags", None) or {}
        missing, mismatched = [], {}
        for name, want in self._type.items():
            info = listed.get(name)
            if info is None:
                missing.append(name)
                continue
            got = info.get("data_type_name") if isinstance(info, dict) else None
            if got != want:
                mismatched[name] = f"{got}, expected {want}"
        self.state.missing, self.state.mismatched = missing, mismatched
        self._skip = set(missing) | set(mismatched)
        if missing:
            log.warning("logix %s: tags missing in the controller: %s", self.path, missing)
        for name, why in mismatched.items():
            log.warning("logix %s: tag %s is %s; left out", self.path, name, why)

    def _reject(self, name: str, error: str) -> None:
        if name not in self._skip:
            log.warning("logix %s: tag %s rejected (%s); left out", self.path, name, error)
            self._skip.add(name)
            if "exist" in error.lower():
                self.state.missing.append(name)
            else:
                self.state.mismatched[name] = error

    def _cycle(self, driver: Driver) -> None:
        """One write then one read, blocking (runs in a worker thread)."""
        now = self.io.values()
        changed = [
            (n, now[n]) for n in self._sim if n not in self._skip and self._sent.get(n) != now[n]
        ]
        if changed:
            for result in _as_list(driver.write(*changed)):
                if result.error:
                    self._reject(result.tag, str(result.error))
                else:
                    self._sent[result.tag] = dict(changed)[result.tag]
            self.state.writes += 1
        names = [n for n in self._plc if n not in self._skip]
        if names:
            values = {}
            for result in _as_list(driver.read(*names)):
                if result.error:
                    self._reject(result.tag, str(result.error))
                else:
                    values[result.tag] = result.value
            self.state.reads += 1
            if values:
                self.io.set_many(values, Writer.PLC)
        self.state.cycles += 1

    def _connect(self) -> Driver:
        driver = self._factory(self.path)
        try:
            driver.open()
            self._check_tags(driver)
        except BaseException:
            with contextlib.suppress(Exception):
                driver.close()
            raise
        self._sent.clear()  # everything goes out again after a (re)connect
        return driver

    def _close(self) -> None:
        driver, self._driver = self._driver, None
        if driver is not None:
            with contextlib.suppress(Exception):
                driver.close()

    def _check_link_loss(self) -> None:
        """With on_link_loss "off": the PLC's outputs off once the link has been silent for the
        timeout, once per outage (called by the watchdog, on the event loop)."""
        if self.on_link_loss != "off" or self.state.outputs_off:
            return
        silent = self._clock() - self._last_ok
        if silent <= self.link_loss_timeout_s:
            return
        self.io.set_many(self._off, Writer.PLC)
        self.state.outputs_off = True
        self.state.output_drops += 1
        log.warning(
            "logix %s: no exchange for %.1f s; every PLC output set off (on_link_loss = off)",
            self.path,
            silent,
        )

    async def _watchdog(self) -> None:
        interval = min(0.05, self.link_loss_timeout_s / 4)
        while not self._stop.is_set():
            self._check_link_loss()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=interval)

    async def serve_forever(self) -> None:
        watchdog = asyncio.create_task(self._watchdog())
        try:
            await self._serve()
        finally:
            await self.shutdown()
            await watchdog

    async def _serve(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            try:
                if self._driver is None:
                    self._driver = await asyncio.to_thread(self._connect)
                    self.state.connected = True
                    self.state.connects += 1
                    if self.state.connects > 1:
                        self.state.reconnects += 1
                        log.info("logix %s: reconnected", self.path)
                    else:
                        log.info("logix %s: connected", self.path)
                    attempt = 0
                    self._ready.set()
                await asyncio.to_thread(self._cycle, self._driver)
                self._last_ok = self._clock()
                if self.state.outputs_off:
                    self.state.outputs_off = False  # the read just restored the PLC's values
                    log.info("logix %s: PLC outputs restored from the controller", self.path)
            except Exception as exc:  # noqa: BLE001 - never end the task: log and reconnect
                if not isinstance(exc, self._errors):
                    log.exception("logix %s: unexpected error in the cycle", self.path)
                was_connected = self.state.connected
                self._close()
                self.state.connected = False
                self.state.last_error = f"{type(exc).__name__}: {exc}"
                delay = self.backoff_s[min(attempt, len(self.backoff_s) - 1)]
                attempt += 1
                if was_connected or attempt == 1:  # one line per outage, not one per attempt
                    log.warning(
                        "logix %s: link lost (%s); reconnecting with backoff",
                        self.path,
                        self.state.last_error,
                    )
                self._ready.set()  # a run does not wait for the controller
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), timeout=delay)
                continue
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self.period_s)
        self._close()
        self.state.connected = False

    async def wait_ready(self, timeout: float = 10.0) -> None:
        """Until the first connect attempt has finished (either way): a run starts even with the
        controller unreachable, and keeps trying."""
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._ready.wait(), timeout)

    async def shutdown(self) -> None:
        self._stop.set()

    def status(self) -> dict[str, Any]:
        s = self.state
        return {
            "path": s.path,
            "connected": s.connected,
            "connects": s.connects,
            "reconnects": s.reconnects,
            "cycles": s.cycles,
            "reads": s.reads,
            "writes": s.writes,
            "last_error": s.last_error,
            "on_link_loss": self.on_link_loss,
            "outputs_off": s.outputs_off,
            "output_drops": s.output_drops,
            "missing": sorted(s.missing),
            "mismatched": dict(s.mismatched),
        }
