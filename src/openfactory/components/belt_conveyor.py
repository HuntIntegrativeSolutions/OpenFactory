"""Belt conveyor (PLAN §5, ``belt_conveyor``).

Behavior spec
-------------
The belt's target surface speed is ``speed_mps × speed% / 100`` while the ``run`` command (DO) is on
and 0 while it is off. ``speed`` (AO, 0–100 %) is clamped to its range; when the port is unbound the
belt runs at 100 %. The actual surface speed follows the target with a linear ramp that takes 0.3 s
for a full-scale (0 → ``speed_mps``) change, in both directions, so a stopped belt coasts to rest.
``running`` (DI) mirrors the run command in the same tick, like a motor contactor's auxiliary
contact; it does not wait for the ramp. The belt does not move parts itself: the kinematic model
(PLAN 1.5) and the viewer physics read :attr:`BeltConveyor.surface_speed`.

Reversing (PLAN 2.5b): a conveyor with ``bidirectional: true`` has a ``reverse`` (DO). With
``run`` and ``reverse`` on, the target speed is the same magnitude toward the infeed end
(negative along local +x); ``reverse`` alone does nothing. A change of direction ramps through
zero at the same rate, so a full reversal takes 0.6 s. ``running`` mirrors ``run`` either way.

Geometry: the origin is the center of the infeed end at belt top-surface height; flow is local +x.
"""

from __future__ import annotations

from typing import Any

from openfactory.core.component import Component, register

RAMP_S = 0.3


@register("belt_conveyor")
class BeltConveyor(Component):
    FAULTS = ("stuck_on", "stuck_off", "jammed", "slow")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.surface_speed = 0.0  # m/s along local +x
        self.travel = 0.0  # cumulative belt travel in meters, for viewer belt animation

    def target_speed(self) -> float:
        if not self.read("run", default=False):
            return 0.0
        lo, hi = self.ports["speed"].range or (0.0, 100.0)
        pct = min(max(float(self.read("speed", default=hi)), lo), hi)
        forward = self.config.speed_mps * pct / 100.0
        return -forward if self.reversing else forward

    @property
    def reversing(self) -> bool:
        """True while the reverse command is on (a bidirectional conveyor only)."""
        return bool(self.config.bidirectional and self.read("reverse", default=False))

    def step(self, dt: float) -> None:
        target = self.target_speed() * self.speed_factor  # jammed: 0; slow: a fraction
        if self.speed_factor == 0:
            self.surface_speed = 0.0  # a jammed belt stops at once
        max_delta = self.config.speed_mps / RAMP_S * dt
        delta = target - self.surface_speed
        self.surface_speed = (
            target
            if abs(delta) <= max_delta
            else (self.surface_speed + (max_delta if delta > 0 else -max_delta))
        )
        self.travel += self.surface_speed * dt
        self.write("running", bool(self.read("run", default=False)))

    def state(self) -> dict[str, Any]:
        state = {
            "running": bool(self.read("run", default=False)),
            "speed_mps": self.surface_speed,
            "travel_m": self.travel,
        }
        if self.config.bidirectional:
            state["reverse"] = self.reversing
        return state

    def geometry(self) -> dict[str, Any]:
        out = {**super().geometry(), "length": self.config.length, "width": self.config.width}
        if self.config.bidirectional:
            out["bidirectional"] = True
        return out
