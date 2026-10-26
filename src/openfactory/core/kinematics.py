"""Headless kinematic part model (PLAN 1.5): part motion without a browser or Rapier.

Behavior spec
-------------
Each part is either *carried* by a conveyor (:attr:`Part.carrier`) or *falling*.

- **Carried:** the part moves along its conveyor's local +x, resting on the belt top. Its speed
  (:attr:`Part.v_flow`) follows the conveyor's current ``surface_speed``, but friction limits how
  fast: the speed changes by at most ``friction × 9.81 m/s² × dt`` per step (scene
  ``scene.friction``, default 0.8), speeding up and slowing down alike. A part at rest on a running
  belt therefore slips for ``v / (μ·g)`` seconds (64 ms at 0.5 m/s and μ = 0.8) before it moves with
  the belt, as in the viewer's Rapier physics (a dropped part gets a head start from its landing
  impact, below). The speed is updated before the position (semi-implicit Euler, like Rapier). When
  its center passes the discharge end (local x > length) it either hands off to the conveyor that
  end feeds (scene ``connections``), landing on the downstream infeed with the overshoot carried
  over, its speed kept, its lateral offset kept (clamped to the belt width), and its height snapped
  to the new belt top, or, with no connection, it leaves the belt and starts to fall. A collinear
  chain therefore hands off with no jump in position, and a connection bridges any gap, step, or
  turn like a transfer plate. A part whose center ends up off either end or beyond either belt edge
  (local |z| > width / 2) also starts to fall.
  While a part straddles a connection seam it rests on both belts in proportion to its overlap,
  and each belt pulls it toward that belt's speed with up to its share of μ·g (Coulomb friction;
  a belt already at the part's speed holds it statically up to its share). So a part crossing onto
  a faster belt speeds up only as more of it rests there, as in Rapier, instead of jumping to the
  full μ·g ramp when its center crosses. On a single belt this is exactly the ramp above.
- **Queued (PLAN 2.0):** a carried part never moves into the part ahead of it. Each step, leaders
  move before followers (downstream belts first, then the part furthest along each belt), and a
  part's advance is limited so its front face stops at the back face of the nearest carried part
  ahead that overlaps it across the flow (local z) and in height, on its own belt or, past its
  discharge end, on the belt it feeds. A blocked part's speed becomes the advance it actually
  made, so it re-accelerates from there when the way clears. Parts therefore accumulate nose to
  tail behind a stopped belt and release in order; side-by-side parts do not block each other.
- **Falling:** gravity (9.81 m/s², semi-implicit Euler). A falling part lands on the highest
  conveyor whose belt top it crosses during the step and whose footprint (0 ≤ local x ≤ length,
  |local z| ≤ width / 2) holds the part's center. Landing is an impact: the normal impulse that
  stops the fall (m·|vy|) lets friction set the along-belt speed to the belt's, limited to ±μ·|vy|,
  at once (as Rapier's contact solver does), so a box dropped 0.1 m onto a 0.5 m/s belt at μ 0.8
  lands moving with it; any remainder follows the μ·g ramp. Falling parts have no horizontal motion.
  A part spawned exactly at rest on a belt lands in its first step. A part that falls more than 2 m
  below the lowest belt top is removed from the world and counted in :attr:`World.lost`.

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

Limits (deliberate, for a deterministic CI model): slip is modeled only as the μ·g acceleration cap
plus the μ·|vy| grab on landing (no tipping, no bounce, no soft-contact spreading of the impact as
in Rapier), part-to-part contact only along the flow axis (no stacking, no sideways shoving, no line
pressure: a queue on a running belt never pushes the part it waits behind, and a part reaching a
stopped downstream belt crosses until its center is on it, whereas in Rapier the queue's pressure
can shove the leader and the stopped belt's friction brakes a part as soon as its nose overhangs;
PLAN 2.0 note), part boxes keep their world-axis orientation through turns, falling parts drop
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
        #: Belts from the end of a chain: 0 feeds nothing, 1 feeds a 0, ... (loops capped).
        self._rank: dict[str, int] = {c: self._chain_rank(c) for c in self.carriers}
        #: |cos|, |sin| of each belt's rotation, for part extents along and across its flow.
        self._axes: dict[str, tuple[float, float]] = {
            cid: (
                abs(math.cos(math.radians(c.config.rotation_deg))),
                abs(math.sin(math.radians(c.config.rotation_deg))),
            )
            for cid, c in self.carriers.items()
        }
        self._boxes: dict[str, dict[str, tuple[float, float, float, float, float, float]]] = {}
        tops = [self._top(c) for c in self.carriers.values()]
        self.lost_below = (min(tops) if tops else 0.0) - FALL_LIMIT_M
        self.pushers: list[Component] = [c for c in components if c.type == "pusher"]
        #: Plate position (m out from retracted) each pusher had when parts were last pushed.
        self._plate_seen: dict[str, float] = {c.id: self._plate(c) for c in self.pushers}

    def _chain_rank(self, carrier_id: str) -> int:
        rank, seen = 0, {carrier_id}
        while carrier_id in self.downstream:
            carrier_id = self.downstream[carrier_id]
            if carrier_id in seen:  # recirculation loop: no true end, keep scene order
                break
            seen.add(carrier_id)
            rank += 1
        return rank

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
        parts = list(self.world.parts.values())
        falling = [p for p in parts if p.carrier is None]  # a part leaving a belt falls next tick
        # Each carried part's belt-local box, computed once per tick and updated as it moves, so
        # the queueing check (_room_ahead) is plain arithmetic: belt -> part id -> box.
        self._boxes = {c: {} for c in self.carriers}
        for part in parts:
            if part.carrier is not None:
                self._record(part)
        # Leaders move before the parts behind them (PLAN 2.0): downstream belts first, then
        # the part furthest along each belt, so a follower is limited by where its leader is now.
        carried = sorted(
            (p for p in parts if p.carrier is not None),
            key=lambda p: (self._rank[p.carrier], -self._boxes[p.carrier][p.id][0]),  # type: ignore[index]
        )
        for part in carried:
            before = part.carrier
            self._convey(part, dt)
            self._boxes[before].pop(part.id, None)  # type: ignore[index]
            if part.carrier is not None:
                self._record(part)  # its new box, on whichever belt now carries it
        for part in falling:
            self._fall(part, dt)

    def _convey(self, part: Part, dt: float) -> None:
        carrier = self.carriers[part.carrier]  # type: ignore[index]
        cfg = carrier.config
        x, _, z = self._local(carrier, part.position)
        part.v_flow = self._friction_speed(part.v_flow, self._supports(part, carrier, x), dt)
        if part.v_flow > 0:
            room = self._room_ahead(part, carrier, x, z)
            if x + part.v_flow * dt > room:  # queued: stop at the back face of the part ahead
                advance = max(0.0, room - x)
                part.v_flow = advance / dt
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

    def _supports(self, part: Part, carrier: Component, x: float) -> list[tuple[float, float]]:
        """``(belt speed, share of the part's footprint)`` for each belt under the part. A part
        straddling a connection seam rests on both belts, in proportion to its overlap."""
        own = carrier.surface_speed  # type: ignore[attr-defined]
        half, _ = self._extent(part, carrier)
        length = carrier.config.length
        if carrier.id in self.downstream and x + half > length:  # nose onto the next belt
            share = min(1.0, (x + half - length) / (2 * half))
            nxt = self.carriers[self.downstream[carrier.id]]
            return [(own, 1.0 - share), (nxt.surface_speed, share)]  # type: ignore[attr-defined]
        if x - half < 0:  # tail still on a belt feeding this one
            feeders = [self.carriers[u] for u, d in self.downstream.items() if d == carrier.id]
            if feeders:
                share = min(1.0, (half - x) / (2 * half))
                return [(own, 1.0 - share), (feeders[0].surface_speed, share)]  # type: ignore[attr-defined]
        return [(own, 1.0)]

    def _friction_speed(self, v: float, supports: list[tuple[float, float]], dt: float) -> float:
        """New along-belt speed under Coulomb friction from each supporting belt.

        Each belt pulls toward its own speed with up to share × μ·g. A belt already moving at the
        part's speed holds it with static friction, up to its share, against the others. The speed
        never passes a belt speed within one step (it stops there and static friction decides the
        next step). On one belt this is the plain μ·g ramp toward that belt's speed.
        """
        a, held = 0.0, 0.0
        for speed, share in supports:
            if abs(speed - v) <= _EPS:
                held += share * self.max_accel
            else:
                a += math.copysign(share * self.max_accel, speed - v)
        if abs(a) <= held + _EPS:
            return v
        a -= math.copysign(held, a)
        new = v + a * dt
        crossed = [sp for sp, _ in supports if min(v, new) - _EPS <= sp <= max(v, new) + _EPS]
        return min(crossed, key=lambda sp: abs(sp - v)) if crossed else new

    def _extent(self, part: Part, carrier: Component) -> tuple[float, float]:
        """Half-extents of a part's world-axis box along a carrier's flow (x) and across it (z)."""
        c, s = self._axes[carrier.id]
        sx, _, sz = part.size
        return (c * sx + s * sz) / 2, (s * sx + c * sz) / 2

    def _record(self, part: Part) -> None:
        """Cache a carried part's box in its belt's frame: (x, z, half_x, half_z, y, height)."""
        carrier = self.carriers[part.carrier]  # type: ignore[index]
        x, _, z = self._local(carrier, part.position)
        half_x, half_z = self._extent(part, carrier)
        self._boxes[carrier.id][part.id] = (x, z, half_x, half_z, part.position[1], part.size[1])

    def _room_ahead(self, part: Part, carrier: Component, x: float, z: float) -> float:
        """Largest local x the part's center may reach this step without overlapping a carried
        part ahead of it, on its own belt or on the belt it feeds (mapped past this belt's end)."""
        half_x, half_z = self._extent(part, carrier)
        y, height = part.position[1], part.size[1]
        room = math.inf
        frames = [(carrier.id, 0.0)]
        if carrier.id in self.downstream:
            frames.append((self.downstream[carrier.id], carrier.config.length))
        for belt_id, offset in frames:
            for pid, (ox, oz, o_half_x, o_half_z, oy, oh) in self._boxes[belt_id].items():
                if pid == part.id or abs(oy - y) >= (oh + height) / 2:
                    continue  # itself, or above or below: no contact
                ox += offset
                if ox <= x or abs(oz - z) >= half_z + o_half_z - _EPS:
                    continue  # behind, or beside on the same or the next belt
                room = min(room, ox - o_half_x - half_x)
        return room

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
            # Impact friction: the normal impulse that stops the fall (m·|vy|) lets friction change
            # the along-belt speed by up to μ·|vy| at once, as Rapier's contact solver does.
            grab = self.max_accel / GRAVITY * abs(vy)
            target = landing.surface_speed  # type: ignore[attr-defined]
            part.v_flow = max(-grab, min(grab, target))
            part.vy = 0.0
            part.carrier = landing.id
            return
        part.position = (x, y + vy * dt, z)
        part.vy = vy
        if part.bottom < self.lost_below:
            self.world.lose(part.id)
