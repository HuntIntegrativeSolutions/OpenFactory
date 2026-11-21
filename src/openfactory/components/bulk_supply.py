"""Bulk supply (PLAN 5.7a, ``bulk_supply``; ADR-0006).

Behavior spec
-------------
A node of the solids network: material arriving from off the drawing (a truck, a line before
this one). While ``enable`` (DO) is on (unbound: always) it gives up to ``capacity_kg_s`` a second
(unset: as much as the links under it take), of its ``material``; it is never exhausted. A link
under it (a slide gate, an airlock, a screw) takes what it gives. Its state reports what it gave
this tick, kg/s, and in the run, kg.

Faults: none.

Geometry: the origin is where it gives.
"""

from __future__ import annotations

from typing import Any

from openfactory.core import solidsnet
from openfactory.core.component import Component, num, register


@register("bulk_supply")
class BulkSupply(Component):
    FAULTS = ()

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.node = solidsnet.Supply(self.id, capacity=self.config.capacity_kg_s)  # type: ignore[attr-defined]
        self.dt = 0.0

    def solids_nodes(self) -> list[solidsnet.Node]:
        return [self.node]

    def step(self, dt: float) -> None:
        self.dt = dt
        self.node.enabled = bool(self.read("enable", default=True))

    def state(self) -> dict[str, Any]:
        rate = self.node.given / self.dt if self.dt > 0 else 0.0
        return {"enabled": self.node.enabled, "rate_kg_s": rate, "total_kg": self.node.total}

    @classmethod
    def describe(cls, state: dict[str, Any], geometry: dict[str, Any]) -> str:
        """One sentence for ``openfactory look`` (PLAN 5.14b; :meth:`Component.describe`)."""
        on = "enabled" if state["enabled"] else "disabled"
        return f"{on}, giving {num(state['rate_kg_s'])} kg/s, {num(state['total_kg'], 1)} kg so far"

    def geometry(self) -> dict[str, Any]:
        return {**super().geometry(), "material": self.config.material}  # type: ignore[attr-defined]
