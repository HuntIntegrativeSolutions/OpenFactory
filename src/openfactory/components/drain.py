"""Drain (PLAN 3.2, ``drain``; ADR-0005 decisions 2 and 4).

Behavior spec
-------------
Where liquid leaves the plant. With no ``demand_m3h`` and no ``schedule`` it is an **open drain**:
a node of known head at the origin's height (atmospheric) that takes whatever arrives. With a
demand it is a **consumer**: a junction of the flow network that draws its demand,
pressure-dependently (EPANET's Wagner model): the full demand while its pressure head is at or
above ``required_head_m``, none at or below ``minimum_head_m``, and the square root of the
fraction between. So a consumer fed from a tank that runs dry slows to nothing instead of pulling
heads negative. ``schedule`` steps the demand at given simulated times (each step applies from its
time; before the first, ``demand_m3h`` or 0), the load change a level loop exercise needs;
``repeat`` starts it over at its last step's time. The demand set in a tick applies from the next
solve, like any actuator. Its state reports the flow it takes and, for a consumer, the current
demand, both m³/h.

Geometry: the origin is the connection point.
"""

from __future__ import annotations

from typing import Any

from openfactory.core import flownet
from openfactory.core.component import Component, register


@register("drain")
class Drain(Component):
    FAULTS = ()

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        cfg = self.config
        self.consumer = cfg.has_demand()  # type: ignore[attr-defined]
        self.node: flownet.FixedHead | flownet.Junction
        if self.consumer:
            self.node = flownet.Junction(
                self.id,
                cfg.position[1],
                demand=cfg.demand_at(0.0) / 3600,  # type: ignore[attr-defined]
                required_head=cfg.required_head_m,  # type: ignore[attr-defined]
                minimum_head=cfg.minimum_head_m,  # type: ignore[attr-defined]
            )
        else:
            self.node = flownet.FixedHead(self.id, cfg.position[1])
        self.time = 0.0
        self._taken = 0.0
        self.flow_m3h = 0.0

    def flow_nodes(self) -> list[flownet.FixedHead | flownet.Junction]:
        return [self.node]

    def step(self, dt: float) -> None:
        self.time += dt
        taken = self.node.received - getattr(self.node, "delivered", 0.0)
        self.flow_m3h = (taken - self._taken) / dt * 3600 if dt > 0 else 0.0
        self._taken = taken
        if self.consumer:
            self.node.demand = self.config.demand_at(self.time) / 3600  # type: ignore[attr-defined,union-attr]

    def state(self) -> dict[str, Any]:
        out: dict[str, Any] = {"flow_m3h": self.flow_m3h}
        if self.consumer:
            out["demand_m3h"] = self.node.demand * 3600  # type: ignore[union-attr]
        return out
