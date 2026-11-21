"""Bulk discharge (PLAN 5.7a, ``bulk_discharge``; ADR-0006).

Behavior spec
-------------
A node of the solids network: where material leaves the drawing (a mixer, a mill, a bagger off
the drawing). It takes up to ``demand_kg_s`` a second (unset: everything that arrives); a link
into it that brings more is blocked. Its state reports what it took this tick, kg/s, and in the
run, kg.

Faults: none.

Geometry: the origin is where it takes.
"""

from __future__ import annotations

from typing import Any

from openfactory.core import solidsnet
from openfactory.core.component import Component, num, register


@register("bulk_discharge")
class BulkDischarge(Component):
    FAULTS = ()

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.node = solidsnet.Discharge(self.id, demand=self.config.demand_kg_s)  # type: ignore[attr-defined]
        self.dt = 0.0

    def solids_nodes(self) -> list[solidsnet.Node]:
        return [self.node]

    def step(self, dt: float) -> None:
        self.dt = dt

    def state(self) -> dict[str, Any]:
        rate = self.node.taken / self.dt if self.dt > 0 else 0.0
        return {"rate_kg_s": rate, "total_kg": self.node.total}

    @classmethod
    def describe(cls, state: dict[str, Any], geometry: dict[str, Any]) -> str:
        """One sentence for ``openfactory look`` (PLAN 5.14b; :meth:`Component.describe`)."""
        return f"taking {num(state['rate_kg_s'])} kg/s, {num(state['total_kg'], 1)} kg so far"
