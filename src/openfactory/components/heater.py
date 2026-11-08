"""Heater (PLAN 3.2, ``heater``; ADR-0005 decision 8).

Behavior spec
-------------
An immersion heater in one ``tank``. While ``on`` (DO) it delivers ``power`` (AO, 0 to 100 %;
unbound: 100 %) of ``rated_kw`` into the tank's liquid, provided the level covers the element
(``min_level``): uncovered it adds no heat. The tank loses ``ua_w_per_k`` × (its temperature −
``ambient_c``) to ambient, the same heater's account. The tank is well mixed, so its temperature
rises at ``(P − UA·(T − Ta)) / (ρ·cp·V)``; inflows mix in at their own temperatures (the flow
network's exact heat balance). ``temp`` (AI, °C, 0 to 100) reads the tank's temperature. The power
set in a tick heats from the next, like any actuator. Its state reports ``on``, the power in kW
and whether the element is uncovered.

Faults: ``stuck_off`` (an open element: no heat whatever the command), ``drift`` and ``noisy``
(``temp``).

Geometry: the origin is the element's centre.
"""

from __future__ import annotations

from typing import Any

from openfactory.core.component import Component, register


@register("heater")
class Heater(Component):
    FAULTS = ("stuck_off", "drift", "noisy")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.tank: Any = None  # the tank component, found when the network is built
        self.on = False
        self.power_kw = 0.0
        self.uncovered = False

    def flow_built(self, by_id: dict[str, Any]) -> None:
        self.tank = by_id[self.config.tank]  # type: ignore[attr-defined]

    def step(self, dt: float) -> None:
        cfg = self.config
        node = self.tank.node
        self.on = bool(self.read("on", default=False))
        broken = self.fault is not None and self.fault.kind == "stuck_off"
        self.uncovered = node.level < cfg.min_level  # type: ignore[attr-defined]
        fraction = min(max(float(self.read("power", default=100.0)), 0.0), 100.0) / 100
        self.power_kw = (
            cfg.rated_kw * fraction if self.on and not broken and not self.uncovered else 0.0
        )  # type: ignore[attr-defined]
        loss = cfg.ua_w_per_k * (node.temperature - cfg.ambient_c) if node.volume > 0 else 0.0  # type: ignore[attr-defined]
        node.heat_sources[self.id] = self.power_kw * 1000 - loss
        self.write("temp", node.temperature)

    def state(self) -> dict[str, Any]:
        return {"on": self.on, "power_kw": self.power_kw, "uncovered": self.uncovered}

    def geometry(self) -> dict[str, Any]:
        return {**super().geometry(), "tank": self.config.tank}  # type: ignore[attr-defined]
