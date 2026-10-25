"""Headless kinematic part model (PLAN 1.5): part motion without a browser or Rapier.

Behavior spec
-------------
Each part is either *carried* by a conveyor (:attr:`Part.carrier`) or *falling*.

- **Carried:** the part moves along its conveyor's local +x, resting on the belt top. Its speed
  (:attr:`Part.v_flow`) follows the conveyor's current ``surface_speed``, but friction limits how
  fast: the speed changes by at most ``friction × 9.81 m/s² × dt`` per step (scene
  ``scene.friction``, default 0.8), speeding up and slowing down alike. A part that lands on a
  running belt therefore slips for ``v / (μ·g)`` seconds (64 ms at 0.5 m/s and μ = 0.8) before it
  moves with the belt, as in the viewer's Rapier physics. The speed is updated before the position
  (semi-implicit Euler, like Rapier). When its center passes the discharge end (local x > length) it
  either hands off to the conveyor that end feeds (scene ``connections``), landing on the downstream
  infeed with the overshoot carried over, its speed kept, its lateral offset kept (clamped to the
  belt width), and its height snapped to the new belt top, or, with no connection, it leaves the
  belt and starts to fall. A collinear chain therefore hands off with no jump in position, and a
  connection bridges any gap, step, or turn like a transfer plate. A part whose center ends up off
  either end or beyond either belt edge (local |z| > width / 2) also starts to fall.
- **Falling:** gravity (9.81 m/s², semi-implicit Euler). A falling part lands on the highest
  conveyor whose belt top it crosses during the step and whose footprint (0 ≤ local x ≤ length,
  |local z| ≤ width / 2) holds the part's center, with zero speed along the belt (falling parts have
  no horizontal motion). A part spawned exactly at rest on a belt lands in its first step. A part
  that falls more than 2 m below the lowest belt top is removed from the world and counted in
  :attr:`World.lost`.

- **Pushed:** a pusher's plate (``plate_width`` × ``plate_height``, its face at local
  z = :attr:`Pusher.position`) moves parts along its local +z. Each step, if the plate advanced
  from z0 to z1 since the last step, every part whose footprint overlaps the plate (local x and y
  ranges) and that lies at least partly ahead of the old face (part back face < z1 and front face
  > z0) is displaced along +z so its back face sits on the new face. A plate reaching a part
  mid-step therefore pushes it only by the remaining travel. Retracting plates do not pull parts.
  A part pushed until its center is past a belt edge falls as above, and lands on whatever belt is
  below (in the shipped sorter, the divert lane alongside).

Tick order (:meth:`openfactory.core.loader.LoadedScene.step`): the kinematic model pushes and moves
parts first, then components step in scene order. Photo eyes and removers therefore see this tick's
part positions, while parts move at the belt speed the conveyors reached, and are pushed by the
plate travel the pushers made, on the previous tick.

Limits (deliberate, for a deterministic CI model): slip is modeled only as the μ·g acceleration
cap (no tipping, no bounce on landing), no part-to-part collisions (parts overlap on a stopped belt;
PLAN task 2.0), part boxes keep their world-axis orientation through turns, falling parts drop
straight down, and a pusher only pushes along its own axis: a part conveyed sideways into an
extended plate passes through it.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import TYPE_CHECKING

from openfactory.core.geometry import rotate_y, to_local, to_world
from openfactory.core.schema import CARRIER_TYPES, Connection
from openfactory.core.world import Part, World

if TYPE_CHECKING:
    from openfactory.core.component import Component

GRAVITY = 9.81
FALL_LIMIT_M = 2.0
DEFAULT_FRICTION = 0.8
_EPS = 1e-9


class KinematicModel:
    """Moves the world's parts along conveyors and under gravity."""

    def __init__(
        self,
        world: World,
        components: Iterable[Component],
        connections: Iterable[Connection],
        friction: float = DEFAULT_FRICTION,
    ) -> None:
        self.world = world
        #: Largest change in a carried part's speed per second (μ·g), m/s².
        self.max_accel = friction * GRAVITY
        self.carriers: dict[str, Component] = {
            c.id: c for c in components if c.type in CARRIER_TYPES
        }
        self.downstream: dict[str, str] = {c.from_: c.to for c in connections}
        tops = [self._top(c) for c in self.carriers.values()]
        self.lost_below = (min(tops) if tops else 0.0) - FALL_LIMIT_M
        self.pushers: list[Component] = [c for c in components if c.type == "pusher"]
        #: Plate position (m out from retracted) each pusher had when parts were last pushed.
        self._plate_seen: dict[str, float] = {c.id: self._plate(c) for c in self.pushers}

    # -- geometry helpers ------------------------------------------------------------------------

    @staticmethod
    def _top(carrier: Component) -> float:
        return carrier.config.position[1]

    @staticmethod
    def _local(carrier: Component, point: tuple[float, float, float]) -> tuple[float, float, float]:
        return to_local(carrier.config.position, carrier.config.rotation_deg, point)

    @staticmethod
    def _world(carrier: Component, local: tuple[float, float, float]) -> tuple[float, float, float]:
        return to_world(carrier.config.position, carrier.config.rotation_deg, local)

    @staticmethod
    def _plate(pusher: Component) -> float:
        """Plate travel in meters (0 = retracted); 0 for a stand-in without one (test stubs)."""
        return float(getattr(pusher, "position", 0.0))

    def _holds(self, carrier: Component, point: tuple[float, float, float]) -> bool:
        x, _, z = self._local(carrier, point)
        cfg = carrier.config
        return -_EPS <= x <= cfg.length + _EPS and abs(z) <= cfg.width / 2 + _EPS

    # -- stepping --------------------------------------------------------------------------------

    def step(self, dt: float) -> None:
        for pusher in self.pushers:
            self._push(pusher)
        for part in list(self.world.parts.values()):
            if part.carrier is not None:
                self._convey(part, dt)
            else:
                self._fall(part, dt)

    def _convey(self, part: Part, dt: float) -> None:
        carrier = self.carriers[part.carrier]  # type: ignore[index]
        cfg = carrier.config
        x, _, z = self._local(carrier, part.position)
        target = carrier.surface_speed  # type: ignore[attr-defined]
        max_dv = self.max_accel * dt
        dv = target - part.v_flow
        part.v_flow = target if abs(dv) <= max_dv else part.v_flow + math.copysign(max_dv, dv)
        x += part.v_flow * dt
        rest_y = part.size[1] / 2  # part center height above the belt top
        if x > cfg.length + _EPS and carrier.id in self.downstream:
            nxt = self.carriers[self.downstream[carrier.id]]
            half_w = nxt.config.width / 2
            lateral = min(max(z, -half_w), half_w)
            part.position = self._world(nxt, (x - cfg.length, rest_y, lateral))
            part.carrier = nxt.id
            return
        part.position = self._world(carrier, (x, rest_y, z))
        if not self._holds(carrier, part.position):
            part.carrier = None  # off the end or the side: falls from here
            part.vy = 0.0
            part.v_flow = 0.0

    def _push(self, pusher: Component) -> None:
        """Displace parts ahead of the plate by the plate's advance since the last step."""
        z0 = self._plate_seen[pusher.id]
        z1 = self._plate(pusher)
        self._plate_seen[pusher.id] = z1
        if z1 <= z0 + _EPS:
            return
        cfg = pusher.config
        rot = cfg.rotation_deg
        # the plate's local axes in world coordinates, to project world-aligned part boxes onto
        axes = [rotate_y(axis, rot) for axis in ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))]
        for part in self.world.parts.values():
            lx, ly, lz = to_local(cfg.position, rot, part.position)
            hx, hy, hz = (sum(abs(a[i]) * part.size[i] / 2 for i in range(3)) for a in axes)
            if abs(lx) >= cfg.plate_width / 2 + hx or abs(ly) >= cfg.plate_height / 2 + hy:
                continue  # beside, above, or below the plate
            back, front = lz - hz, lz + hz
            if back < z1 and front > z0:
                part.position = to_world(cfg.position, rot, (lx, ly, z1 + hz))

    def _fall(self, part: Part, dt: float) -> None:
        vy = part.vy - GRAVITY * dt
        bottom_before = part.bottom
        bottom_after = bottom_before + vy * dt
        landing: Component | None = None
        for carrier in self.carriers.values():
            top = self._top(carrier)
            crosses = bottom_after - _EPS <= top <= bottom_before + _EPS
            higher = landing is None or top > self._top(landing)
            if crosses and higher and self._holds(carrier, part.position):
                landing = carrier
        x, y, z = part.position
        if landing is not None:
            part.position = (x, self._top(landing) + part.size[1] / 2, z)
            part.vy = 0.0
            part.v_flow = 0.0
            part.carrier = landing.id
            return
        part.position = (x, y + vy * dt, z)
        part.vy = vy
        if part.bottom < self.lost_below:
            self.world.lose(part.id)
