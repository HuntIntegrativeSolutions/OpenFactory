"""Roller conveyor (PLAN §5, ``roller_conveyor``).

Behavior spec
-------------
Driven rollers. The ports and the speed behavior are the belt conveyor's: the target surface speed
is ``speed_mps × speed% / 100`` while ``run`` (DO) is on and 0 while it is off, the actual speed
follows it with the same 0.3 s full-scale ramp, and ``running`` (DI) mirrors ``run`` in the same
tick. The kinematic model (``core/kinematics.py``) and the viewer physics carry parts on it exactly
as on a belt, except that parts accelerate at ``friction × 9.81 m/s²`` with the roller's own
``friction`` (default: the scene's), so low-friction rollers take longer to bring a part up to
speed, and a part landing on them grabs less of the roller speed at impact.

Geometry: the origin is the center of the infeed end at roller-top height; flow is local +x. The
viewer draws rollers across the flow every ``roller_pitch`` meters, turning with the surface.
"""

from __future__ import annotations

from typing import Any

from openfactory.components.belt_conveyor import BeltConveyor
from openfactory.core.component import register


@register("roller_conveyor")
class RollerConveyor(BeltConveyor):
    def geometry(self) -> dict[str, Any]:
        return {
            **super().geometry(),
            "roller_pitch": self.config.roller_pitch,
            "friction": self.config.friction,
        }
