"""Distance sensor (PLAN §5, ``distance_sensor``).

Behavior spec
-------------
A laser or ultrasonic range sensor. Each step it measures along its beam, from the sensor face for
``beam_length`` meters, to the nearest point where the beam enters a part's box or a conveyor's
belt (its top surface, :data:`BELT_DEPTH_M` deep; 0 when the face is inside one), and writes that
distance in meters to ``distance`` (AI). With nothing in the beam it reads ``beam_length``. A
sensor looking down at a belt therefore reads the belt's distance when no part is under it, and
a part's top when one is. ``switching`` (AI) carries the same value for a binding with a window
(PLAN 2.17), ``{tag: X_DI, window: [lo, hi]}``: the DI is true while the distance is inside
``[lo, hi]``, like a real sensor's switching output beside its analog one.

Both ports' range is 0 to ``beam_length`` for this sensor (``range_from``), so ``int16_scaled``
maps 0 m to 0 and ``beam_length`` to 32767; the scene's int16_scaled lockfile records that range,
and changing ``beam_length`` fails the lockfile check instead of silently rescaling (ADR-0004
lockfile, PLAN 2.5 ruling). The reading is the core's only: the viewer draws the beam to the
measured distance but has no physics for it (PLAN §3.3).

Geometry: the origin is the sensor face. ``axis: +z`` aims the beam along local +z (across the flow,
like a photo eye's beam, but from one side); ``axis: -y`` aims it straight down.
"""

from __future__ import annotations

import math
from typing import Any

from openfactory.core.component import Component, register
from openfactory.core.geometry import Vec3, rotate_y, to_local

_EPS = 1e-12
#: A belt is seen as a slab this deep below its top surface, meters.
BELT_DEPTH_M = 0.05


def ray_box(origin: Vec3, direction: Vec3, lo: Vec3, hi: Vec3) -> float | None:
    """Distance along a unit ray to where it enters the box [lo, hi], 0 if it starts inside, or
    None if it misses (slab method)."""
    t0, t1 = 0.0, math.inf
    for axis in range(3):
        o, d = origin[axis], direction[axis]
        if abs(d) < _EPS:
            if o < lo[axis] or o > hi[axis]:
                return None
            continue
        a, b = (lo[axis] - o) / d, (hi[axis] - o) / d
        t0, t1 = max(t0, min(a, b)), min(t1, max(a, b))
        if t0 > t1:
            return None
    return t0


@register("distance_sensor")
class DistanceSensor(Component):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.distance = float(self.config.beam_length)

    @property
    def direction(self) -> Vec3:
        if self.config.axis == "-y":
            return (0.0, -1.0, 0.0)
        return rotate_y((0.0, 0.0, 1.0), self.config.rotation_deg)

    def step(self, dt: float) -> None:
        reach = float(self.config.beam_length)
        nearest = reach
        for part in self.world.parts.values():
            lo, hi = part.aabb()
            t = ray_box(self.config.position, self.direction, lo, hi)
            if t is not None and t < nearest:
                nearest = t
        for belt in self.world.conveyors:
            t = self._belt_hit(belt)
            if t is not None and t < nearest:
                nearest = t
        self.distance = nearest
        self.write("distance", nearest)
        self.write("switching", nearest)

    def _belt_hit(self, belt: Any) -> float | None:
        """Distance to a conveyor's belt slab, in the belt's own frame (turntable decks turn)."""
        frame = getattr(belt, "frame", None)
        origin, rotation = frame() if frame else (belt.config.position, belt.config.rotation_deg)
        o = to_local(origin, rotation, self.config.position)
        d = rotate_y(self.direction, -rotation)
        cfg = belt.config
        return ray_box(o, d, (0.0, -BELT_DEPTH_M, -cfg.width / 2), (cfg.length, 0.0, cfg.width / 2))

    def state(self) -> dict[str, Any]:
        return {
            "distance_m": self.distance,
            "target": self.distance < self.config.beam_length,
        }

    def geometry(self) -> dict[str, Any]:
        return {
            **super().geometry(),
            "axis": self.config.axis,
            "beam_length": self.config.beam_length,
        }
