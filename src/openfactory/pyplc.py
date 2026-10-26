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
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

from openfactory.core.iotable import IOTable, Value, Writer


class PlcIO:
    """The PLC's view of the I/O table: read any tag, write only DO/AO."""

    def __init__(self, table: IOTable) -> None:
        self._table = table

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
