"""Pick and place (PLAN §5, ``pick_and_place``).

Behavior spec
-------------
A two-axis pneumatic gantry with a gripper pad. ``traverse`` (DO) held moves the carriage from the
pick side to the place side, ``travel`` meters along local +x in ``travel_time_s``; released, it
returns. ``lower`` (DO) held lowers the pad by ``stroke`` in ``lower_time_s``; released, it rises.
Both axes reverse at once on a command change. ``at_pick`` and ``at_place`` (DI) are true only at
either end of the traverse, ``lowered`` (DI) only at full stroke: it reports the pad's true
position.

Pad collision (PLAN 2.5b): a lowering pad stops on the top of the highest part under it (its
footprint overlapping the pad's, its top at or below the pad), so a pad lowered onto a box taller
than the stroke allows rests on the box, with ``lowered`` false. ``contact`` (DI) is true while the
pad rests on a part's top or holds a part. Only the pad collides; a held part does not.

``grip`` (DO) on while the pad is fully lowered or in contact picks the highest part whose top face
is within :data:`GRAB_M` of the pad's bottom and whose footprint overlaps the pad: the part leaves
its conveyor, keeps its offset from the pad, and moves with the pad (``holding`` DI true). The
kinematic model leaves a held part alone (:attr:`openfactory.core.world.Part.held_by`). ``grip``
off releases it where it is, and it falls onto whatever is below. A held part a remover takes is
let go. In the viewer, held parts follow the core's position (PLAN §3.3).

Geometry: the origin is the bottom centre of the pad, raised, over the pick point; the traverse runs
along local +x and the pad drops along -y. The pad is ``grip_size``.
"""

from __future__ import annotations

from typing import Any

from openfactory.core.component import Component, register
from openfactory.core.geometry import Vec3, rotate_y, to_world

_EPS = 1e-9
#: A part is gripped when its top face is within this of the lowered pad's bottom, meters.
GRAB_M = 0.02
#: The pad rests on a part when its bottom is this close to the part's top, meters.
_CONTACT_M = 1e-6


def _toward(value: float, goal: float, rate: float) -> float:
    if abs(goal - value) <= rate + _EPS:
        return goal
    return value + (rate if goal > value else -rate)


@register("pick_and_place")
class PickAndPlace(Component):
    FAULTS = ("stuck_on", "stuck_off", "jammed", "slow")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.x = 0.0  # carriage travel from the pick side, meters
        self.y = 0.0  # pad drop from raised, meters
        self.held: str | None = None  # id of the held part
        self._offset: Vec3 = (0.0, 0.0, 0.0)

    @property
    def pad(self) -> Vec3:
        """World position of the bottom centre of the pad."""
        return to_world(self.config.position, self.config.rotation_deg, (self.x, -self.y, 0.0))

    def step(self, dt: float) -> None:
        cfg = self.config
        self.x = _toward(
            self.x, cfg.travel if self.read("traverse", default=False) else 0.0,
            cfg.travel / cfg.travel_time_s * dt * self.speed_factor,
        )  # fmt: skip
        drop = _toward(
            self.y, cfg.stroke if self.read("lower", default=False) else 0.0,
            cfg.stroke / cfg.lower_time_s * dt * self.speed_factor,
        )  # fmt: skip
        if drop > self.y:  # lowering: stop on the highest part under the pad (PLAN 2.5b)
            drop = min(drop, self._floor())
        self.y = max(drop, 0.0)
        lowered = abs(self.y - cfg.stroke) <= _EPS
        grip = bool(self.read("grip", default=False))
        if self.held is not None and self.held not in self.world.parts:
            self.held = None  # a remover took it
        resting = self._resting()
        if self.held is None and grip and (lowered or resting):
            self._pick()
        elif self.held is not None and not grip:
            part = self.world.parts[self.held]
            part.held_by, part.carrier, part.vy, part.v_flow = None, None, 0.0, 0.0
            self.held = None
        if self.held is not None:
            px, py, pz = self.pad
            ox, oy, oz = self._offset
            self.world.parts[self.held].position = (px + ox, py + oy, pz + oz)
        self.write("at_pick", self.x <= _EPS)
        self.write("at_place", abs(self.x - cfg.travel) <= _EPS)
        self.write("lowered", lowered)
        self.write("holding", self.held is not None)
        self.write("contact", self.held is not None or self._resting())

    def _half_pad(self) -> tuple[float, float]:
        gx, _, gz = self.config.grip_size
        c, _, s = rotate_y((1.0, 0.0, 0.0), self.config.rotation_deg)
        return (abs(c) * gx + abs(s) * gz) / 2, (abs(s) * gx + abs(c) * gz) / 2

    def _under(self) -> list[tuple[float, Any]]:
        """``(top, part)`` of every part not held here whose footprint overlaps the pad's and
        whose top is at or below the pad's bottom."""
        px, py, pz = self.pad
        pad_hx, pad_hz = self._half_pad()
        out = []
        for part in self.world.parts.values():
            if part.id == self.held:
                continue
            (lx, _, lz), (hx, hy, hz) = part.aabb()
            if hx <= px - pad_hx or lx >= px + pad_hx or hz <= pz - pad_hz or lz >= pz + pad_hz:
                continue  # not under the pad
            if hy <= py + _CONTACT_M:
                out.append((hy, part))
        return out

    def _floor(self) -> float:
        """The deepest drop the pad may reach before touching a part under it."""
        tops = [top for top, _ in self._under()]
        if not tops:
            return self.config.stroke
        return self.config.position[1] - max(tops)

    def _resting(self) -> bool:
        """True while the pad's bottom is on the top of a part under it."""
        py = self.pad[1]
        return any(abs(top - py) <= _CONTACT_M for top, _ in self._under())

    def _pick(self) -> None:
        px, py, pz = self.pad
        pad_hx, pad_hz = self._half_pad()
        best = None
        for part in self.world.parts.values():
            (lx, ly, lz), (hx, hy, hz) = part.aabb()
            if abs(hy - py) > GRAB_M + _EPS:
                continue  # its top is not at the pad
            if hx <= px - pad_hx or lx >= px + pad_hx or hz <= pz - pad_hz or lz >= pz + pad_hz:
                continue  # not under the pad
            if best is None or hy > best.aabb()[1][1]:
                best = part
        if best is None:
            return
        best.held_by, best.carrier, best.vy, best.v_flow = self.id, None, 0.0, 0.0
        self.held = best.id
        x, y, z = best.position
        self._offset = (x - px, y - py, z - pz)

    def state(self) -> dict[str, Any]:
        return {
            "x_m": self.x,
            "y_m": self.y,
            "gripping": bool(self.read("grip", default=False)),
            "holding": self.held is not None,
        }

    def geometry(self) -> dict[str, Any]:
        return {
            **super().geometry(),
            "travel": self.config.travel,
            "stroke": self.config.stroke,
            "grip_size": list(self.config.grip_size),
        }
