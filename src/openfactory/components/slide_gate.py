"""Slide gate (PLAN 5.7a, ``slide_gate``; ADR-0006).

Behavior spec
-------------
A gate under a bin's (or a supply's) outlet: a feeder link of the solids network
(``core/solidsnet.py``) from ``from`` to ``to``. It opens while ``open`` (DO) is held and closes
when it is released, taking ``travel_s`` for a full stroke and reversing at once on a change
mid-travel, as an on/off valve does. At opening ``x`` (0 to 1) it passes up to ``capacity_kg_s`` ×
``x`` a second, what its ``from`` has and what its ``to`` has room for, arriving in the same tick;
shut, nothing. ``opened`` and ``closed`` are limit switches, both false mid-travel; ``plugged``
reads true while its outlet cannot take what it brings. It starts closed.

Faults: ``jammed`` (it stops where it is; the limits stay honest), ``plugged`` (its outlet takes
nothing), ``stuck_on``/``stuck_off`` (the DIs).

Geometry: the origin is the gate's centre; it is drawn with chutes to its two ends.
"""

from __future__ import annotations

from typing import Any

from openfactory.components._bulk import chute
from openfactory.core import solidsnet
from openfactory.core.component import Component, num, register


@register("slide_gate")
class SlideGate(Component):
    FAULTS = ("jammed", "plugged", "stuck_on", "stuck_off")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        cfg = self.config
        self.link = solidsnet.Feeder(self.id, cfg.from_, cfg.to, capacity=cfg.capacity_kg_s)  # type: ignore[attr-defined]
        self.opening = 0.0
        self.dt = 0.0
        self.points: list[list[float]] = []

    def solids_links(self) -> list[solidsnet.Link]:
        return [self.link]

    def solids_built(self, by_id: dict[str, Any]) -> None:
        self.points = chute(by_id, self)

    def step(self, dt: float) -> None:
        self.dt = dt
        target = 1.0 if self.read("open", default=False) else 0.0
        rate = self.speed_factor / self.config.travel_s  # type: ignore[attr-defined]
        if self.opening < target:
            self.opening = min(target, self.opening + rate * dt)
        elif self.opening > target:
            self.opening = max(target, self.opening - rate * dt)
        self.link.speed = self.opening
        self.link.plugged = self.fault is not None and self.fault.kind == "plugged"
        self.write("opened", self.opening >= 1.0)
        self.write("closed", self.opening <= 0.0)
        self.write("plugged", self.link.plugged or self.link.blocked)

    def state(self) -> dict[str, Any]:
        return {"opening": self.opening, "rate_kg_s": self.link.rate(self.dt),
                "plugged": self.link.plugged or self.link.blocked}  # fmt: skip

    @classmethod
    def describe(cls, state: dict[str, Any], geometry: dict[str, Any]) -> str:
        """One sentence for ``openfactory look`` (PLAN 5.14b; :meth:`Component.describe`)."""
        opening = float(state["opening"])
        shut = "closed" if opening <= 1e-6 else "open" if opening >= 1 - 1e-6 else None
        where = shut or f"{num(opening * 100, 0)} % open"
        plug = ", its outlet blocked" if state["plugged"] else ""
        return f"{where}, {num(state['rate_kg_s'])} kg/s{plug}"

    def geometry(self) -> dict[str, Any]:
        cfg = self.config
        return {**super().geometry(), "size": cfg.size, "points": self.points,  # type: ignore[attr-defined]
                "from": cfg.from_, "to": cfg.to}  # type: ignore[attr-defined]  # fmt: skip
