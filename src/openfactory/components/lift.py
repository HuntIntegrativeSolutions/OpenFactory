"""Lift (PLAN 2.5b, ``lift``).

Behavior spec
-------------
A short belt conveyor (the deck) that rises and falls between two heights.

- Deck belt: ``run`` (DO) on sets the target surface speed to ``speed_mps`` (off: 0); the speed
  ramps like a belt conveyor's (0.3 s full scale) and ``running`` (DI) mirrors ``run`` in the same
  tick.
- Lift: ``raise`` (DO) held true drives the deck up to ``lift_height`` above the bottom; released,
  it returns to the bottom, at ``lift_height / lift_time_s`` meters per second, reversing at once
  on a command change (the stopper's ``raise`` and the turntable's ``turn`` work the same way).
  ``at_top`` (DI) is true only at the top and ``at_bottom`` (DI) only at the bottom; both are
  false while moving. ``start: top`` begins at the top, with ``raise`` off it then goes down.

The kinematic model (``core/kinematics.py``) treats the deck as a conveyor whose frame moves up
and down with it (:meth:`Lift.frame`): parts on the deck move with it, and the deck belt carries
them along local +x. A ``connections`` entry to or from the deck hands parts across only while the
two belt tops are level within 1 mm (``kinematics.LEVEL_M``), so a lift may be fed at the bottom
and feed a conveyor at the top (or both ways); a part reaching the end of a conveyor whose
connections are all out of level waits there. In the viewer, parts on a moving deck follow the
core's position (PLAN §3.3). Faults: ``jammed`` stops belt and lift at once, ``slow`` runs both at
half speed, ``stuck_on``/``stuck_off`` act on the DIs.

Geometry: the origin is the centre of the deck's infeed end, at deck-belt top height with the deck
at the bottom (as ``belt_conveyor``); flow is local +x.
"""

from __future__ import annotations

from typing import Any

from openfactory.components.belt_conveyor import RAMP_S
from openfactory.core.component import Component, register
from openfactory.core.geometry import Vec3

_EPS = 1e-9


@register("lift")
class Lift(Component):
    FAULTS = ("stuck_on", "stuck_off", "jammed", "slow")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.height = self.config.lift_height if self.config.start == "top" else 0.0
        self.surface_speed = 0.0  # deck belt, m/s along local +x
        self.travel = 0.0

    @property
    def moving(self) -> bool:
        """True while the deck is between the bottom and the top."""
        return _EPS < self.height < self.config.lift_height - _EPS

    def frame(self) -> tuple[Vec3, float]:
        """``(origin, rotation_deg)`` of the deck as a belt: its infeed end at the deck's height."""
        x, y, z = self.config.position
        return (x, y + self.height, z), self.config.rotation_deg

    def step(self, dt: float) -> None:
        cfg = self.config
        target = (cfg.speed_mps if self.read("run", default=False) else 0.0) * self.speed_factor
        if self.speed_factor == 0:
            self.surface_speed = 0.0  # jammed: deck belt and lift stop at once
        max_delta = cfg.speed_mps / RAMP_S * dt
        delta = target - self.surface_speed
        self.surface_speed = target if abs(delta) <= max_delta else (
            self.surface_speed + (max_delta if delta > 0 else -max_delta)
        )  # fmt: skip
        self.travel += self.surface_speed * dt
        goal = cfg.lift_height if self.read("raise", default=False) else 0.0
        rate = cfg.lift_height / cfg.lift_time_s * dt * self.speed_factor
        self.height = goal if abs(goal - self.height) <= rate + _EPS else (
            self.height + (rate if goal > self.height else -rate)
        )  # fmt: skip
        self.write("running", bool(self.read("run", default=False)))
        self.write("at_top", abs(self.height - cfg.lift_height) <= _EPS)
        self.write("at_bottom", abs(self.height) <= _EPS)

    def state(self) -> dict[str, Any]:
        return {
            "running": bool(self.read("run", default=False)),
            "speed_mps": self.surface_speed,
            "travel_m": self.travel,
            "height_m": self.height,
            "moving": self.moving,
        }

    def geometry(self) -> dict[str, Any]:
        return {
            **super().geometry(),
            "length": self.config.length,
            "width": self.config.width,
            "lift_height": self.config.lift_height,
        }
