"""Rotary airlock (PLAN 5.7a, ``rotary_airlock``; ADR-0006).

Behavior spec
-------------
A rotary valve under a bin's (or a supply's) outlet: a feeder link of the solids network
(``core/solidsnet.py``) from ``from`` to ``to``. While ``run`` (DO) is on it turns at ``speed``
(AO, 0 to 100 %; unbound: 100 %) and moves up to ``capacity_kg_s`` × speed a second, what its
``from`` has and what its ``to`` has room for, arriving in the same tick (ADR-0006 decision 3).
``running`` (DI) follows ``run`` (a contactor's auxiliary contact). ``plugged`` (DI) reads true
while its outlet cannot take what it brings: the bin below is full, the screw below is blocked
or full at its inlet, or the chute is plugged (the fault). It reads this tick's network, and
its speed reaches the network on the next tick, as every actuator does.

Faults: ``trip`` (its motor trips: it stops and ``running`` goes false while ``run`` is on, until
the fault is cleared), ``plugged`` (its outlet takes nothing), ``stuck_on``/``stuck_off``
(``running`` and ``plugged``).

Geometry: the origin is the valve's centre, under its bin's outlet; it is drawn with chutes to
its two ends.
"""

from __future__ import annotations

from typing import Any

from openfactory.components._bulk import chute, speed_fraction
from openfactory.core import solidsnet
from openfactory.core.component import Component, num, register


@register("rotary_airlock")
class RotaryAirlock(Component):
    FAULTS = ("trip", "plugged", "stuck_on", "stuck_off")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        cfg = self.config
        self.link = solidsnet.Feeder(self.id, cfg.from_, cfg.to, capacity=cfg.capacity_kg_s)  # type: ignore[attr-defined]
        self.running = False
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
        self.link.speed = speed_fraction(self) if self.running else 0.0
        self.link.plugged = kind == "plugged"
        self.write("running", self.running)
        self.write("plugged", self.link.plugged or self.link.blocked)

    def state(self) -> dict[str, Any]:
        return {"running": self.running, "rate_kg_s": self.link.rate(self.dt),
                "plugged": self.link.plugged or self.link.blocked}  # fmt: skip

    @classmethod
    def describe(cls, state: dict[str, Any], geometry: dict[str, Any]) -> str:
        """One sentence for ``openfactory look`` (PLAN 5.14b; :meth:`Component.describe`)."""
        run = "running" if state["running"] else "stopped"
        plug = ", its outlet blocked" if state["plugged"] else ""
        return f"{run}, {num(state['rate_kg_s'])} kg/s{plug}"

    def geometry(self) -> dict[str, Any]:
        cfg = self.config
        return {**super().geometry(), "size": cfg.size, "points": self.points,  # type: ignore[attr-defined]
                "from": cfg.from_, "to": cfg.to}  # type: ignore[attr-defined]  # fmt: skip
