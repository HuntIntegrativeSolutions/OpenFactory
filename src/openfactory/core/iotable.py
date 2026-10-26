"""I/O table: the named tags shared by the simulation and the protocol servers (PLAN §3.1).

Behavior spec
-------------
A :class:`Tag` has a name, a kind (DI/DO/AI/AO), a dtype, a value, a description, and the id of the
component that owns it. Each kind has exactly one writer side:

- DI and AI are written by the simulation (:attr:`Writer.SIM`) and read by the PLC.
- DO and AO are written by the PLC (:attr:`Writer.PLC`) and read by the simulation.

A write from the wrong side raises :class:`TagWriteError`, so a protocol server cannot overwrite a
sensor and a component cannot fake a PLC command. Values are coerced to the tag's dtype on write.

The table is thread-safe: protocol servers run on their own event loop or thread while the scheduler
steps components. Change callbacks fire only when a value actually changes, after the lock is
released (so a callback may read or write the table), in registration order. A callback that raises
is logged and does not stop the others or the writer. Callbacks from writes on different threads may
interleave; each carries the old and new value so a subscriber can tell what changed.

Not a safety-rated simulation. OpenFactory is a training and demo tool.
"""

from __future__ import annotations

import logging
import math
import threading
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, replace
from enum import Enum
from typing import Literal

log = logging.getLogger(__name__)

Kind = Literal["DI", "DO", "AI", "AO"]
DType = Literal["bool", "int16", "float32"]
Value = bool | int | float


class Writer(Enum):
    """Which side is writing a tag."""

    SIM = "sim"
    PLC = "plc"


#: The only side allowed to write each kind.
WRITER_FOR_KIND: dict[str, Writer] = {
    "DI": Writer.SIM,
    "AI": Writer.SIM,
    "DO": Writer.PLC,
    "AO": Writer.PLC,
}
DEFAULT_DTYPE: dict[str, DType] = {"DI": "bool", "DO": "bool", "AI": "float32", "AO": "float32"}


class TagWriteError(PermissionError):
    """A tag was written by the side that does not own its kind."""


@dataclass(frozen=True)
class Tag:
    """Snapshot of one I/O point. The table hands out copies; write through :meth:`IOTable.set`."""

    name: str
    kind: Kind
    dtype: DType
    value: Value
    description: str = ""
    owner: str = ""
    range: tuple[float, float] | None = None
    units: str | None = None

    @classmethod
    def new(
        cls,
        name: str,
        kind: Kind,
        *,
        dtype: DType | None = None,
        description: str = "",
        owner: str = "",
        range: tuple[float, float] | None = None,  # noqa: A002
        units: str | None = None,
        value: Value = 0,
    ) -> Tag:
        """Create a tag with the kind's default dtype, starting at ``value`` (zero/False)."""
        if kind not in WRITER_FOR_KIND:
            raise ValueError(f"{name}: unknown kind {kind!r}")
        dt = dtype or DEFAULT_DTYPE[kind]
        return cls(name, kind, dt, _coerce(name, dt, value), description, owner, range, units)


#: Callback signature: (tag name, old value, new value, writer).
ChangeCallback = Callable[[str, Value, Value, Writer], None]


def _coerce(name: str, dtype: DType, value: object) -> Value:
    if dtype == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and value in (0, 1):
            return bool(value)
        raise TypeError(f"{name}: bool tag needs True/False or 0/1, got {value!r}")
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{name}: {dtype} tag needs a number, got {value!r}")
    if dtype == "int16":
        if not float(value).is_integer() or not -32768 <= value <= 32767:
            raise ValueError(f"{name}: int16 value out of range: {value!r}")
        return int(value)
    out = float(value)
    if math.isnan(out):
        raise ValueError(f"{name}: NaN is not a valid tag value")
    return out


class IOTable:
    """Thread-safe table of tags with kind-based write restriction and change callbacks."""

    def __init__(self, tags: list[Tag] | None = None) -> None:
        self._lock = threading.RLock()
        self._tags: dict[str, Tag] = {}
        self._callbacks: list[ChangeCallback] = []
        for tag in tags or ():
            self.add(tag)

    # -- structure -----------------------------------------------------------------------------

    def add(self, tag: Tag) -> None:
        """Add a tag. Names are globally unique."""
        with self._lock:
            if tag.name in self._tags:
                raise KeyError(f"duplicate tag {tag.name!r}")
            self._tags[tag.name] = replace(tag, value=_coerce(tag.name, tag.dtype, tag.value))

    def __contains__(self, name: object) -> bool:
        with self._lock:
            return name in self._tags

    def __len__(self) -> int:
        with self._lock:
            return len(self._tags)

    def __iter__(self) -> Iterator[Tag]:
        return iter(self.tags())

    def tags(self) -> list[Tag]:
        """Snapshot of all tags in insertion order."""
        with self._lock:
            return list(self._tags.values())

    def tag(self, name: str) -> Tag:
        """Snapshot of one tag. Raises ``KeyError`` for an unknown name."""
        with self._lock:
            return self._tags[name]

    def get(self, name: str) -> Value:
        """Current value of a tag."""
        with self._lock:
            return self._tags[name].value

    def values(self) -> dict[str, Value]:
        """Consistent snapshot of every tag value."""
        with self._lock:
            return {name: tag.value for name, tag in self._tags.items()}

    # -- writes --------------------------------------------------------------------------------

    def set(self, name: str, value: object, writer: Writer) -> bool:
        """Write one tag. Returns True when the value changed."""
        return bool(self.set_many({name: value}, writer))

    def set_many(self, values: Mapping[str, object], writer: Writer) -> list[str]:
        """Write several tags atomically (all or none). Returns the names whose value changed.

        Every write is checked before any is applied, so a protocol batch write (FC15/FC16) either
        lands completely or raises without side effects.
        """
        changes: list[tuple[str, Value, Value]] = []
        with self._lock:
            staged: dict[str, Value] = {}
            for name, raw in values.items():
                tag = self._tags.get(name)
                if tag is None:
                    raise KeyError(f"unknown tag {name!r}")
                if WRITER_FOR_KIND[tag.kind] is not writer:
                    raise TagWriteError(
                        f"{writer.value} cannot write {tag.kind} tag {name!r}; "
                        f"only {WRITER_FOR_KIND[tag.kind].value} writes {tag.kind}"
                    )
                staged[name] = _coerce(name, tag.dtype, raw)
            for name, new in staged.items():
                old = self._tags[name].value
                if new != old or type(new) is not type(old):
                    self._tags[name] = replace(self._tags[name], value=new)
                    changes.append((name, old, new))
            callbacks = list(self._callbacks)
        for name, old, new in changes:
            for callback in callbacks:
                try:
                    callback(name, old, new, writer)
                except Exception:
                    log.exception("I/O table change callback failed for %s", name)
        return [name for name, _, _ in changes]

    # -- callbacks -----------------------------------------------------------------------------

    def on_change(self, callback: ChangeCallback) -> Callable[[], None]:
        """Register a change callback. Returns a function that unregisters it."""
        with self._lock:
            self._callbacks.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                if callback in self._callbacks:
                    self._callbacks.remove(callback)

        return unsubscribe
