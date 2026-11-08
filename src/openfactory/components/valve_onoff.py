"""On/off valve (PLAN 3.2, ``valve_onoff``; ADR-0005).

Behavior spec
-------------
A valve that opens fully while ``open`` (DO) is held and closes fully when it is released, taking
``travel_s`` for a full stroke and reversing at once on a change mid-travel, like the pusher. It
is a link of the flow network from ``from`` to ``to`` whose head loss is set by its flow
coefficient (``cv``, US gpm at 1 psi, or ``kv``, m³/h at 1 bar; Kv = 0.865 Cv) and its opening:
``Q[m³/h] = Kv·x·√(Δp[bar])`` at opening ``x``, linear in between, so it throttles while it
travels and passes nothing shut. ``opened`` and ``closed`` are limit switches, both false
mid-travel. It starts closed. Its state reports the opening (0 to 1) and the flow, m³/h.

Faults: ``jammed`` (it stops where it is; the limits stay honest), ``slow`` (it travels at
``factor`` of its speed), ``stuck_on``/``stuck_off`` (the limit switches).

Geometry: the origin is the valve body; it is drawn joined to its ends.
"""

from __future__ import annotations

import math
from typing import Any

from openfactory.components._process import link_points
from openfactory.core import flownet
from openfactory.core.component import Component, register


@register("valve_onoff")
class ValveOnOff(Component):
    FAULTS = ("jammed", "slow", "stuck_on", "stuck_off")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        cfg = self.config
        self.kv = cfg.kv_value()  # type: ignore[attr-defined]
        self.opening = 0.0
        self.link = flownet.Resistance(
            self.id,
            cfg.from_,  # type: ignore[attr-defined]
            cfg.to,  # type: ignore[attr-defined]
            position=tuple(cfg.position),  # type: ignore[arg-type]
            r=math.inf,
        )
        self.points: list[list[float]] = []

    def flow_links(self) -> list[flownet.Link]:
        return [self.link]

    def flow_built(self, by_id: dict[str, Any]) -> None:
        self.points = link_points(by_id, self, [list(self.config.position)])

    def step(self, dt: float) -> None:
        target = 1.0 if self.read("open", default=False) else 0.0
        rate = self.speed_factor / self.config.travel_s  # type: ignore[attr-defined]
        if self.opening < target:
            self.opening = min(target, self.opening + rate * dt)
        elif self.opening > target:
            self.opening = max(target, self.opening - rate * dt)
        self.link.r = flownet.valve_resistance(self.kv, self.opening)
        self.write("opened", self.opening >= 1.0)
        self.write("closed", self.opening <= 0.0)

    def state(self) -> dict[str, Any]:
        return {"opening": self.opening, "flow_m3h": self.link.flow * 3600}

    def geometry(self) -> dict[str, Any]:
        return {**super().geometry(), "points": self.points, "kv": self.kv}
