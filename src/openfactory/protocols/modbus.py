"""Modbus TCP server bound live to the I/O table (PLAN §6.1, task 1.7).

Behavior spec
-------------
**Register map.** :func:`allocate` assigns every tag in the I/O table to one Modbus table by kind:

========  ==========================  ======================  ==================================
Kind      Table                       Function codes          Width
========  ==========================  ======================  ==================================
DI        discrete inputs             FC02 (read)             1 bit
DO        coils                       FC01, FC05, FC15        1 bit
AI        input registers             FC04 (read)             2 words (float32) or 1 (int16_scaled)
AO        holding registers           FC03, FC06, FC16        2 words (float32) or 1 (int16_scaled)
========  ==========================  ======================  ==================================

Addresses are zero-based protocol addresses. Analog encoding is per scene (ADR-0003): ``float32`` is
IEEE-754 with the high word at the lower address; ``int16_scaled`` is 0–32767 over the tag's range,
clamped both ways. The same :class:`RegisterMap` drives the server and the exported I/O CSV.

**Address stability (ADR-0004).** Within each table an address comes from, in order: a binding that
pins it (``{tag, address}`` in the scene), the scene's lockfile (``<scene>.modbus.yaml``, committed
next to the scene), or allocation, which appends the remaining tags in tag-name order after the
highest address already used in that table. With no lockfile and no pins that is the plain packed
allocation from 0. :func:`scene_register_map` writes the lockfile on first use and rewrites it only
when its content changes. The lockfile only grows: a tag removed from the scene keeps its line,
marked ``# not in scene``, so its address is never given to another tag and comes back if the tag
is re-added; deleting the line frees it. A lockfile written for the other analog encoding, two tags
on overlapping addresses, or an address past 65535 is an error (:class:`AddressError`).

**Live binding.** The server is one pymodbus ``SimDevice`` (id 0 answers every unit id) with four
separate tables and an ``action`` hook (``docs/research/libraries.md``, "pymodbus 3.x datastore
binding"; verified against the pymodbus 3.16.1 source). On every read the hook refills the whole
table from the I/O table, so the PLC always sees current values. On every write (FC05/06/15/16) it
decodes the request and writes the DO/AO tags as :attr:`Writer.PLC`, all or nothing. The hook is
idempotent (pymodbus can call it twice for one FC05) and never raises: an address outside the
allocation returns ILLEGAL ADDRESS (2), a NaN or infinite float returns ILLEGAL DATA VALUE (3), and
any other failure is logged and returns SERVER DEVICE FAILURE (4). A write to part of a float32 pair
(FC06, or FC16 starting mid-pair) is merged with the tag's current other word.

Unallocated addresses inside a table (holes left by pins or retired lockfile entries) read as 0 or
False, and a write that touches one is rejected with ILLEGAL ADDRESS. Bit reads past the last
allocated bit but inside its final 16-bit word return False (pymodbus hands the hook a word count
for bit reads); bit writes past the allocation are rejected. Tables with no tags still answer at
address 0 only with ILLEGAL ADDRESS.

The server always binds the address it is given; callers resolve it with
:func:`openfactory.core.config.resolve_endpoint` (default ``127.0.0.1:5020``).
"""

from __future__ import annotations

import asyncio
import logging
import math
import struct
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import yaml
from pymodbus.constants import ExcCodes
from pymodbus.server import ModbusTcpServer
from pymodbus.simulator import DataType, SimData, SimDevice
from pymodbus.simulator.simutils import SimUtils

from openfactory.core.config import Endpoint
from openfactory.core.iotable import IOTable, Tag, Writer

if TYPE_CHECKING:
    from openfactory.core.schema import Scene

log = logging.getLogger(__name__)

Encoding = Literal["float32", "int16_scaled"]
Table = Literal["di", "co", "ir", "hr"]

TABLE_FOR_KIND: dict[str, Table] = {"DI": "di", "DO": "co", "AI": "ir", "AO": "hr"}
TABLE_LABEL: dict[Table, str] = {"di": "DI", "co": "CO", "ir": "IR", "hr": "HR"}
KIND_ORDER = ("DI", "DO", "AI", "AO")
#: Function code -> table, for reads and writes.
FC_TABLE: dict[int, Table] = {
    1: "co",
    2: "di",
    3: "hr",
    4: "ir",
    5: "co",
    6: "hr",
    15: "co",
    16: "hr",
}
RAW_MAX = 32767
#: Highest zero-based address in a Modbus table.
ADDRESS_MAX = 65535
#: Lockfile section names, in file order (ADR-0004).
LOCK_SECTION: dict[Table, str] = {
    "di": "discrete_inputs",
    "co": "coils",
    "ir": "input_registers",
    "hr": "holding_registers",
}
LOCKFILE_SUFFIX = ".modbus.yaml"


# --- encoding ------------------------------------------------------------------------------------


def _span(tag: Tag) -> tuple[float, float]:
    lo, hi = tag.range or (0.0, 100.0)
    return lo, hi


def encode(tag: Tag, value: float, encoding: Encoding) -> list[int]:
    """Engineering value -> register words."""
    if encoding == "float32":
        hi, lo = struct.unpack(">HH", struct.pack(">f", float(value)))
        return [hi, lo]
    lo_v, hi_v = _span(tag)
    raw = round((float(value) - lo_v) / (hi_v - lo_v) * RAW_MAX)
    return [min(max(raw, 0), RAW_MAX)]


def decode(tag: Tag, words: list[int], encoding: Encoding) -> float:
    """Register words -> engineering value. Raises ValueError for NaN or infinity."""
    if encoding == "float32":
        value = struct.unpack(">f", struct.pack(">HH", words[0] & 0xFFFF, words[1] & 0xFFFF))[0]
        if not math.isfinite(value):
            raise ValueError(f"{tag.name}: non-finite float32 {value!r}")
        return value
    lo_v, hi_v = _span(tag)
    raw = min(max(words[0] & 0xFFFF, 0), RAW_MAX)  # unsigned writes above 32767 read as 100 %
    return lo_v + raw / RAW_MAX * (hi_v - lo_v)


# --- allocation ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Assignment:
    tag: str
    kind: str
    table: Table
    address: int
    width: int

    @property
    def label(self) -> str:
        """Human-readable address, e.g. ``CO:3`` or ``IR:0-1``."""
        prefix = TABLE_LABEL[self.table]
        if self.width == 1:
            return f"{prefix}:{self.address}"
        return f"{prefix}:{self.address}-{self.address + self.width - 1}"


@dataclass
class RegisterMap:
    encoding: Encoding
    assignments: list[Assignment] = field(default_factory=list)
    #: Locked addresses of tags no longer in the scene, kept reserved (ADR-0004).
    retired: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.by_tag = {a.tag: a for a in self.assignments}
        self.size: dict[Table, int] = dict.fromkeys(("di", "co", "ir", "hr"), 0)
        self.at: dict[Table, dict[int, Assignment]] = {t: {} for t in ("di", "co", "ir", "hr")}
        for a in self.assignments:
            self.size[a.table] = max(self.size[a.table], a.address + a.width)
            for offset in range(a.width):
                self.at[a.table][a.address + offset] = a

    def describe(self) -> str:
        """The map as printed at startup (PLAN §6.1)."""
        lines = [f"Modbus register map ({self.encoding}, zero-based addresses):"]
        lines += [f"  {a.label:<10} {a.tag}" for a in self.assignments]
        return "\n".join(lines)


class AddressError(ValueError):
    """A pinned or locked Modbus address cannot be honored (ADR-0004)."""


@dataclass(frozen=True)
class AddressLock:
    """The content of a ``<scene>.modbus.yaml`` lockfile (ADR-0004)."""

    encoding: Encoding
    #: Tag name -> zero-based address. The table follows from the tag's kind suffix.
    addresses: dict[str, int] = field(default_factory=dict)


def _kind_of(tag_name: str) -> str:
    kind = tag_name.rsplit("_", 1)[-1]
    if kind not in TABLE_FOR_KIND:
        raise AddressError(f"{tag_name!r} has no _DI/_DO/_AI/_AO suffix")
    return kind


def _width(kind: str, encoding: Encoding) -> int:
    return 2 if kind in ("AI", "AO") and encoding == "float32" else 1


def allocate(
    tags: Iterable[Tag],
    encoding: Encoding,
    *,
    pins: Mapping[str, int] | None = None,
    lock: AddressLock | None = None,
) -> RegisterMap:
    """Assign every tag an address: pinned, then locked, then appended (ADR-0004).

    Without ``pins`` or ``lock`` this is sorted by kind (DI, DO, AI, AO), then tag name, packed from
    0 in each table. Locked entries for tags not in ``tags`` keep their addresses reserved.
    """
    pins = dict(pins or {})
    if lock is not None and lock.encoding != encoding:
        raise AddressError(
            f"the address lockfile was written for {lock.encoding} but the scene uses {encoding}; "
            "analog addresses would change. Set plc.analog_encoding back, or delete the lockfile "
            "to re-allocate and update the PLC program to the new map"
        )
    locked = dict(lock.addresses) if lock else {}
    ordered = sorted(tags, key=lambda t: (KIND_ORDER.index(t.kind), t.name))
    present = {t.name for t in ordered}
    unknown = sorted(set(pins) - present)
    if unknown:
        raise AddressError(f"pinned address for unknown tag(s): {', '.join(unknown)}")
    used: dict[Table, dict[int, str]] = {t: {} for t in ("di", "co", "ir", "hr")}
    chosen: dict[str, int] = {}

    def claim(name: str, address: int, source: str) -> None:
        kind = _kind_of(name)
        table, width = TABLE_FOR_KIND[kind], _width(kind, encoding)
        if address < 0 or address + width - 1 > ADDRESS_MAX:
            raise AddressError(f"{name}: {source} address {address} is outside 0-{ADDRESS_MAX}")
        for addr in range(address, address + width):
            other = used[table].get(addr)
            if other is not None:
                raise AddressError(
                    f"{name} ({source}) and {other} both use {TABLE_LABEL[table]}:{addr}; "
                    "change the pin or delete the conflicting line from the lockfile"
                )
        for addr in range(address, address + width):
            used[table][addr] = name
        chosen[name] = address

    for name, address in sorted(pins.items()):
        claim(name, address, "pinned")
    for name, address in sorted(locked.items(), key=lambda kv: (kv[1], kv[0])):
        if name not in chosen:
            claim(name, address, "locked")
    for tag in ordered:
        if tag.name not in chosen:
            table = TABLE_FOR_KIND[tag.kind]
            claim(tag.name, max(used[table], default=-1) + 1, "allocated")

    out = [
        Assignment(t.name, t.kind, TABLE_FOR_KIND[t.kind], chosen[t.name], _width(t.kind, encoding))
        for t in ordered
    ]
    out.sort(key=lambda a: (KIND_ORDER.index(a.kind), a.address))
    retired = {name: addr for name, addr in chosen.items() if name not in present}
    return RegisterMap(encoding, out, retired)


# --- lockfile (ADR-0004) -------------------------------------------------------------------------


def lockfile_path(scene_path: str | Path) -> Path:
    """``scenes/<name>.yaml`` -> ``scenes/<name>.modbus.yaml``."""
    return Path(scene_path).with_suffix(LOCKFILE_SUFFIX)


def read_lock(path: str | Path) -> AddressLock | None:
    """Parse a lockfile, or return None when it does not exist."""
    path = Path(path)
    if not path.is_file():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict) or data.get("encoding") not in ("float32", "int16_scaled"):
        raise AddressError(f"{path}: not an OpenFactory address lockfile (no valid 'encoding')")
    addresses: dict[str, int] = {}
    for table, section in LOCK_SECTION.items():
        for name, address in (data.get(section) or {}).items():
            if TABLE_FOR_KIND[_kind_of(name)] != table:
                raise AddressError(f"{path}: {name} is listed under {section}")
            if not isinstance(address, int) or isinstance(address, bool):
                raise AddressError(f"{path}: {name} has a non-integer address {address!r}")
            addresses[name] = address
    unknown = set(data) - {"encoding", *LOCK_SECTION.values()}
    if unknown:
        raise AddressError(f"{path}: unknown key(s) {', '.join(sorted(unknown))}")
    return AddressLock(data["encoding"], addresses)


def lock_text(register_map: RegisterMap, scene_file: str = "the scene") -> str:
    """The lockfile for a register map, as written to disk (deterministic)."""
    lines = [
        f"# Modbus address lockfile for {scene_file} (ADR-0004). Generated by OpenFactory;",
        "# commit it with the scene. Existing tags keep these zero-based addresses; new tags are",
        "# appended after the highest address in their table. A line marked 'not in scene'",
        "# reserves the address of a removed tag; delete the line to free it.",
        f"encoding: {register_map.encoding}",
    ]
    entries: dict[Table, list[tuple[int, str, bool]]] = {t: [] for t in LOCK_SECTION}
    for a in register_map.assignments:
        entries[a.table].append((a.address, a.tag, True))
    for name, address in register_map.retired.items():
        entries[TABLE_FOR_KIND[_kind_of(name)]].append((address, name, False))
    for table, section in LOCK_SECTION.items():
        rows = sorted(entries[table])
        if not rows:
            lines.append(f"{section}: {{}}")
            continue
        lines.append(f"{section}:")
        lines += [
            f"  {name}: {address}" + ("" if live else "  # not in scene")
            for address, name, live in rows
        ]
    return "\n".join(lines) + "\n"


def scene_register_map(
    scene: Scene,
    encoding: Encoding | None = None,
    *,
    scene_path: str | Path | None = None,
    write: bool = True,
) -> RegisterMap:
    """The register map for a scene: pins from its bindings, plus its lockfile when ``scene_path``
    is given. With ``write``, the lockfile is created or updated if its content changed."""
    from openfactory.core.loader import build_iotable  # runtime import: loader is heavier

    enc: Encoding = encoding or scene.plc.analog_encoding
    pins = {b.name: b.address for b in scene.tag_bindings() if b.address is not None}
    path = lockfile_path(scene_path) if scene_path is not None else None
    lock = read_lock(path) if path is not None else None
    register_map = allocate(build_iotable(scene).tags(), enc, pins=pins, lock=lock)
    if path is not None and write:
        text = lock_text(register_map, Path(scene_path).name)  # type: ignore[arg-type]
        if not path.is_file() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8")
            log.info("wrote Modbus address lockfile %s", path)
    return register_map


# --- server --------------------------------------------------------------------------------------


def _bits_padded(n: int) -> int:
    return max(16, -(-n // 16) * 16)


def _block(table: Table, size: int) -> list[SimData]:
    if table in ("di", "co"):
        return [SimData(0, count=_bits_padded(size), values=False, datatype=DataType.BITS)]
    if size == 0:
        return [SimData(0, count=1, datatype=DataType.INVALID)]
    return [SimData(0, count=size, values=0, datatype=DataType.REGISTERS)]


class ModbusServer:
    """Modbus TCP server whose registers are the I/O table."""

    def __init__(
        self,
        io: IOTable,
        encoding: Encoding,
        endpoint: Endpoint,
        register_map: RegisterMap | None = None,
    ) -> None:
        self.io = io
        self.encoding = encoding
        self.endpoint = endpoint
        self.map = register_map or allocate(io.tags(), encoding)
        if self.map.encoding != encoding:
            raise ValueError(f"register map is {self.map.encoding}, server encoding is {encoding}")
        self.device = SimDevice(
            id=0,
            simdata=(
                _block("co", self.map.size["co"]),
                _block("di", self.map.size["di"]),
                _block("hr", self.map.size["hr"]),
                _block("ir", self.map.size["ir"]),
            ),
            action=self._hook,
        )
        self._server: ModbusTcpServer | None = None

    # -- table images ----------------------------------------------------------------------------

    def _words(self, table: Table, values: dict[str, object]) -> list[int]:
        words = [0] * self.map.size[table]
        for a in self.map.assignments:
            if a.table == table:
                words[a.address : a.address + a.width] = encode(
                    self.io.tag(a.tag),
                    float(values[a.tag]),
                    self.encoding,  # type: ignore[arg-type]
                )
        return words

    def _bits(self, table: Table, values: dict[str, object]) -> list[bool]:
        bits = [False] * _bits_padded(self.map.size[table])
        for a in self.map.assignments:
            if a.table == table:
                bits[a.address] = bool(values[a.tag])
        return bits

    # -- pymodbus action hook --------------------------------------------------------------------

    async def _hook(
        self,
        function_code: int,
        start_address: int,
        address: int,
        count: int,
        registers: list[int],
        set_values: list[int] | list[bool] | None,
    ) -> ExcCodes | None:
        try:
            table = FC_TABLE.get(function_code)
            if table is None:
                return ExcCodes.ILLEGAL_FUNCTION
            if set_values is None:
                self._refill(table, registers)
                return None
            return self._write(table, address, list(set_values))
        except Exception:
            log.exception("Modbus hook failed (FC%02d @ %d)", function_code, address)
            return ExcCodes.DEVICE_FAILURE

    def _refill(self, table: Table, registers: list[int]) -> None:
        """Copy the whole table from the I/O table into pymodbus's block, in place."""
        values = self.io.values()
        if table in ("di", "co"):
            packed = SimUtils.bitsToRegisters(self._bits(table, values))
        else:
            packed = self._words(table, values)
        registers[: len(packed)] = packed

    def _write(self, table: Table, address: int, set_values: list) -> ExcCodes | None:
        end = address + len(set_values)
        if address < 0 or end > self.map.size[table]:
            return ExcCodes.ILLEGAL_ADDRESS
        if any(addr not in self.map.at[table] for addr in range(address, end)):
            return ExcCodes.ILLEGAL_ADDRESS  # a hole between allocated tags (ADR-0004)
        if table == "co":
            updates = {
                self.map.at["co"][address + i].tag: bool(v) for i, v in enumerate(set_values)
            }
        else:
            updates = {}
            touched = {self.map.at["hr"][addr].tag for addr in range(address, end)}
            for name in touched:
                a = self.map.by_tag[name]
                tag = self.io.tag(name)
                words = encode(tag, float(tag.value), self.encoding)  # current value
                for i in range(a.width):  # merge the written words over it
                    if address <= a.address + i < end:
                        words[i] = int(set_values[a.address + i - address])
                try:
                    updates[name] = decode(tag, words, self.encoding)
                except ValueError:
                    return ExcCodes.ILLEGAL_VALUE
        self.io.set_many(updates, Writer.PLC)
        return None

    # -- lifecycle -------------------------------------------------------------------------------

    async def serve_forever(self) -> None:
        """Bind ``self.endpoint`` and serve until :meth:`shutdown`."""
        log.info("%s", self.map.describe())
        log.info("Modbus TCP listening on %s:%d", self.endpoint.bind, self.endpoint.port)
        self._server = ModbusTcpServer(
            self.device, address=(self.endpoint.bind, self.endpoint.port)
        )
        await self._server.serve_forever()

    async def shutdown(self) -> None:
        if self._server is not None:
            await self._server.shutdown()
            self._server = None

    async def wait_ready(self, timeout: float = 5.0) -> None:
        """Wait until the listening socket accepts connections."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            try:
                _, writer = await asyncio.open_connection(self.endpoint.bind, self.endpoint.port)
                writer.close()
                await writer.wait_closed()
                return
            except OSError:
                if loop.time() > deadline:
                    raise
                await asyncio.sleep(0.02)
