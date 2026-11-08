"""Supply (PLAN 3.2, ``supply``; ADR-0005 decision 2).

Behavior spec
-------------
A source of liquid at a fixed gauge pressure: a node of known head in the flow network
(``core/flownet.py``), ``pressure_bar`` above atmospheric at the origin's height, delivering
whatever the network draws at ``temperature_c`` as the ingredient ``ingredient`` (for mixing).
It is never exhausted. It has no ports: a valve between it and the plant is the control element.
Its state reports the flow it delivers, m³/h.

Geometry: the origin is the connection point.
"""

from __future__ import annotations

from typing import Any

from openfactory.core import flownet
from openfactory.core.component import Component, register


@register("supply")
class Supply(Component):
    FAULTS = ()

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        cfg = self.config
        self.node = flownet.FixedHead(
            self.id,
            cfg.position[1],
            pressure_bar=cfg.pressure_bar,  # type: ignore[attr-defined]
            temperature=cfg.temperature_c,  # type: ignore[attr-defined]
            fractions={cfg.ingredient: 1.0},  # type: ignore[attr-defined]
        )
        self._delivered = 0.0
        self.flow_m3h = 0.0

    def flow_nodes(self) -> list[flownet.FixedHead]:
        return [self.node]

    def step(self, dt: float) -> None:
        net = self.node.delivered - self.node.received
        self.flow_m3h = (net - self._delivered) / dt * 3600 if dt > 0 else 0.0
        self._delivered = net

    def state(self) -> dict[str, Any]:
        return {"flow_m3h": self.flow_m3h}

    def geometry(self) -> dict[str, Any]:
        return {**super().geometry(), "pressure_bar": self.config.pressure_bar}  # type: ignore[attr-defined]
