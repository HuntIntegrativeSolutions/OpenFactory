"""Modbus TCP server bound live to the I/O table (PLAN §6.1, task 1.7).

Behavior spec
-------------
**Register map.** :func:`allocate` assigns every tag in the I/O table to one Modbus table, sorted by
kind and then by tag name, packed from address 0 with no gaps:

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

**Live binding.** The server is one pymodbus ``SimDevice`` (id 0 answers every unit id) with four
separate tables and an ``action`` hook (``docs/research/libraries.md``, "pymodbus 3.x datastore
binding"; verified against the pymodbus 3.16.1 source). On every read the hook refills the whole
table from the I/O table, so the PLC always sees current values. On every write (FC05/06/15/16) it
decodes the request and writes the DO/AO tags as :attr:`Writer.PLC`, all or nothing. The hook is
idempotent (pymodbus can call it twice for one FC05) and never raises: an address outside the
allocation returns ILLEGAL ADDRESS (2), a NaN or infinite float returns ILLEGAL DATA VALUE (3), and
any other failure is logged and returns SERVER DEVICE FAILURE (4). A write to part of a float32 pair
(FC06, or FC16 starting mid-pair) is merged with the tag's current other word.

Bit reads past the last allocated bit but inside its final 16-bit word return False (pymodbus hands
the hook a word count for bit reads); bit writes past the allocation are rejected. Tables with no
tags still answer at address 0 only with ILLEGAL ADDRESS.

The server always binds the address it is given; callers resolve it with
:func:`openfactory.core.config.resolve_endpoint` (default ``127.0.0.1:5020``).
"""

from __future__ import annotations

import asyncio
import logging
import math
import struct
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal

from pymodbus.constants import ExcCodes
from pymodbus.server import ModbusTcpServer
from pymodbus.simulator import DataType, SimData, SimDevice
from pymodbus.simulator.simutils import SimUtils

from openfactory.core.config import Endpoint
from openfactory.core.iotable import IOTable, Tag, Writer

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


def allocate(tags: Iterable[Tag], encoding: Encoding) -> RegisterMap:
    """Sorted by kind (DI, DO, AI, AO), then tag name; packed from 0 in each table."""
    width = 2 if encoding == "float32" else 1
    next_free: dict[Table, int] = dict.fromkeys(("di", "co", "ir", "hr"), 0)
    out = []
    for tag in sorted(tags, key=lambda t: (KIND_ORDER.index(t.kind), t.name)):
        table = TABLE_FOR_KIND[tag.kind]
        w = width if tag.kind in ("AI", "AO") else 1
        out.append(Assignment(tag.name, tag.kind, table, next_free[table], w))
        next_free[table] += w
    return RegisterMap(encoding, out)


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

    def __init__(self, io: IOTable, encoding: Encoding, endpoint: Endpoint) -> None:
        self.io = io
        self.encoding = encoding
        self.endpoint = endpoint
        self.map = allocate(io.tags(), encoding)
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
