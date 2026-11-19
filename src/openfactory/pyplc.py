"""Scripted stand-in PLC (PLAN 2.10): run a scene with control logic written in Python.

Behavior spec
-------------
``openfactory run SCENE --script path.py`` loads ``path.py`` as a module and calls its
``step(io, t)`` once per simulation tick, before the tick, the way a PLC scans its program
between I/O updates. ``t`` is the simulated time in seconds (never the wall clock), so a scripted
run is deterministic and works with ``--as-fast-as-possible``.

``io`` is a :class:`PlcIO`, the PLC's side of the I/O table:

- ``io["PE_01_DI"]`` reads any tag (DI and AI from the simulation, DO and AO as last written);
- ``io["CONV_01_RUN_DO"] = True`` writes a DO or AO tag as the PLC
  (:attr:`~openfactory.core.iotable.Writer.PLC`). Writing a DI or AI raises
  :class:`~openfactory.core.iotable.TagWriteError`, exactly as a write from a protocol server
  would, and an unknown tag raises ``KeyError`` naming it.

**Watch values (PLAN 5.14i).** ``io.watch("step", 20)`` publishes a piece of the program's own
state (the step of a sequence, a count) for people and assistants to see: ``openfactory look``
lists them, a recording's frames hold them (``"watch"``), ``openfactory explain`` says when they
changed, and the viewer shows them on its status lines with ``labels=tags``. They are not tags:
no address, no export, no protocol sees them, and the lockfiles do not move. They are data: a
name matches ``[A-Za-z_][A-Za-z0-9_.]{0,39}`` (else ValueError; it is never a path or a tag
name), at most :data:`MAX_WATCHES` names, and a value is kept as a bool, a whole number, a
finite float or ``None``; anything else (a non-finite float, a string, an object) as text, with
control characters dropped (newline and tab as a space) and cut to :data:`MAX_WATCH_VALUE`
characters. A script that publishes none runs exactly as before.

A script may define ``init(io)``, called once before the first tick. If ``step`` or ``init``
raises, the run stops and ``openfactory run`` exits 1 with the traceback: a faulted program, not a
silently stuck plant.

:class:`TON` and :class:`R_TRIG` mirror the IEC 61131-3 function blocks of the same names, timed
on ``t``, so a Structured Text program ports line for line (``examples/pyplc/``).

A script is ordinary Python and runs with your permissions. Run only scripts you trust, as with any
program. Not a safety-rated simulation; OpenFactory is a training and demo tool.
"""

from __future__ import annotations

import importlib.util
import math
import re
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

from openfactory.core.iotable import IOTable, Value, Writer

#: The most watch values one script may publish (PLAN 5.14i).
MAX_WATCHES = 32
#: The longest watch value kept as text, characters.
MAX_WATCH_VALUE = 80
#: A watch value's name: never a path, never a tag name's form only.
WATCH_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{0,39}")

WatchValue = bool | int | float | str | None


def watch_value(value: object) -> WatchValue:
    """A watch value as it is kept (see the module docstring)."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int) and abs(value) < 10**15:
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    try:
        text = value if isinstance(value, str) else str(value)
    except Exception:  # an object whose str() fails is still shown
        text = f"<{type(value).__name__}>"
    text = "".join(
        " " if c in "\n\t" else c for c in text if c in "\n\t" or (ord(c) >= 32 and ord(c) != 127)
    )
    return text[:MAX_WATCH_VALUE]


class PlcIO:
    """The PLC's view of the I/O table: read any tag, write only DO/AO; and watch values."""

    def __init__(self, table: IOTable) -> None:
        self._table = table
        #: The watch values published so far, in the order first published (PLAN 5.14i).
        self.watches: dict[str, WatchValue] = {}

    def watch(self, name: str, value: object) -> None:
        """Publish one piece of the program's own state (PLAN 5.14i; see the module docstring)."""
        if not isinstance(name, str) or not WATCH_NAME.fullmatch(name):
            raise ValueError(
                f"watch name {name!r}: letters, digits, '_' and '.', starting with a letter or "
                "'_', at most 40 characters"
            )
        if name not in self.watches and len(self.watches) >= MAX_WATCHES:
            raise ValueError(f"a script may publish at most {MAX_WATCHES} watch values")
        self.watches[name] = watch_value(value)

    def __getitem__(self, tag: str) -> Value:
        if tag not in self._table:
            raise KeyError(f"no tag {tag!r} in this scene")
        return self._table.get(tag)

    def __setitem__(self, tag: str, value: Value) -> None:
        if tag not in self._table:
            raise KeyError(f"no tag {tag!r} in this scene")
        self._table.set(tag, value, Writer.PLC)

    def __contains__(self, tag: object) -> bool:
        return tag in self._table


class TON:
    """On-delay timer: ``Q`` turns on when ``IN`` has been on for ``PT`` seconds (IEC 61131-3)."""

    def __init__(self, pt_s: float) -> None:
        self.pt_s = pt_s
        self.q = False
        self.et_s = 0.0
        self._since: float | None = None

    def __call__(self, in_: bool, t: float) -> bool:
        if not in_:
            self._since, self.q, self.et_s = None, False, 0.0
            return False
        if self._since is None:
            self._since = t
        self.et_s = min(t - self._since, self.pt_s)
        self.q = t - self._since >= self.pt_s - 1e-9
        return self.q


class R_TRIG:  # noqa: N801 - the IEC 61131-3 name
    """Rising-edge detector: ``Q`` is on for the one scan where ``CLK`` goes from off to on."""

    def __init__(self) -> None:
        self._last = False
        self.q = False

    def __call__(self, clk: bool) -> bool:
        self.q = bool(clk) and not self._last
        self._last = bool(clk)
        return self.q


class ScriptPlc:
    """A loaded ``--script`` file, scanned once per tick by the runtime."""

    def __init__(self, path: str | Path, table: IOTable) -> None:
        self.path = Path(path)
        self.module = load_script(self.path)
        step = getattr(self.module, "step", None)
        if not callable(step):
            raise ValueError(f"{self.path}: a --script must define step(io, t)")
        self._step: Callable[[PlcIO, float], Any] = step
        self.io = PlcIO(table)
        self.scans = 0
        init = getattr(self.module, "init", None)
        if callable(init):
            init(self.io)

    def scan(self, t: float) -> None:
        self._step(self.io, t)
        self.scans += 1


def load_script(path: Path) -> ModuleType:
    if not path.is_file():
        raise FileNotFoundError(f"script not found: {path}")
    name = f"openfactory_script_{path.stem.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"{path}: not a Python file")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses and pickling inside the script need this
    spec.loader.exec_module(module)
    return module
