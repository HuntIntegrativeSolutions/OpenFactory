"""Screw conveyor (PLAN 5.7a, ``screw_conveyor``; ADR-0006).

Behavior spec
-------------
A screw feeder or conveyor: a carrying link of the solids network (``core/solidsnet.py``) from
``from`` (a bin or a supply it draws from, or a feeder that drops into its inlet) to ``to`` (a
bin, a discharge, or another screw). While ``run`` (DO) is on it turns at ``speed`` (AO, 0 to
100 %; unbound: 100 %). Material entering its inlet reaches its outlet ``travel_s`` later at full
speed (``travel_s`` / speed at part speed): plug flow, a ring of one slot a tick, each slot
holding at most ``capacity_kg_s`` × dt, so the screw holds at most ``capacity_kg_s`` ×
``travel_s``. Drawing from a bin it takes up to ``capacity_kg_s`` × speed a second. Stopped, it
holds what is on it, and a feeder above it can fill its inlet slot and no more.

**Blocked** (ADR-0006 decision 3): when its outlet cannot take a whole slot (the bin below is
full, or the chute is plugged), it delivers what fits, keeps the rest, stops conveying and takes
nothing in; it never drops or loses material. It starts again when there is room.

Ports: ``running`` (DI) follows ``run``; ``motion`` (DI), a zero-speed switch, is on while the
screw turns; ``plugged`` (DI) reads true while it is blocked.

Faults: ``trip`` (its motor trips: it stops, ``running`` and ``motion`` go false while ``run`` is
on, until cleared), ``jammed`` (the screw stops turning: ``running`` follows ``run``, ``motion``
goes false), ``plugged`` (its outlet takes nothing), ``stuck_on``/``stuck_off`` (the DIs).

Geometry: the origin is the centre of the inlet end; it runs ``length`` along local +x, rising
``incline_deg``.
"""

from __future__ import annotations

from typing import Any

from openfactory.components._bulk import chute, speed_fraction
from openfactory.core import solidsnet
from openfactory.core.component import Component, num, register

#: Segments the viewer draws the screw's load in (its ring averaged).
SEGMENTS = 16


@register("screw_conveyor")
class ScrewConveyor(Component):
    FAULTS = ("trip", "jammed", "plugged", "stuck_on", "stuck_off")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        cfg = self.config
        self.link = solidsnet.Carrier(self.id, cfg.from_, cfg.to,  # type: ignore[attr-defined]
                                      capacity=cfg.capacity_kg_s, travel_s=cfg.travel_s)  # type: ignore[attr-defined]  # fmt: skip
        self.running = False
        self.turning = False
        self.dt = 0.0
        self.points: list[list[float]] = []

    def solids_links(self) -> list[solidsnet.Link]:
        return [self.link]

    def solids_built(self, by_id: dict[str, Any]) -> None:
        self.points = chute(by_id, self)

    def step(self, dt: float) -> None:
        self.dt = dt
        kind = self.fault.kind if self.fault is not None else None
        run = bool(self.read("run", default=False))
        self.running = run and kind != "trip"
        self.turning = self.running and kind != "jammed"
        self.link.speed = speed_fraction(self) if self.turning else 0.0
        self.link.plugged = kind == "plugged"
        self.write("running", self.running)
        self.write("motion", self.turning and self.link.speed > 0)
        self.write("plugged", self.link.plugged or self.link.blocked)

    def load(self) -> list[float]:
        """The fill along it, 0 to 1, inlet first, in :data:`SEGMENTS` segments."""
        fill = self.link.fill()
        if not fill:
            return [0.0] * SEGMENTS
        n = len(fill)
        out = []
        for k in range(SEGMENTS):
            a, b = k * n // SEGMENTS, max((k + 1) * n // SEGMENTS, k * n // SEGMENTS + 1)
            part = fill[a : min(b, n)] or [0.0]
            out.append(round(sum(part) / len(part), 4))
        return out

    def state(self) -> dict[str, Any]:
        return {"running": self.running, "turning": self.turning,
                "rate_kg_s": self.link.rate(self.dt), "load_kg": self.link.load,
                "load": self.load(), "plugged": self.link.plugged or self.link.blocked}  # fmt: skip

    @classmethod
    def describe(cls, state: dict[str, Any], geometry: dict[str, Any]) -> str:
        """One sentence for ``openfactory look`` (PLAN 5.14b; :meth:`Component.describe`)."""
        run = "turning" if state["turning"] else "running, not turning" if state["running"] else (
            "stopped")  # fmt: skip
        plug = ", blocked at its outlet" if state["plugged"] else ""
        return (
            f"{run}, {num(state['load_kg'], 1)} kg on it, {num(state['rate_kg_s'])} kg/s out{plug}"
        )

    def geometry(self) -> dict[str, Any]:
        cfg = self.config
        return {**super().geometry(), "length": cfg.length, "diameter": cfg.diameter,  # type: ignore[attr-defined]
                "incline_deg": cfg.incline_deg, "points": self.points,  # type: ignore[attr-defined]
                "from": cfg.from_, "to": cfg.to}  # type: ignore[attr-defined]  # fmt: skip
