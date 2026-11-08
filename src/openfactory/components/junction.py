"""Junction (PLAN 3.2, ``junction``; ADR-0005 decision 2).

Behavior spec
-------------
A point where links of the flow network meet: a node whose head the solver finds each tick from
flow continuity. It holds no liquid. At least one link must connect to it (otherwise the scene
fails to load). Its pressure, gauge, in bar, is its head above its height: read it with a
``transmitter`` (``measures: J_01.pressure``); below a node it still connects to, it can be
negative (a column hanging from a closed valve). A junction cut off from every node of known head
(every valve to it shut) carries no flow and has no pressure (reported as 0). Links may also be
joined end to end without one (``to: FV_01``), which makes an implicit junction at the second
link's position.

Geometry: the origin is the junction point.
"""

from __future__ import annotations

from typing import Any

from openfactory.core import flownet
from openfactory.core.component import Component, register


@register("junction")
class Junction(Component):
    FAULTS = ()

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.node = flownet.Junction(self.id, self.config.position[1])

    def flow_nodes(self) -> list[flownet.Junction]:
        return [self.node]

    def step(self, dt: float) -> None:
        pass

    def state(self) -> dict[str, Any]:
        return {"pressure_bar": self.node.pressure_bar}
