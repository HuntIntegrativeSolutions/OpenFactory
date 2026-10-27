"""Pneumatic pusher (PLAN §5, ``pusher``).

Behavior spec
-------------
A single-solenoid, spring-return linear actuator. While ``extend`` (DO) is on, the plate moves out
at ``stroke / extend_time_s``; while it is off, the plate retracts at the same speed. A command
change mid-stroke reverses direction immediately. ``retracted`` (DI) is true only at position 0 and
``extended`` (DI) only at full stroke; both are false mid-stroke. The pusher starts retracted, and
its limit switches are written from the first step. Moving parts it contacts is done by the
kinematic model (``core/kinematics.py``) and viewer physics, which read :attr:`Pusher.position`.

Geometry: the origin is the center of the retracted push-plate face; the plate extends along
local +z. The plate is ``plate_width`` (local x, default 0.3 m) by ``plate_height`` (y), centered on
the origin.

Default plate height: a plate that meets a box below its centre of mass tips it instead of sliding
it (seen in the viewer with a 0.15 m plate against 0.4 m boxes). When ``plate_height`` is unset, the
loader (:func:`resolve_plate_height`) sizes the plate so its top reaches ``PLATE_REACH`` (3/4) of
the tallest emitted part's height above the belt the plate sweeps (:func:`push_path`), a quarter of
that height above its centre of mass. The plate stays centered on the origin, so a low origin gives
a tall plate that extends below the belt surface; raise ``position`` y to keep it above. With no
emitter or no belt in the plate's path the default is 0.15 m. ``openfactory lint`` warns when a
plate top is below an emitted part's centre of mass (W206) or the stroke stops short of the far
edge of the swept belt (W207).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from openfactory.core.component import Component, register
from openfactory.core.geometry import rotate_y, to_local
from openfactory.core.schema import CARRIER_TYPES, Scene
from openfactory.core.sizing import emitted_parts, resolver

_EPS = 1e-9
DEFAULT_PLATE_HEIGHT_M = 0.15
PLATE_REACH = 0.75  # plate top at this fraction of the tallest part's height above the belt


@dataclass(frozen=True)
class PushPath:
    """The belt a pusher's plate sweeps, measured along the stroke from the retracted face."""

    belt_id: str
    belt_y: float  # belt top surface, where pushed parts rest
    near_m: float  # rest face to the belt's near edge (negative if the face rests over it)
    far_m: float  # rest face to the belt's far edge: the stroke that clears a part off it


def push_path(pusher: Any, scene: Scene) -> PushPath | None:
    """The first belt along the plate's centre line, below the plate's centre, that it reaches.

    ``pusher`` is a pusher config. Only belts whose near edge is within the stroke count.
    """
    ox, oy, oz = pusher.position
    d = rotate_y((0.0, 0.0, 1.0), pusher.rotation_deg)
    best: PushPath | None = None
    for belt in scene.components:
        if belt.type not in CARRIER_TYPES or belt.position[1] > oy + _EPS:
            continue
        o = to_local(belt.position, belt.rotation_deg, (ox, oy, oz))
        if belt.type == "turntable":  # a deck's origin is its centre, at 0 degrees
            o = (o[0] + belt.length / 2, o[1], o[2])
        u = rotate_y(d, -belt.rotation_deg)
        t0, t1 = -math.inf, math.inf
        for axis, lo, hi in ((0, 0.0, belt.length), (2, -belt.width / 2, belt.width / 2)):
            if abs(u[axis]) < 1e-12:
                if not lo - _EPS <= o[axis] <= hi + _EPS:
                    t0, t1 = math.inf, -math.inf
                continue
            a, b = (lo - o[axis]) / u[axis], (hi - o[axis]) / u[axis]
            t0, t1 = max(t0, min(a, b)), min(t1, max(a, b))
        if t0 >= t1 or t1 <= _EPS or t0 > pusher.stroke + _EPS:
            continue
        if best is None or t0 < best.near_m:
            best = PushPath(belt.id, belt.position[1], t0, t1)
    return best


def default_plate_height(pusher: Any, scene: Scene) -> float:
    path, parts = push_path(pusher, scene), emitted_parts(scene)
    if path is None or not parts:
        return DEFAULT_PLATE_HEIGHT_M
    top = path.belt_y + PLATE_REACH * max(part.size[1] for part in parts)
    return max(DEFAULT_PLATE_HEIGHT_M, round(2 * (top - pusher.position[1]), 6))


@resolver("pusher")
def resolve_plate_height(pusher: Any, scene: Scene) -> Any:
    """The pusher config with ``plate_height`` filled in when the scene leaves it unset."""
    if pusher.plate_height is not None:
        return pusher
    return pusher.model_copy(update={"plate_height": default_plate_height(pusher, scene)})


@register("pusher")
class Pusher(Component):
    FAULTS = ("stuck_on", "stuck_off", "jammed", "slow")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.position = 0.0  # meters out from retracted, 0..stroke

    @property
    def speed(self) -> float:
        return self.config.stroke / self.config.extend_time_s

    def step(self, dt: float) -> None:
        stroke = self.config.stroke
        travel = self.speed * dt * self.speed_factor  # jammed: 0; slow: a fraction
        if self.read("extend", default=False):
            self.position = min(stroke, self.position + travel)
        else:
            self.position = max(0.0, self.position - travel)
        if stroke - self.position <= _EPS:
            self.position = stroke
        elif self.position <= _EPS:
            self.position = 0.0
        self.write("extended", self.position >= stroke)
        self.write("retracted", self.position <= 0.0)

    def state(self) -> dict[str, Any]:
        return {"position_m": self.position, "extending": bool(self.read("extend", default=False))}

    def geometry(self) -> dict[str, Any]:
        return {
            **super().geometry(),
            "stroke": self.config.stroke,
            "plate_width": self.config.plate_width,
            "plate_height": self.config.plate_height,
        }
