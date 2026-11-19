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

**Plant names** (PLAN 5.3b, design note §1.4). A scene whose bindings carry ``plc_tag`` (5.3a)
is exchanged by those names: ``plant_names`` maps each scene tag to the name the controller
knows it by (its ``plc_tag``, the heartbeat's ``plc.heartbeat_plc_tag``; a binding without one
keeps its scene tag). The batches are the same, one ``write()`` and one ``read()`` a cycle, with
plant names in them; pycomm3 returns each result under the name requested (``LogixDriver.read``
and ``write`` build ``Tag(request_data["user_tag"], ...)``), and the client puts it into the I/O
table under the scene tag. pycomm3 needs a tag's definition to read or write it (the
``LogixDriver.__init__`` docstring: "Initialization of tags is required for the read and write
to work"), and with ``init_program_tags=False`` it uploads controller-scoped definitions only, so
the default driver is opened with ``init_program_tags=True`` exactly when a plant name starts
with ``Program:`` (``get_tag_list(program="*")``, whose docstring names program tags
``Program:{program}.{tag_name}``, as the scene writes them).

**The type check by plant name.** pycomm3's ``LogixDriver.get_tag_info`` looks a name up in the
uploaded definitions (a ``Program:`` base takes its next segment with it, an ``[n]`` is
stripped, members are found through ``data_type["internal_tags"]``), but it does not strip a
trailing bit, which ``_parse_tag_request`` does when reading (a last segment of digits is a
bit). So the client splits the bit off itself, looks up the rest, and accepts:

- for a ``bool`` point: a ``BOOL`` (a controller or program tag, a structure's ``BOOL`` member,
  which pycomm3 lists as a bit member), an element of a ``BOOL`` array (which pycomm3 types
  ``DWORD``), or a **bit of a SINT, INT or DINT** whose number is in that type's range: 0 to 7,
  0 to 15 and 0 to 31 (1756-PM004L-EN-P, revision L, ch. 2: "Address tag data" names a bit as a
  bit of a SINT, INT or DINT; "Data type" gives their widths as 8, 16 and 32 bits). A bit of any
  other type, a LINT included (that manual does not name one), is refused;
- for a ``float32`` point: a ``REAL``; for an ``int16`` point: an ``INT``; a bit on either is
  refused.

A name the controller does not have is **missing**, one of another type or out of range
**mismatched** (the reason names the plant name); both are logged once per connection and left
out of the batches, as before. A scene with no ``plc_tag`` takes the path above this section
unchanged: scene tags, ``init_program_tags=False``, the check against ``LogixDriver.tags``.

**Path.** The CIP path is a named connection in ``openfactory.toml`` /
``openfactory.local.toml`` (``[connections.<name>]``, ``protocol = "logix"``, ``path``), never in
a scene (ADR-0002: a bench 5380 is ``<ip>``; an Echo 5580 ``<ip>/<slot>``; Logix Emulate
``<host>/<slot>``). ``docs/logix.md`` says how to point it at each.

The driver is injected (``driver_factory``) so tests run it against a fake controller; the
default is pycomm3's ``LogixDriver(path, init_program_tags=...)`` from the ``logix`` extra
(``False`` unless a plant name is program-scoped, above).
Training and demo use only; not a safety-rated system.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from openfactory.core.iotable import IOTable, Writer

if TYPE_CHECKING:
    from openfactory.core.schema import Scene

log = logging.getLogger(__name__)

#: OpenFactory tag dtype -> Logix atomic data type.
LOGIX_TYPE = {"bool": "BOOL", "float32": "REAL", "int16": "INT"}
#: Seconds to wait before reconnect attempt 1, 2, 3, ...; the last repeats.
BACKOFF_S = (0.5, 1.0, 2.0, 4.0, 5.0)
PERIOD_S = 0.05
#: A PLC-written tag's off state, by dtype (on_link_loss = "off").
OFF_VALUE: dict[str, Any] = {"bool": False, "float32": 0.0, "int16": 0}
#: The integer types a bit may be taken from, and their bits (1756-PM004L-EN-P, revision L,
#: ch. 2, "Address tag data" and "Data type": a bit of a SINT, INT or DINT; 8, 16, 32 bits).
BIT_WIDTH = {"SINT": 8, "INT": 16, "DINT": 32}
#: The publication the bit rules are from, for the messages.
MANUAL = "1756-PM004L-EN-P, ch. 2"


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


def default_driver(path: str, init_program_tags: bool = False) -> Driver:
    from pycomm3 import LogixDriver  # the logix extra

    return LogixDriver(path, init_program_tags=init_program_tags)


def plant_names(scene: Scene) -> dict[str, str]:
    """Scene tag -> the controller's name for it, for every point with a ``plc_tag`` and the
    heartbeat with a ``heartbeat_plc_tag`` (PLAN 5.3a); empty for a scene with none."""
    from openfactory.core.heartbeat import HEARTBEAT_TAG

    names = {b.name: b.plc_tag for b in scene.tag_bindings() if b.plc_tag}
    if scene.plc.heartbeat_plc_tag:
        names[HEARTBEAT_TAG] = scene.plc.heartbeat_plc_tag
    return names


def split_bit(name: str) -> tuple[str, int | None]:
    """``Word.4`` -> (``Word``, 4); a name without a trailing bit -> (name, None). The split
    pycomm3's ``LogixDriver._parse_tag_request`` makes: a ``Program:`` base takes its next
    segment, and a last segment made only of digits is a bit."""
    base, *attrs = name.split(".")
    if base.startswith("Program:") and attrs:
        base = f"{base}.{attrs.pop(0)}"
    if attrs and attrs[-1].isdigit():
        bit = int(attrs.pop())
        return ".".join([base, *attrs]), bit
    return name, None


def type_problem(info: Mapping[str, Any], name: str, want: str) -> str | None:
    """Why the controller's definition ``info`` (pycomm3 ``get_tag_info`` of ``name`` without its
    bit) cannot carry a point of Logix type ``want``; None when it can. See the module spec."""
    got = info.get("data_type_name")
    _, bit = split_bit(name)
    if bit is not None:
        if want != "BOOL":
            return f"{name} is a bit, expected {want}"
        a = "an" if str(got)[:1] in ("I", "L") else "a"  # an INT, an LINT; a SINT, a REAL
        if got not in BIT_WIDTH:
            return (f"{name} is a bit of {a} {got}; a bit is a bit of a SINT, INT or DINT "
                    f"({MANUAL}, Address tag data)")  # fmt: skip
        if bit >= BIT_WIDTH[got]:
            return (f"{name} is bit {bit} of {a} {got}, whose bits are 0 to {BIT_WIDTH[got] - 1} "
                    f"({MANUAL}, Data type)")  # fmt: skip
        return None
    if want == "BOOL" and got == "DWORD" and name.endswith("]"):
        return None  # an element of a BOOL array, which pycomm3 types DWORD
    return None if got == want else f"{name} is {got}, expected {want}"


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
        driver_factory: Callable[[str], Driver] | None = None,
        on_link_loss: str = "off",
        link_loss_timeout_s: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
        plant: Mapping[str, str] | None = None,
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
        tags = io.tags()
        #: PLAN 5.3b: scene tag -> the controller's name for it (itself when it has no plc_tag).
        self.plant = dict(plant or {})
        self._name = {t.name: self.plant.get(t.name, t.name) for t in tags}
        self._scene_of = {v: k for k, v in self._name.items()}
        #: Whether the driver uploads program-scoped definitions (a plant name needs them).
        self.init_program_tags = any(n.startswith("Program:") for n in self.plant.values())
        self._factory = driver_factory or functools.partial(
            default_driver, init_program_tags=self.init_program_tags
        )
        self._errors = _comm_errors()
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
        if self.plant:
            self._check_plant_names(driver)
            return
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

    def _check_plant_names(self, driver: Driver) -> None:
        """PLAN 5.3b: each point by the controller's name for it, through pycomm3's
        ``get_tag_info`` with the bit split off here (see the module spec)."""
        missing, mismatched = [], {}
        for scene_tag, want in self._type.items():
            name = self._name[scene_tag]
            rest, _ = split_bit(name)
            try:
                info = driver.get_tag_info(rest)  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001 - pycomm3 raises RequestError for an unknown name
                info = None
            if not isinstance(info, Mapping):
                missing.append(scene_tag)
                continue
            problem = type_problem(info, name, want)
            if problem is not None:
                mismatched[scene_tag] = problem
        self.state.missing, self.state.mismatched = missing, mismatched
        self._skip = set(missing) | set(mismatched)
        if missing:
            named = [f"{t} ({self._name[t]})" if self._name[t] != t else t for t in missing]
            log.warning("logix %s: tags missing in the controller: %s", self.path, named)
        for scene_tag, why in mismatched.items():
            log.warning("logix %s: tag %s: %s; left out", self.path, scene_tag, why)

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
        name, scene_of = self._name, self._scene_of  # the controller's names (PLAN 5.3b)
        if changed:
            for result in _as_list(driver.write(*[(name[n], v) for n, v in changed])):
                tag = scene_of.get(result.tag, result.tag)
                if result.error:
                    self._reject(tag, str(result.error))
                else:
                    self._sent[tag] = dict(changed)[tag]
            self.state.writes += 1
        names = [n for n in self._plc if n not in self._skip]
        if names:
            values = {}
            for result in _as_list(driver.read(*[name[n] for n in names])):
                tag = scene_of.get(result.tag, result.tag)
                if result.error:
                    self._reject(tag, str(result.error))
                else:
                    values[tag] = result.value
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
