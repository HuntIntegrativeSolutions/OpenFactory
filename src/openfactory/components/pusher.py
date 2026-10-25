"""Pneumatic pusher (PLAN §5, ``pusher``).

Behavior spec
-------------
A single-solenoid, spring-return linear actuator. While ``extend`` (DO) is on, the plate moves out
at ``stroke / extend_time_s``; while it is off, the plate retracts at the same speed. A command
change mid-stroke reverses direction immediately. ``retracted`` (DI) is true only at position 0 and
``extended`` (DI) only at full stroke; both are false mid-stroke. The pusher starts retracted, and
its limit switches are written from the first step. Moving parts it contacts is done by the
kinematic model (PLAN 1.5) and viewer physics, which read :attr:`Pusher.position`.

Geometry: the origin is the center of the retracted push-plate face; the plate extends along
local +z.
"""

from __future__ import annotations

from typing import Any

from openfactory.core.component import Component, register

_EPS = 1e-9


@register("pusher")
class Pusher(Component):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.position = 0.0  # meters out from retracted, 0..stroke

    @property
    def speed(self) -> float:
        return self.config.stroke / self.config.extend_time_s

    def step(self, dt: float) -> None:
        stroke = self.config.stroke
        if self.read("extend", default=False):
            self.position = min(stroke, self.position + self.speed * dt)
        else:
            self.position = max(0.0, self.position - self.speed * dt)
        if stroke - self.position <= _EPS:
            self.position = stroke
        elif self.position <= _EPS:
            self.position = 0.0
        self.write("extended", self.position >= stroke)
        self.write("retracted", self.position <= 0.0)

    def state(self) -> dict[str, Any]:
        return {"position_m": self.position, "extending": bool(self.read("extend", default=False))}

    def geometry(self) -> dict[str, Any]:
        return {**super().geometry(), "stroke": self.config.stroke}
