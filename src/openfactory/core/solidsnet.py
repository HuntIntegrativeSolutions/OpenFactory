"""Solids network (PLAN 5.7a, ADR-0006): bulk material as a mass flow, not as parts.

Behavior spec
-------------
Bulk material is a quantity (kg), never an object. **Nodes** hold or exchange it: a
:class:`Bin` holds a mass up to its capacity (its volume × its material's bulk density), a
:class:`Supply` delivers up to its capacity a second (unlimited when unset) while enabled, and a
:class:`Discharge` takes up to its demand a second (everything when unset). **Links** move it
from their ``from`` end to their ``to`` end: a :class:`Feeder` (a rotary airlock, a slide gate)
moves it within the tick, and a :class:`Carrier` (a screw conveyor; in 5.7b an elevator and a
bulk belt) carries it for its travel time. There are no heads and no iteration (ADR-0006
decision 3).

Each tick the links are taken **downstream first**: the reverse of the network's topological
order, ties in the order given, a loop broken by taking the link that closes it (the earliest
given of those left) as the most upstream. So a link that empties a bin has done so before the
link that fills it asks for room, and material moves at most one link a tick. A link moves
``min(capacity × speed × dt, available upstream, room downstream)``:

- available: a bin's mass (none while it is bridged), a supply's capacity × dt less what it gave
  this tick (none while disabled), or, for a carrier fed by another link, what that link puts in;
- room: a bin's capacity less its mass, a discharge's demand × dt less what it took this tick, or
  a carrier's room at its inlet (below). A link whose outlet is plugged has no room.

**A carrier** is a ring of ``N = max(1, round(travel_s / dt))`` slots (sized on its first tick),
and a slot holds at most ``capacity × dt``, so the carrier holds at most ``capacity ×
travel_s``. Running at speed fraction ``s`` (0 to 1) it advances ``s`` slots a tick, the fraction
kept for the next tick. To advance, its outlet slot must leave in full, up to the room at its
``to``; when it cannot, the carrier is **blocked**: what left is gone, the rest stays in the slot,
the ring does not advance and nothing enters that tick. Its inlet slot takes in up to its free
room whether it runs or not, so a stopped or blocked carrier under a running link fills one
slot's worth and then blocks that link in its turn: never overfilled (the blocked-feeder rule).
Mass entering at full speed leaves ``N`` ticks later: the dead time is the travel time.

Mass balances every tick, to float rounding: the bins' mass plus the carriers' loads changes by
what the supplies gave less what the discharges took.

Deterministic: no clock, no randomness; links and nodes are taken in a fixed order.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field

#: A carrier's phase counts as a whole slot from this close below 1 (0.3 + 0.3 + 0.4).
PHASE_TOL = 1e-9


@dataclass
class Node:
    id: str

    def available(self, dt: float) -> float:
        return 0.0

    def room(self, dt: float) -> float:
        return 0.0

    def take(self, mass: float) -> None:
        raise ValueError(f"{self.id} gives nothing")

    def put(self, mass: float) -> None:
        raise ValueError(f"{self.id} takes nothing")


@dataclass
class Bin(Node):
    """A vessel: ``mass`` kg of one material, at most ``capacity`` kg."""

    capacity: float = 0.0
    mass: float = 0.0
    #: A bridge over the outlet (the ``bridge`` fault): it gives nothing while set.
    bridged: bool = False

    def available(self, dt: float) -> float:
        return 0.0 if self.bridged else self.mass

    def room(self, dt: float) -> float:
        return max(0.0, self.capacity - self.mass)

    def take(self, mass: float) -> None:
        self.mass -= mass

    def put(self, mass: float) -> None:
        self.mass += mass


@dataclass
class Supply(Node):
    """Material arriving from off the drawing: ``capacity`` kg/s (None: unlimited)."""

    capacity: float | None = None
    enabled: bool = True
    given: float = 0.0  # this tick, kg
    total: float = 0.0  # in the run, kg

    def available(self, dt: float) -> float:
        if not self.enabled:
            return 0.0
        return math.inf if self.capacity is None else max(0.0, self.capacity * dt - self.given)

    def take(self, mass: float) -> None:
        self.given += mass
        self.total += mass


@dataclass
class Discharge(Node):
    """Material leaving the drawing: ``demand`` kg/s (None: everything that arrives)."""

    demand: float | None = None
    taken: float = 0.0  # this tick, kg
    total: float = 0.0  # in the run, kg

    def room(self, dt: float) -> float:
        return math.inf if self.demand is None else max(0.0, self.demand * dt - self.taken)

    def put(self, mass: float) -> None:
        self.taken += mass
        self.total += mass


@dataclass
class Link:
    """A feeder from ``from_`` to ``to``: ``capacity`` kg/s at full speed and full fill, running
    at ``speed`` (0 to 1, set by its component each tick)."""

    id: str
    from_: str
    to: str
    capacity: float
    speed: float = 0.0
    #: A plugged chute at its outlet (the ``plugged`` fault): its outlet takes nothing.
    plugged: bool = False
    moved: float = 0.0  # delivered at its outlet this tick, kg
    total: float = 0.0  # delivered in the run, kg
    blocked: bool = False  # its outlet could not take what it had this tick
    carries = False

    def rate(self, dt: float) -> float:
        """What it delivered this tick, kg/s."""
        return self.moved / dt if dt > 0 else 0.0


@dataclass
class Feeder(Link):
    """An airlock or a gate: what it moves arrives in the same tick."""


@dataclass
class Carrier(Link):
    """A screw (in 5.7b an elevator, a bulk belt): it carries for ``travel_s`` at full speed."""

    travel_s: float = 1.0
    slots: list[float] = field(default_factory=list)  # inlet first, outlet last
    phase: float = 0.0
    slot_max: float = 0.0
    carries = True

    @property
    def load(self) -> float:
        """The mass on it, kg."""
        return math.fsum(self.slots)

    def size(self, dt: float) -> None:
        if not self.slots:
            self.slots = [0.0] * max(1, round(self.travel_s / dt))
            self.slot_max = self.capacity * dt

    def inlet_room(self) -> float:
        return 0.0 if self.blocked else max(0.0, self.slot_max - self.slots[0])

    def put(self, mass: float) -> None:
        self.slots[0] += mass

    def fill(self) -> list[float]:
        """Each slot's fill, 0 to 1, inlet first (the viewer's ridge)."""
        if self.slot_max <= 0:
            return [0.0 for _ in self.slots]
        return [min(1.0, s / self.slot_max) for s in self.slots]


class Network:
    """The solids network of a scene: its nodes, and its links in the order they step."""

    def __init__(self, nodes: list[Node], links: list[Link]) -> None:
        self.nodes = {n.id: n for n in nodes}
        self.links = {lk.id: lk for lk in links}
        self.order = _downstream_first(links, self.nodes)
        self.ticks = 0

    @classmethod
    def build(cls, nodes: Iterable[Node], links: Iterable[Link]) -> Network:
        """A network, or ValueError for an end that names nothing it can join: a link's ``from``
        is a bin or a supply, or (for a carrier only) another link; its ``to`` is a bin, a
        discharge or a carrier."""
        nodes, links = list(nodes), list(links)
        ids = {n.id: n for n in nodes}
        by_link = {lk.id: lk for lk in links}
        for lk in links:
            src = ids.get(lk.from_) or by_link.get(lk.from_)
            if src is None:
                raise ValueError(f"{lk.id}: from {lk.from_!r} names no bulk component")
            if isinstance(src, Discharge):
                raise ValueError(f"{lk.id}: from {lk.from_!r} is a discharge, which gives nothing")
            if isinstance(src, Link) and not lk.carries:
                raise ValueError(f"{lk.id}: from {lk.from_!r} is a feeder; an airlock or gate "
                                 "sits under a bin or a supply")  # fmt: skip
            dst = ids.get(lk.to) or by_link.get(lk.to)
            if dst is None:
                raise ValueError(f"{lk.id}: to {lk.to!r} names no bulk component")
            if isinstance(dst, Supply):
                raise ValueError(f"{lk.id}: to {lk.to!r} is a supply, which takes nothing")
            if isinstance(dst, Link) and not dst.carries:
                raise ValueError(f"{lk.id}: to {lk.to!r} is a feeder; only a screw (or another "
                                 "carrier) is fed by a link")  # fmt: skip
            if dst is lk:
                raise ValueError(f"{lk.id}: feeds itself")
        return cls(nodes, links)

    # -- one tick ----------------------------------------------------------------------------

    def _room(self, end: str, dt: float) -> float:
        node = self.nodes.get(end)
        if node is not None:
            return node.room(dt)
        carrier = self.links[end]
        assert isinstance(carrier, Carrier)
        carrier.size(dt)
        return carrier.inlet_room()

    def _put(self, end: str, mass: float) -> None:
        node = self.nodes.get(end)
        if node is not None:
            node.put(mass)
        else:
            self.links[end].put(mass)  # type: ignore[attr-defined]

    def step(self, dt: float) -> None:
        """Move material for one tick of ``dt`` seconds. Never reads the clock."""
        for node in self.nodes.values():
            if isinstance(node, Supply):
                node.given = 0.0
            elif isinstance(node, Discharge):
                node.taken = 0.0
        for lk in self.links.values():
            lk.moved, lk.blocked = 0.0, False
            if isinstance(lk, Carrier):
                lk.size(dt)
        for lk in self.order:
            if isinstance(lk, Carrier):
                self._carry(lk, dt)
            else:
                self._feed(lk, dt)
        self.ticks += 1

    def _feed(self, lk: Link, dt: float) -> None:
        src = self.nodes[lk.from_]
        want = min(lk.capacity * _fraction(lk.speed) * dt, src.available(dt))
        if want <= 0.0:
            return
        room = 0.0 if lk.plugged else self._room(lk.to, dt)
        mass = min(want, room)
        lk.blocked = mass < want
        if mass > 0.0:
            src.take(mass)
            self._put(lk.to, mass)
            lk.moved += mass
            lk.total += mass

    def _carry(self, lk: Carrier, dt: float) -> None:
        lk.phase += _fraction(lk.speed)
        while lk.phase >= 1.0 - PHASE_TOL:
            head = lk.slots[-1]
            if head > 0.0:
                room = 0.0 if lk.plugged else self._room(lk.to, dt)
                mass = min(head, room)
                if mass > 0.0:
                    self._put(lk.to, mass)
                    lk.slots[-1] = head - mass if mass < head else 0.0
                    lk.moved += mass
                    lk.total += mass
                if mass < head:  # blocked: it holds what it has, and takes nothing in
                    lk.blocked = True
                    lk.phase = 1.0 - 2 * PHASE_TOL  # one advance owed: tried again next tick
                    return
            lk.slots.insert(0, lk.slots.pop())  # the empty outlet slot comes round to the inlet
            lk.phase -= 1.0
        if lk.phase < 0.0:
            lk.phase = 0.0
        src = self.nodes.get(lk.from_)
        if src is None:  # fed by another link, which puts into its inlet
            return
        want = min(lk.capacity * _fraction(lk.speed) * dt, src.available(dt), lk.inlet_room())
        if want > 0.0:
            src.take(want)
            lk.slots[0] += want

    # -- what a test or a check reads --------------------------------------------------------

    def held(self) -> float:
        """Every bin's mass and every carrier's load, kg."""
        bins = [n.mass for n in self.nodes.values() if isinstance(n, Bin)]
        loads = [s for lk in self.links.values() if isinstance(lk, Carrier) for s in lk.slots]
        return math.fsum(bins + loads)

    def supplied(self) -> float:
        return math.fsum(n.total for n in self.nodes.values() if isinstance(n, Supply))

    def discharged(self) -> float:
        return math.fsum(n.total for n in self.nodes.values() if isinstance(n, Discharge))


def _fraction(speed: float) -> float:
    return min(max(float(speed), 0.0), 1.0)


def _downstream_first(links: list[Link], nodes: dict[str, Node]) -> list[Link]:
    """The links in the order they step: downstream first (see the module's spec)."""
    index = {lk.id: i for i, lk in enumerate(links)}
    feeds: dict[str, set[str]] = {lk.id: set() for lk in links}  # link -> links just downstream
    for a in links:
        for b in links:
            if a is b:
                continue
            if a.to == b.id or (a.to in nodes and b.from_ == a.to):
                feeds[a.id].add(b.id)
    upstream = {lk.id: 0 for lk in links}
    for downs in feeds.values():
        for b in downs:
            upstream[b] += 1
    left = [lk.id for lk in links]
    order: list[str] = []
    while left:
        ready = [i for i in left if upstream[i] == 0]
        pick = min(ready or left, key=index.__getitem__)  # none ready: a loop, broken here
        left.remove(pick)
        order.append(pick)
        for b in feeds[pick]:
            upstream[b] -= 1
    by_id = {lk.id: lk for lk in links}
    return [by_id[i] for i in reversed(order)]


@dataclass(frozen=True)
class BinShape:
    """A bin's inside (ADR-0006 decision 5): a cylinder (``diameter``) or a box (``length`` ×
    ``width``) of straight wall ``height``, over a cone or a pyramid of ``cone_height`` down to a
    round or square outlet of ``outlet`` across (no hopper section when ``cone_height`` is 0).
    Heights are from the outlet."""

    shape: str
    diameter: float
    length: float
    width: float
    height: float
    cone_height: float
    outlet: float

    @property
    def total_height(self) -> float:
        return self.cone_height + self.height

    def _top_area(self) -> float:
        if self.shape == "box":
            return self.length * self.width
        return math.pi * self.diameter**2 / 4

    def _hopper(self, y: float) -> float:
        """Volume of the hopper section from the outlet up to ``y`` (0 to ``cone_height``)."""
        hc = self.cone_height
        if self.shape == "box":
            a0 = self.outlet
            p, q = (self.length - a0) / hc, (self.width - a0) / hc
            return a0 * a0 * y + a0 * (p + q) * y * y / 2 + p * q * y**3 / 3
        r0, r1 = self.outlet / 2, self.diameter / 2
        k = (r1 - r0) / hc
        return math.pi * (r0 * r0 * y + r0 * k * y * y + k * k * y**3 / 3)

    def volume(self, level: float | None = None) -> float:
        """Volume below ``level`` (m from the outlet), m³; the whole bin when None."""
        y = self.total_height if level is None else min(max(level, 0.0), self.total_height)
        if self.cone_height <= 0:
            return self._top_area() * y
        if y <= self.cone_height:
            return self._hopper(y)
        return self._hopper(self.cone_height) + self._top_area() * (y - self.cone_height)

    def level(self, volume: float) -> float:
        """The level of ``volume`` m³ in the bin, m from the outlet (its surface drawn flat):
        exact in the straight wall, by bisection (60 halvings) in the hopper."""
        if volume <= 0:
            return 0.0
        full = self.volume()
        if volume >= full:
            return self.total_height
        hopper = self.volume(self.cone_height) if self.cone_height > 0 else 0.0
        if volume >= hopper:
            return self.cone_height + (volume - hopper) / self._top_area()
        lo, hi = 0.0, self.cone_height
        for _ in range(60):
            mid = (lo + hi) / 2
            if self._hopper(mid) < volume:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2
