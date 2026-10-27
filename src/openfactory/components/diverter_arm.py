"""Diverter arm (PLAN §5, ``diverter_arm``).

Behavior spec
-------------
A paddle on a pivot at the edge of a belt. While ``extend`` (DO) is on, the arm swings from rest
(along the belt edge, local +x) out across the belt to ``swing_deg`` at ``|swing_deg| /
extend_time_s`` degrees per second; while it is off it swings back at the same rate, reversing at
once on a command change (as the pusher's ``extend``). ``extended`` (DI) is true only at
``swing_deg`` and ``retracted`` (DI) only at rest; both are false while moving.

The kinematic model (``core/kinematics.py``) treats the arm as a frictionless fence for parts on
the belt its pivot stands on: a part that would pass through the arm is moved back out along the
arm's normal, so a part carried into an extended arm slides along it at the belt speed's component
along the arm, and a swinging arm sweeps the parts it reaches. A part slid past the belt edge falls
onto whatever is below (a side conveyor). The viewer's Rapier physics uses the arm as a frictionless
kinematic body, so diverted parts stay Rapier-driven (PLAN §3.3).

Geometry: the origin is the pivot at belt-top height; at rest the arm lies along local +x, and
``swing_deg`` is positive toward local +z. The arm is ``length`` long and :data:`THICKNESS_M`
thick, from :data:`CLEARANCE_M` above the belt to ``height`` (its top above the belt). An unset
``height`` is sized at load (:func:`resolve_arm`) to 3/4 of the tallest emitted part, like the
pusher plate, and ``openfactory lint`` warns (W206) when a set height is below an emitted part's
centre of mass.
"""

from __future__ import annotations

from typing import Any

from openfactory.core.component import Component, register
from openfactory.core.schema import Scene
from openfactory.core.sizing import resolver, tallest_part

_EPS = 1e-9
THICKNESS_M = 0.02
CLEARANCE_M = 0.005
DEFAULT_HEIGHT_M = 0.15
ARM_REACH = 0.75  # arm top at this fraction of the tallest part's height


@resolver("diverter_arm")
def resolve_arm(config: Any, scene: Scene) -> Any:
    if config.height is not None:
        return config
    tallest = tallest_part(scene)
    height = ARM_REACH * tallest if tallest else DEFAULT_HEIGHT_M
    return config.model_copy(update={"height": round(height, 6)})


@register("diverter_arm")
class DiverterArm(Component):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.angle = 0.0  # degrees from rest, 0 .. swing_deg

    def step(self, dt: float) -> None:
        swing = self.config.swing_deg
        goal = swing if self.read("extend", default=False) else 0.0
        rate = abs(swing) / self.config.extend_time_s * dt
        if abs(goal - self.angle) <= rate + _EPS:
            self.angle = goal
        else:
            self.angle += rate if goal > self.angle else -rate
        self.write("extended", abs(self.angle - swing) <= _EPS)
        self.write("retracted", abs(self.angle) <= _EPS)

    def state(self) -> dict[str, Any]:
        return {"angle_deg": self.angle, "extending": bool(self.read("extend", default=False))}

    def geometry(self) -> dict[str, Any]:
        return {
            **super().geometry(),
            "length": self.config.length,
            "height": self.config.height,
            "swing_deg": self.config.swing_deg,
            "thickness": THICKNESS_M,
            "clearance": CLEARANCE_M,
        }
