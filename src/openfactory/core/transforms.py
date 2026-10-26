"""Binding-level signal transforms (PLAN 2.17): conditioning between a component and its tag.

Behavior spec
-------------
A DI binding may condition the component's raw output before the PLC sees it:

- ``combine: or | and`` with ``from: [A.port, B.port, ...]``: the tag follows the OR / AND of the
  listed DI ports' raw values. The binding's own port must be one of them. The listed ports keep
  their own bindings, if they have any.
- ``invert: true``: a normally closed contact. The PLC sees the opposite state.
- ``on_delay_s`` / ``off_delay_s``: the tag turns on only after the conditioned signal has been on
  for ``on_delay_s``, and off only after it has been off for ``off_delay_s``. Each is counted from
  the first tick that sees the change, so a delay of n·dt changes the tag n ticks later. A pulse
  shorter than the delay never reaches the PLC.
- on an AI port, ``window: [lo, hi]`` binds a ``_DI`` tag that is true while the value is inside
  the window (a distance sensor's switching window).

Order: combine or window, then invert, then delays.

On a DO binding, ``invert: true`` makes the component act on the opposite of what the PLC wrote
(:meth:`openfactory.core.component.Component.read`).

Where it runs: components always record their raw port values (``Component.raw``). A port whose
binding has a DI transform is not written to its tag directly. :class:`TransformStage` runs after
every component has stepped (:meth:`openfactory.core.loader.LoadedScene.step`) and writes each
conditioned tag as the simulation, in the same tick. So Modbus, the viewer, a ``--script`` PLC, and
every later protocol see one result. :meth:`TransformStage.prime` settles every tag from the
components' initial raw values (False / 0) at load, so an inverted photo eye reads true before the
first tick.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from openfactory.core.iotable import IOTable, Writer
from openfactory.core.schema import Transform

if TYPE_CHECKING:
    from openfactory.core.component import Component

_EPS = 1e-9


@dataclass
class _Signal:
    tag: str
    transform: Transform
    sources: list[tuple[Component, str]]
    state: bool = False
    pending_s: float | None = None  # how long the input has differed from `state`

    def conditioned(self) -> bool:
        raws = [comp.raw.get(port, False) for comp, port in self.sources]
        t = self.transform
        if t.window is not None:
            value = float(raws[0])
            on = t.window[0] - _EPS <= value <= t.window[1] + _EPS
        elif t.combine == "and":
            on = all(bool(v) for v in raws)
        else:  # "or", or a single source
            on = any(bool(v) for v in raws)
        return on != t.invert

    def step(self, dt: float) -> bool:
        target = self.conditioned()
        if target == self.state:
            self.pending_s = None
            return self.state
        delay = self.transform.on_delay_s if target else self.transform.off_delay_s
        self.pending_s = 0.0 if self.pending_s is None else self.pending_s + dt
        if self.pending_s >= delay - _EPS:
            self.state, self.pending_s = target, None
        return self.state


class TransformStage:
    """Writes every transformed DI tag from its components' raw values, once per tick."""

    def __init__(self, io: IOTable, signals: list[_Signal]) -> None:
        self.io = io
        self.signals = signals

    @classmethod
    def build(cls, io: IOTable, components: list[Component], bindings) -> TransformStage:
        by_id = {c.id: c for c in components}
        signals = []
        for b in bindings:
            t = b.transform
            if b.kind != "DI" or not t:
                continue
            refs = t.sources or (f"{b.component_id}.{b.port.name}",)
            sources = [(by_id[ref.split(".")[0]], ref.split(".")[1]) for ref in refs]
            signals.append(_Signal(b.name, t, sources))
        return cls(io, signals)

    def prime(self) -> None:
        """Settle every tag from the initial raw values, ignoring delays (load time)."""
        for s in self.signals:
            s.state = s.conditioned()
            self.io.set(s.tag, s.state, Writer.SIM)

    def step(self, dt: float) -> None:
        for s in self.signals:
            self.io.set(s.tag, s.step(dt), Writer.SIM)
