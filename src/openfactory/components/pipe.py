"""Pipe (PLAN 3.2, ``pipe``; ADR-0005).

Behavior spec
-------------
A run of pipe between its ``from`` and ``to`` ends: a link of the flow network whose head loss is
``r·q·|q|``, with ``r`` from Darcy-Weisbach at a fixed friction factor plus minor losses,
``(f·L/D + ΣK) / (2·g·A²)`` (``core/flownet.py``). ``length`` defaults to the drawn length: from
its ``from`` end through each ``path`` point to its ``to`` end, where a tank nozzle's end is on the
tank's wall (or the top's centre) and any other end is that component's origin. With
``check_valve: true`` it passes flow only from ``from`` to ``to``. No ports: a ``transmitter``
reads its flow (``measures: PIPE_01.flow``). Its state reports the flow, m³/h, positive from
``from`` to ``to``.

Geometry: the drawn points, scene coordinates; the origin places an implicit junction made with
another link downstream of it.
"""

from __future__ import annotations

import math
from typing import Any

from openfactory.components._process import link_points
from openfactory.core import flownet
from openfactory.core.component import Component, register


@register("pipe")
class Pipe(Component):
    FAULTS = ()

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        cfg = self.config
        self.link = flownet.Resistance(
            self.id,
            cfg.from_,  # type: ignore[attr-defined]
            cfg.to,  # type: ignore[attr-defined]
            position=tuple(cfg.position),  # type: ignore[arg-type]
            check_valve=cfg.check_valve,  # type: ignore[attr-defined]
        )
        self.points: list[list[float]] = []
        self.length = cfg.length or 1.0  # type: ignore[attr-defined]
        self._size()

    def _size(self) -> None:
        cfg = self.config
        self.link.r = flownet.pipe_resistance(
            self.length,
            cfg.diameter,  # type: ignore[attr-defined]
            cfg.friction,  # type: ignore[attr-defined]
            cfg.minor_loss,  # type: ignore[attr-defined]
        )

    def flow_links(self) -> list[flownet.Link]:
        return [self.link]

    def flow_built(self, by_id: dict[str, Any]) -> None:
        """Place the ends and, without a ``length``, size the pipe from its drawn length."""
        cfg = self.config
        self.points = link_points(by_id, self, [list(p) for p in cfg.path])  # type: ignore[attr-defined]
        if cfg.length is None:  # type: ignore[attr-defined]
            drawn = sum(math.dist(a, b) for a, b in zip(self.points, self.points[1:], strict=False))
            self.length = max(drawn, 0.1)
            self._size()

    def step(self, dt: float) -> None:
        pass

    def state(self) -> dict[str, Any]:
        return {"flow_m3h": self.link.flow * 3600}

    def geometry(self) -> dict[str, Any]:
        return {
            **super().geometry(),
            "points": self.points,
            "diameter": self.config.diameter,  # type: ignore[attr-defined]
            "length": self.length,
            "check_valve": self.config.check_valve,  # type: ignore[attr-defined]
        }
