"""Turntable (PLAN §5, ``turntable``).

Behavior spec
-------------
A short belt conveyor (the deck) on a platform that turns 90 degrees about its centre.

- Deck belt: ``run`` (DO) on sets the target surface speed to ``speed_mps`` (off: 0); the speed
  ramps like a belt conveyor's (0.3 s full scale) and ``running`` (DI) mirrors ``run`` in the same
  tick.
- Turn: ``turn`` (DO) held true drives the deck to ``turn_deg`` (+90 counter-clockwise seen from
  above, or -90); released, it returns to 0, at ``|turn_deg| / turn_time_s`` degrees per second,
  reversing at once on a command change (the same convention as the pusher's ``extend``).
  ``at_0`` (DI) is true only at 0 and ``at_90`` (DI) only at ``turn_deg``; both are false while
  moving.

The kinematic model (``core/kinematics.py``) treats the deck as a conveyor whose frame turns with
it (:meth:`Turntable.frame`): parts on the deck turn with it about the centre, and the deck belt
carries them along its current heading. A ``connections`` entry to or from the deck hands parts
across only while the two conveyors' headings agree within 1 degree (``kinematics.ALIGN_DEG``);
a part reaching the end of a conveyor whose connections are all out of line waits there. In the
viewer, parts on a turning deck follow the core's position (PLAN §3.3).

Geometry: the origin is the deck centre (the pivot) at deck-belt top height; at 0 degrees the deck
flows along local +x. Unlike ``belt_conveyor`` the origin is not the infeed end.
"""

from __future__ import annotations

from typing import Any

from openfactory.components.belt_conveyor import RAMP_S
from openfactory.core.component import Component, register
from openfactory.core.geometry import Vec3, rotate_y

_EPS = 1e-9


@register("turntable")
class Turntable(Component):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.angle = 0.0  # degrees turned, 0 .. turn_deg
        self.surface_speed = 0.0  # deck belt, m/s along the deck's current heading
        self.travel = 0.0

    @property
    def heading(self) -> float:
        """Deck flow direction as a rotation about +y, degrees (scene rotation plus the turn)."""
        return self.config.rotation_deg + self.angle

    @property
    def turning(self) -> bool:
        """True while the deck is between 0 and ``turn_deg``."""
        return _EPS < abs(self.angle) < abs(self.config.turn_deg) - _EPS

    def frame(self) -> tuple[Vec3, float]:
        """``(origin, rotation_deg)`` of the deck as a belt: origin at its current infeed end."""
        cx, cy, cz = self.config.position
        dx, _, dz = rotate_y((self.config.length / 2, 0.0, 0.0), self.heading)
        return (cx - dx, cy, cz - dz), self.heading

    def step(self, dt: float) -> None:
        cfg = self.config
        target = cfg.speed_mps if self.read("run", default=False) else 0.0
        max_delta = cfg.speed_mps / RAMP_S * dt
        delta = target - self.surface_speed
        self.surface_speed = target if abs(delta) <= max_delta else (
            self.surface_speed + (max_delta if delta > 0 else -max_delta)
        )  # fmt: skip
        self.travel += self.surface_speed * dt
        goal = float(cfg.turn_deg) if self.read("turn", default=False) else 0.0
        rate = abs(cfg.turn_deg) / cfg.turn_time_s * dt
        self.angle = goal if abs(goal - self.angle) <= rate + _EPS else (
            self.angle + (rate if goal > self.angle else -rate)
        )  # fmt: skip
        self.write("running", bool(self.read("run", default=False)))
        self.write("at_0", abs(self.angle) <= _EPS)
        self.write("at_90", abs(self.angle - cfg.turn_deg) <= _EPS)

    def state(self) -> dict[str, Any]:
        return {
            "running": bool(self.read("run", default=False)),
            "speed_mps": self.surface_speed,
            "travel_m": self.travel,
            "angle_deg": self.angle,
            "turning": self.turning,
        }

    def geometry(self) -> dict[str, Any]:
        return {
            **super().geometry(),
            "length": self.config.length,
            "width": self.config.width,
            "turn_deg": self.config.turn_deg,
        }
