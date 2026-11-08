"""Flow network solver (PLAN 3.1, ADR-0005; design and sources: docs/research/flownet-design.md).

Behavior spec
-------------
The process plant is an incompressible liquid network solved quasi-statically once per tick.
**Nodes** are tanks, supplies and open drains, whose head is known during a solve, and junctions,
whose head is solved. **Links** are pipes, valves and pumps between two node ends. A tank end
names a nozzle (``TK_01.bottom``): under the liquid surface it sees the tank's head, above it
its own elevation, and a nozzle at or above the surface passes no outflow. A link end may name
another link, which joins the two through an implicit junction at the downstream link's
position (ADR-0005 decision 3).

Each link relates the head drop across it to its flow ``q`` (m³/s, positive from its ``from``
end to its ``to`` end):

- resistance (pipe, valve): ``h_from - h_to = r·q·|q|``, and ``r·Q_LIN·q`` below ``Q_LIN``
  (3.6 L/h), so the slope stays above zero at zero flow, as EPANET 2.2's low-flow linear
  relation does; above ``Q_LIN`` the loss is exactly quadratic.
- pump: ``h_to - h_from = ω²·A - B·ω^(2-n)·qⁿ`` for ``q >= 0``, with relative speed ``ω``
  (EPANET 2.2 manual, equation 12.2); it never runs backwards.
- demand (a junction with a demand, ADR-0005 decision 4): a virtual link to a fixed head at the
  junction's elevation plus ``minimum_head``, with ``h - (z + P0) = R·d²``, ``R = (Pf - P0)/D²``,
  so the full demand ``D`` flows at or above ``Pf`` of pressure head and none at or below
  ``P0`` (Wagner's model, manual equations 12.13 to 12.17).

The solve is the Global Gradient Algorithm (Todini and Pilati; manual §12.1, equations 12.5 to
12.12): linearize every open link at its current flow, solve the junction heads from flow
continuity (dense Gaussian elimination with partial pivoting), update every flow, repeat until
the summed flow change is below ``ACCURACY`` of the summed flow and every link's head error is
below ``HEAD_TOL``, at most ``MAX_ITERATIONS`` times (a solve that reaches the cap is counted in
:attr:`Network.stats` and its last iterate used). Then the status checks: a pump or a
``check_valve`` pipe with reverse flow closes, a link drawing out of a tank through a dry nozzle
closes, a link that would draw liquid out of an open drain (``sink_only``) closes, and a closed
one reopens when its heads would drive flow the allowed way; the solve
repeats while statuses change, at most ``MAX_STATUS_ROUNDS`` times. Junctions that no open link
connects to a node of known head carry no flow (their links are idle this solve).

**Integration** (manual §12.1, "Extended period analysis", adapted to a fixed tick): tank
volumes advance by forward Euler with the solved flows. Where a tank's level would fall to a
nozzle it is drawing out of before the tick ends, the tick is split there (at most
``MAX_SPLITS`` times per tick): every tank advances to that moment, the nozzle runs dry, and the
network is solved again for the rest of the tick. A tank above its height overflows: its level
holds and the excess is counted in :attr:`Tank.overflow`. So the change in all tank volumes
equals the supplies' delivery minus the drains' receipt minus overflow, every tick, to rounding.

Each tank is well mixed: its temperature and ingredient fractions take the inflows' (a supply's
own, a tank's, or a junction's flow-weighted mix of what enters it) by an exact heat and volume
balance, plus any heat a heater adds (:attr:`Tank.heat_w`). Properties are water's at 20 °C:
NIST Chemistry WebBook (SRD 69), density 998.21 kg/m³ and Cp 4.1841 kJ/(kg·K) at 0.101 MPa
(IAPWS-95), read 2026-11-07.

Deterministic: no clock, no randomness; links, nodes and junctions are visited in the order
given; tolerances and caps are fixed.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from openfactory.core.kinematics import GRAVITY

#: Water at 20 °C, 0.101 MPa (NIST SRD 69, IAPWS-95).
DENSITY = 998.21  # kg/m³
SPECIFIC_HEAT = 4184.1  # J/(kg·K)
#: Metres of water per bar (1e5 Pa / (ρ·g)), about 10.21.
BAR_HEAD = 1e5 / (DENSITY * GRAVITY)
#: Flow below which a resistance's loss turns linear (m³/s, 3.6 L/h).
Q_LIN = 1e-6
#: Convergence: summed |Δq| over summed |q|, and the largest head error (m).
ACCURACY = 1e-6
HEAD_TOL = 1e-3
MAX_ITERATIONS = 40
MAX_STATUS_ROUNDS = 5
MAX_SPLITS = 5
#: A tank level within this of a nozzle's height counts as at the nozzle (m).
LEVEL_EPS = 1e-9
#: Slope outside a demand's limits (m per m³/s), EPANET's "high resistance".
R_HIGH = 1e8
#: Smallest flow a pump's slope is taken at (m³/s), so a pump near shutoff keeps a slope.
Q_PUMP_MIN = 1e-5


class FlowNetError(ValueError):
    """A network that cannot be built: an unknown end, a nozzle a tank does not have."""


# --- nodes ---------------------------------------------------------------------------------------


@dataclass(eq=False)
class Tank:
    """An open tank of constant cross-section ``area`` (m²) and ``height`` (m); its bottom is at
    ``elevation``. ``nozzles`` maps names to heights above the bottom."""

    id: str
    elevation: float
    area: float
    height: float
    volume: float
    nozzles: dict[str, float]
    temperature: float = 20.0
    fractions: dict[str, float] = field(default_factory=dict)
    #: Heat added to the liquid (W), set before the tick; 0 by default.
    heat_w: float = 0.0
    #: Net heat from each heater on this tank (W, its power less the tank's loss), by heater id.
    heat_sources: dict[str, float] = field(default_factory=dict)
    #: Liquid that overflowed the top, in total (m³).
    overflow: float = 0.0
    known_head = True

    @property
    def level(self) -> float:
        return self.volume / self.area

    @property
    def capacity(self) -> float:
        return self.area * self.height

    @property
    def head(self) -> float:
        """Elevation of the liquid surface."""
        return self.elevation + self.level

    def nozzle_head(self, nozzle: str) -> float:
        """Head a link sees at ``nozzle``: the surface, or the nozzle itself when above it."""
        return max(self.head, self.elevation + self.nozzles[nozzle])

    def dry(self, nozzle: str) -> bool:
        """True when the level is at or below ``nozzle``: no outflow through it."""
        return self.level <= self.nozzles[nozzle] + LEVEL_EPS


@dataclass(eq=False)
class FixedHead:
    """A supply (head = elevation + pressure) or an open drain (head = elevation): a node whose
    head never changes and that delivers or takes whatever the network asks."""

    id: str
    elevation: float
    pressure_bar: float = 0.0
    temperature: float = 20.0
    fractions: dict[str, float] = field(default_factory=dict)
    #: Liquid this node has delivered into the network (m³) and taken out of it (m³).
    delivered: float = 0.0
    received: float = 0.0
    #: An open drain: it takes liquid but never gives any back (a link would draw air).
    sink_only: bool = False
    known_head = True

    @property
    def head(self) -> float:
        return self.elevation + self.pressure_bar * BAR_HEAD


@dataclass(eq=False)
class Junction:
    """A node whose head is solved. With a ``demand`` (m³/s) it draws that flow out of the
    network, pressure-dependently (module docstring), and counts it in ``received``."""

    id: str
    elevation: float
    demand: float = 0.0
    required_head: float = 2.0
    minimum_head: float = 0.0
    head: float = math.nan
    temperature: float = 20.0
    fractions: dict[str, float] = field(default_factory=dict)
    received: float = 0.0
    known_head = False

    @property
    def pressure_bar(self) -> float:
        return (self.head - self.elevation) / BAR_HEAD if math.isfinite(self.head) else 0.0


Node = Tank | FixedHead | Junction


# --- links ---------------------------------------------------------------------------------------


@dataclass(eq=False)
class Link:
    """A pipe, valve or pump between ``from_ref`` and ``to_ref`` (a node id, ``TANK.nozzle``, or
    another link's id). ``position`` places an implicit junction made with this link downstream.
    Subclasses give :meth:`loss` (head drop from ``from`` to ``to`` at flow ``q``) and
    :meth:`slope` (its derivative, kept above zero)."""

    id: str
    from_ref: str
    to_ref: str
    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    #: Flow (m³/s), positive from ``from`` to ``to``; kept between ticks as the next start.
    flow: float = 0.0
    #: Closed by a status check this tick (reverse flow, a dry nozzle); reconsidered every solve.
    held_closed: bool = False
    #: Ends after :meth:`Network.build`: (node, nozzle or None).
    start: tuple[Node, str | None] | None = None
    end: tuple[Node, str | None] | None = None
    #: This link only passes flow from ``from`` to ``to`` (a pump, a check valve).
    forward_only = False

    def available(self) -> bool:
        """Open as commanded (a valve not shut, a pump running)."""
        return True

    def loss(self, q: float) -> float:
        raise NotImplementedError

    def slope(self, q: float) -> float:
        raise NotImplementedError

    def zero_flow_drive(self) -> float:
        """Head drop from ``from`` to ``to`` that would just start forward flow (0 for a
        resistance, minus the shutoff head for a pump)."""
        return 0.0


@dataclass(eq=False)
class Resistance(Link):
    """A pipe or valve: ``loss = r·q·|q|`` (linear below ``Q_LIN``); ``r`` in s²/m⁵, ``math.inf``
    when shut."""

    r: float = 0.0
    check_valve: bool = False

    @property
    def forward_only(self) -> bool:  # type: ignore[override]
        return self.check_valve

    def available(self) -> bool:
        return math.isfinite(self.r)

    def loss(self, q: float) -> float:
        return self.r * q * max(abs(q), Q_LIN)

    def slope(self, q: float) -> float:
        return self.r * (2 * abs(q) if abs(q) >= Q_LIN else Q_LIN)


@dataclass(eq=False)
class Pump(Link):
    """A pump with head gain ``ω²·shutoff - coefficient·ω^(2-n)·qⁿ`` at relative speed ``speed``
    (0 to 1) while ``running``. See :func:`pump_curve` for the coefficients."""

    shutoff: float = 10.0
    coefficient: float = 1.0
    exponent: float = 2.0
    speed: float = 1.0
    running: bool = False
    forward_only = True

    def available(self) -> bool:
        return self.running and self.speed > 0

    def gain(self, q: float) -> float:
        w = self.speed
        return (
            w * w * self.shutoff
            - self.coefficient * w ** (2 - self.exponent) * max(q, 0.0) ** self.exponent
        )

    def loss(self, q: float) -> float:
        return -self.gain(q) + (R_HIGH * q if q < 0 else 0.0)

    def slope(self, q: float) -> float:
        if q < 0:
            return R_HIGH
        w, n = self.speed, self.exponent
        return n * self.coefficient * w ** (2 - n) * max(q, Q_PUMP_MIN) ** (n - 1)

    def zero_flow_drive(self) -> float:
        return -self.speed * self.speed * self.shutoff


@dataclass(eq=False)
class _DemandLink(Link):
    """A demand junction's virtual link to its fixed outlet head (module docstring)."""

    junction: Junction | None = None

    def _r(self) -> float:
        j = self.junction
        assert j is not None
        return (j.required_head - j.minimum_head) / (j.demand * j.demand)

    def loss(self, q: float) -> float:
        d = self.junction.demand  # type: ignore[union-attr]
        if q < 0:
            return R_HIGH * q
        if q > d:
            return self._r() * d * max(d, Q_LIN) + R_HIGH * (q - d)
        return self._r() * q * max(q, Q_LIN)

    def slope(self, q: float) -> float:
        d = self.junction.demand  # type: ignore[union-attr]
        if q < 0 or q > d:
            return R_HIGH
        return self._r() * (2 * q if q >= Q_LIN else Q_LIN)


# --- coefficients from data-sheet values ------------------------------------------------------


def pipe_resistance(length: float, diameter: float, friction: float, minor_loss: float) -> float:
    """``r`` of a pipe (s²/m⁵): Darcy-Weisbach with a fixed friction factor plus minor losses,
    ``(f·L/D + ΣK) / (2·g·A²)``."""
    area = math.pi * diameter * diameter / 4
    return (friction * length / diameter + minor_loss) / (2 * GRAVITY * area * area)


def valve_resistance(kv: float, relative_capacity: float) -> float:
    """``r`` of a valve (s²/m⁵) with flow coefficient ``kv`` (m³/h at a 1 bar drop) passing
    ``relative_capacity`` (0 to 1) of it: ``Q[m³/h] = Kv·f·√(Δh / BAR_HEAD)``. Shut: inf."""
    if relative_capacity <= 0:
        return math.inf
    k = kv * relative_capacity / 3600  # m³/s at a 1 bar drop
    return BAR_HEAD / (k * k)


def kv_from_cv(cv: float) -> float:
    """Kv (m³/h, bar) from Cv (US gpm, psi): Kv = 0.865·Cv (ISA-75.01.01 liquid sizing)."""
    return 0.865 * cv


def pump_curve(points: Sequence[tuple[float, float]]) -> tuple[float, float, float]:
    """``(shutoff, coefficient, exponent)`` for ``h = shutoff - coefficient·qⁿ`` through three
    ``(q m³/s, h m)`` points, the first at zero flow; or, from one design point, EPANET's
    single-point curve: shutoff 133 % of the design head and zero head at twice the design flow
    (EPANET 2.2 manual §3.1, "Pump curve")."""
    if len(points) == 1:
        q, h = points[0]
        points = [(0.0, 1.33 * h), (q, h), (2 * q, 0.0)]
    (q0, h0), (q1, h1), (q2, h2) = points
    if q0 != 0 or not (0 < q1 < q2) or not (h0 > h1 > h2 >= 0):
        raise FlowNetError(
            "a pump curve needs three points of rising flow and falling head, the first at "
            "zero flow"
        )
    n = math.log((h0 - h2) / (h0 - h1)) / math.log(q2 / q1)
    return h0, (h0 - h1) / q1**n, n


# --- the network ---------------------------------------------------------------------------------


@dataclass
class FlowStats:
    """What the solver did, for the statistics block and the tests."""

    solves: int = 0
    iterations: int = 0
    max_iterations: int = 0
    cap_hits: int = 0
    status_rounds: int = 0
    splits: int = 0
    split_cap_hits: int = 0


class Network:
    """A built network: nodes and links with their ends resolved. :meth:`step` advances it."""

    def __init__(self, nodes: Sequence[Node], links: Sequence[Link]) -> None:
        self.nodes = list(nodes)
        self.links = list(links)
        self.tanks = [n for n in self.nodes if isinstance(n, Tank)]
        self.fixed = [n for n in self.nodes if isinstance(n, FixedHead)]
        self.junctions = [n for n in self.nodes if isinstance(n, Junction)]
        self.stats = FlowStats()
        self._demand_links: dict[str, _DemandLink] = {}
        self._outlets: dict[str, FixedHead] = {}

    # -- building ---------------------------------------------------------------------------

    @classmethod
    def build(cls, nodes: Iterable[Node], links: Iterable[Link]) -> Network:
        """Resolve every link end: a node id, ``TANK.nozzle``, or another link's id (an implicit
        junction ``A>B`` at B's position, where A.to names B and B.from names A)."""
        nodes, links = list(nodes), list(links)
        by_node = {n.id: n for n in nodes}
        by_link = {lk.id: lk for lk in links}
        clash = set(by_node) & set(by_link)
        if clash or len(by_node) != len(nodes) or len(by_link) != len(links):
            raise FlowNetError(f"duplicate ids in the flow network: {sorted(clash) or 'repeated'}")
        implicit: dict[tuple[str, str], Junction] = {}

        def joint(up: Link, down: Link) -> Junction:
            key = (up.id, down.id)
            if key not in implicit:
                implicit[key] = Junction(f"{up.id}>{down.id}", down.position[1])
            return implicit[key]

        def resolve(link: Link, ref: str, is_to: bool) -> tuple[Node, str | None]:
            name, _, nozzle = ref.partition(".")
            if name in by_link:
                other = by_link[name]
                back = other.from_ref if is_to else other.to_ref
                if back != link.id:
                    side, other_side = ("to", "from") if is_to else ("from", "to")
                    raise FlowNetError(
                        f"{link.id}.{side} names {name}, so {name}.{other_side} must name "
                        f"{link.id} (it names {back})"
                    )
                return (joint(link, other) if is_to else joint(other, link)), None
            if name not in by_node:
                raise FlowNetError(
                    f"{link.id}: {ref!r} names no tank, supply, drain, junction or link"
                )
            node = by_node[name]
            if isinstance(node, Tank):
                if not nozzle:
                    raise FlowNetError(
                        f"{link.id}: {ref!r} is a tank; name a nozzle "
                        f"({', '.join(f'{name}.{n}' for n in node.nozzles)})"
                    )
                if nozzle not in node.nozzles:
                    raise FlowNetError(
                        f"{link.id}: {name} has no nozzle {nozzle!r} "
                        f"(it has {', '.join(node.nozzles)})"
                    )
                return node, nozzle
            if nozzle:
                raise FlowNetError(f"{link.id}: {name} is not a tank and has no nozzles")
            return node, None

        for link in links:
            link.start = resolve(link, link.from_ref, is_to=False)
            link.end = resolve(link, link.to_ref, is_to=True)
            if link.start[0] is link.end[0] and link.start[1] == link.end[1]:
                raise FlowNetError(f"{link.id}: both ends are {link.from_ref}")
        net = cls([*nodes, *implicit.values()], links)
        attached = {id(e[0]) for lk in links for e in (lk.start, lk.end)}  # type: ignore[index]
        for node in net.junctions:
            if id(node) not in attached:
                raise FlowNetError(f"{node.id}: no link connects to this junction")
        return net

    # -- heads ------------------------------------------------------------------------------

    @staticmethod
    def _end_head(end: tuple[Node, str | None]) -> float:
        node, nozzle = end
        if isinstance(node, Tank):
            return node.nozzle_head(nozzle)  # type: ignore[arg-type]
        return node.head

    @staticmethod
    def _allowed(link: Link) -> tuple[bool, bool]:
        """(forward allowed, reverse allowed) for this solve."""
        fwd, rev = True, not link.forward_only
        node, nozzle = link.start  # type: ignore[misc]
        if isinstance(node, Tank) and node.dry(nozzle):  # type: ignore[arg-type]
            fwd = False  # forward flow would leave this tank through a dry nozzle
        if isinstance(node, FixedHead) and node.sink_only:
            fwd = False  # forward flow would come out of an open drain
        node, nozzle = link.end  # type: ignore[misc]
        if isinstance(node, Tank) and node.dry(nozzle):  # type: ignore[arg-type]
            rev = False
        if isinstance(node, FixedHead) and node.sink_only:
            rev = False
        return fwd, rev

    def _sync_demands(self) -> list[Link]:
        """The virtual links of junctions that have a demand this tick."""
        out: list[Link] = []
        for j in self.junctions:
            if j.demand <= 0:
                continue
            link = self._demand_links.get(j.id)
            if link is None:
                outlet = FixedHead(f"{j.id}:demand", j.elevation)
                link = _DemandLink(f"{j.id}:demand", j.id, outlet.id, junction=j)
                link.start, link.end = (j, None), (outlet, None)
                self._demand_links[j.id], self._outlets[j.id] = link, outlet
            outlet = self._outlets[j.id]
            outlet.elevation = j.elevation + j.minimum_head
            out.append(link)
        return out

    # -- solving ----------------------------------------------------------------------------

    def solve(self) -> int:
        """Solve flows and junction heads for the current tank levels and link settings.
        Returns the Global Gradient iterations used (all status rounds)."""
        links = [*self.links, *self._sync_demands()]
        total = 0
        for round_ in range(MAX_STATUS_ROUNDS + 1):
            active = [lk for lk in links if lk.available() and not lk.held_closed]
            for lk in links:
                if lk not in active:
                    lk.flow = 0.0
            total += self._gga(active)
            if round_ == MAX_STATUS_ROUNDS or not self._status(links):
                break
            self.stats.status_rounds += 1
        self.stats.solves += 1
        self.stats.iterations += total
        return total

    def _status(self, links: list[Link]) -> bool:
        """Close links flowing the wrong way, reopen held links whose heads now drive the
        allowed way. True when any status changed."""
        changed = False
        for lk in links:
            if not lk.available():
                if lk.held_closed:
                    lk.held_closed = False  # shut by command: nothing to hold
                continue
            fwd, rev = self._allowed(lk)
            if not lk.held_closed:
                if (lk.flow > 0 and not fwd) or (lk.flow < 0 and not rev) or (not fwd and not rev):
                    lk.held_closed, lk.flow, changed = True, 0.0, True
                continue
            h_from, h_to = self._end_head(lk.start), self._end_head(lk.end)  # type: ignore[arg-type]
            if not (math.isfinite(h_from) and math.isfinite(h_to)):
                continue
            drop = h_from - h_to
            if (fwd and drop > lk.zero_flow_drive()) or (rev and drop < -lk.zero_flow_drive()):
                lk.held_closed, changed = False, True
        return changed

    def _gga(self, active: list[Link]) -> int:
        """Global Gradient iterations over the ``active`` links; returns how many."""
        # junctions reachable from a node of known head through active links; the rest idle
        adjacent: dict[int, list[Link]] = {}
        for lk in active:
            for node in (lk.start[0], lk.end[0]):  # type: ignore[index]
                adjacent.setdefault(id(node), []).append(lk)
        reached: set[int] = set()
        stack = [n for lk in active for n in (lk.start[0], lk.end[0]) if n.known_head]  # type: ignore[index]
        while stack:
            node = stack.pop()
            if id(node) in reached:
                continue
            reached.add(id(node))
            for lk in adjacent.get(id(node), ()):
                for other in (lk.start[0], lk.end[0]):  # type: ignore[index]
                    if id(other) not in reached:
                        stack.append(other)
        live = [lk for lk in active if id(lk.start[0]) in reached]  # type: ignore[index]
        for lk in active:
            if lk not in live:
                lk.flow = 0.0
        unknown = [j for j in self.junctions if id(j) in reached]
        index = {id(j): i for i, j in enumerate(unknown)}
        for j in self.junctions:
            if id(j) not in reached:
                j.head = math.nan
        if not live:
            return 0
        for lk in live:
            if lk.flow == 0.0:
                lk.flow = self._start_flow(lk)
        n = len(unknown)
        for iteration in range(1, MAX_ITERATIONS + 1):  # noqa: B007 - read after the loop
            a = [[0.0] * n for _ in range(n)]
            f = [0.0] * n
            coeff: list[tuple[float, float]] = []
            for lk in live:
                p = lk.slope(lk.flow)
                y = lk.flow - lk.loss(lk.flow) / p
                coeff.append((p, y))
                i = index.get(id(lk.start[0]))  # type: ignore[index]
                k = index.get(id(lk.end[0]))  # type: ignore[index]
                if i is not None:
                    a[i][i] += 1 / p
                    f[i] -= y
                    if k is not None:
                        a[i][k] -= 1 / p
                    else:
                        f[i] += self._end_head(lk.end) / p  # type: ignore[arg-type]
                if k is not None:
                    a[k][k] += 1 / p
                    f[k] += y
                    if i is not None:
                        a[k][i] -= 1 / p
                    else:
                        f[k] += self._end_head(lk.start) / p  # type: ignore[arg-type]
            heads = _solve_dense(a, f) if n else []
            for j, h in zip(unknown, heads, strict=True):
                j.head = h
            change = total = worst = 0.0
            for lk, (p, y) in zip(live, coeff, strict=True):
                drop = self._end_head(lk.start) - self._end_head(lk.end)  # type: ignore[arg-type]
                new = y + drop / p
                change += abs(new - lk.flow)
                total += abs(new)
                lk.flow = new
                worst = max(worst, abs(lk.loss(new) - drop))
            if change <= ACCURACY * max(total, 1e-9) and worst <= HEAD_TOL:
                break
        else:
            self.stats.cap_hits += 1
        self.stats.max_iterations = max(self.stats.max_iterations, iteration)
        return iteration

    def _start_flow(self, lk: Link) -> float:
        """First guess for a link that had no flow: a small flow the way its heads drive."""
        if isinstance(lk, Pump):
            return max(lk.speed, 0.1) * 1e-3
        if isinstance(lk, _DemandLink):
            return lk.junction.demand  # type: ignore[union-attr]
        drop = self._end_head(lk.start) - self._end_head(lk.end)  # type: ignore[arg-type]
        if not math.isfinite(drop) or drop == 0:
            return 1e-4
        if isinstance(lk, Resistance) and lk.r > 0:
            q = math.sqrt(abs(drop) / lk.r)
            return math.copysign(q, drop)
        return math.copysign(1e-4, drop)

    # -- integrating ------------------------------------------------------------------------

    def _rates(self) -> dict[int, float]:
        """Net inflow (m³/s) of every tank and fixed node, from the current flows."""
        rate: dict[int, float] = {id(n): 0.0 for n in [*self.tanks, *self.fixed]}
        for lk in [*self.links, *self._demand_links.values()]:
            if lk.flow == 0.0:
                continue
            a, b = lk.start[0], lk.end[0]  # type: ignore[index]
            if id(a) in rate:
                rate[id(a)] -= lk.flow
            if id(b) in rate:
                rate[id(b)] += lk.flow
        return rate

    def _next_event(
        self, rate: dict[int, float], horizon: float
    ) -> tuple[float, Tank, float] | None:
        """The first moment within ``horizon`` a tank's level falls to a nozzle it is drawing
        out of: (time, tank, nozzle height)."""
        best: tuple[float, Tank, float] | None = None
        for lk in self.links:
            for end, outward in ((lk.start, lk.flow > 0), (lk.end, lk.flow < 0)):
                node, nozzle = end  # type: ignore[misc]
                if not (outward and isinstance(node, Tank)):
                    continue
                q = rate[id(node)]
                height = node.nozzles[nozzle]  # type: ignore[index]
                if q >= 0 or node.level <= height + LEVEL_EPS:
                    continue
                t = (node.volume - node.area * height) / -q
                if t < horizon and (best is None or t < best[0]):
                    best = (t, node, height)
        return best

    def _mix(self) -> dict[int, tuple[float, dict[str, float]]]:
        """Temperature and fractions of what each junction passes on: the flow-weighted mix of
        what enters it (a few passes, in order, for junctions fed by junctions)."""
        out: dict[int, tuple[float, dict[str, float]]] = {
            id(j): (j.temperature, dict(j.fractions)) for j in self.junctions
        }

        def source(end: tuple[Node, str | None]) -> tuple[float, dict[str, float]]:
            node = end[0]
            if isinstance(node, Junction):
                return out[id(node)]
            return node.temperature, node.fractions

        links = self.links
        for _ in range(len(self.junctions) + 1):
            for j in self.junctions:
                q_sum, heat, frac = 0.0, 0.0, {}  # type: ignore[var-annotated]
                for lk in links:
                    if lk.end[0] is j and lk.flow > 0:  # type: ignore[index]
                        q, (t, fr) = lk.flow, source(lk.start)  # type: ignore[arg-type]
                    elif lk.start[0] is j and lk.flow < 0:  # type: ignore[index]
                        q, (t, fr) = -lk.flow, source(lk.end)  # type: ignore[arg-type]
                    else:
                        continue
                    q_sum += q
                    heat += q * t
                    for name, x in fr.items():
                        frac[name] = frac.get(name, 0.0) + q * x
                if q_sum > 0:
                    out[id(j)] = (heat / q_sum, {k: v / q_sum for k, v in frac.items()})
        for j in self.junctions:
            j.temperature, j.fractions = out[id(j)]
        return out

    def _advance(self, t: float, rate: dict[int, float]) -> None:
        """Move every tank and fixed node ``t`` seconds along the current flows."""
        if t <= 0:
            return
        mixed = self._mix()

        def source(end: tuple[Node, str | None]) -> tuple[float, dict[str, float]]:
            node = end[0]
            if isinstance(node, Junction):
                return mixed[id(node)]
            return node.temperature, node.fractions

        inflow: dict[int, list[tuple[float, float, dict[str, float]]]] = {
            id(k): [] for k in self.tanks
        }
        for lk in self.links:
            if lk.flow > 0 and id(lk.end[0]) in inflow:  # type: ignore[index]
                inflow[id(lk.end[0])].append((lk.flow, *source(lk.start)))  # type: ignore[arg-type,index]
            elif lk.flow < 0 and id(lk.start[0]) in inflow:  # type: ignore[index]
                inflow[id(lk.start[0])].append((-lk.flow, *source(lk.end)))  # type: ignore[arg-type,index]
        for tank in self.tanks:
            q_net = rate[id(tank)]
            q_in = sum(q for q, _, _ in inflow[id(tank)])
            q_out = q_in - q_net
            old = tank.volume
            new = old + q_net * t
            heat = old * tank.temperature - q_out * t * tank.temperature
            heat += sum(q * t * temp for q, temp, _ in inflow[id(tank)])
            power = tank.heat_w + sum(tank.heat_sources.values())
            heat += power * t / (DENSITY * SPECIFIC_HEAT)
            frac = {k: old * v - q_out * t * v for k, v in tank.fractions.items()}
            for q, _, fr in inflow[id(tank)]:
                for k, v in fr.items():
                    frac[k] = frac.get(k, 0.0) + q * t * v
            if new < 0.0:  # only past the split cap: the balance no longer closes exactly
                new = 0.0
            if new > 1e-12:
                tank.temperature = heat / new
                tank.fractions = {k: max(v / new, 0.0) for k, v in frac.items()}
            if new > tank.capacity:
                tank.overflow += new - tank.capacity
                new = tank.capacity
            tank.volume = new
        for node in self.fixed:
            q = rate[id(node)] * t
            if q > 0:
                node.received += q
            else:
                node.delivered -= q
        for link in self._demand_links.values():
            if link.flow > 0:
                link.junction.received += link.flow * t  # type: ignore[union-attr]

    def step(self, dt: float) -> None:
        """Advance the network ``dt`` seconds (module docstring, "Integration")."""
        remaining, splits = dt, 0
        while True:
            self.solve()
            rate = self._rates()
            event = self._next_event(rate, remaining) if splits < MAX_SPLITS else None
            t = event[0] if event else remaining
            self._advance(t, rate)
            remaining -= t
            if event is None or remaining <= 0:
                if (
                    event is None
                    and splits == MAX_SPLITS
                    and self._next_event(rate, 0.0) is not None
                ):
                    self.stats.split_cap_hits += 1
                break
            _, tank, height = event
            tank.volume = tank.area * height  # land exactly on the nozzle (rounding only)
            splits += 1
            self.stats.splits += 1

    # -- reading --------------------------------------------------------------------------------

    def total_volume(self) -> float:
        return sum(t.volume for t in self.tanks)


def _solve_dense(a: list[list[float]], b: list[float]) -> list[float]:
    """Solve ``a·x = b`` by Gaussian elimination with partial pivoting (``a`` and ``b`` are
    consumed)."""
    n = len(b)
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if a[pivot][col] == 0.0:
            raise FlowNetError("flow network: singular head equations")
        if pivot != col:
            a[col], a[pivot] = a[pivot], a[col]
            b[col], b[pivot] = b[pivot], b[col]
        inv = 1.0 / a[col][col]
        for r in range(col + 1, n):
            factor = a[r][col] * inv
            if factor:
                row, prow = a[r], a[col]
                for c in range(col, n):
                    row[c] -= factor * prow[c]
                b[r] -= factor * b[col]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        s = b[r] - sum(a[r][c] * x[c] for c in range(r + 1, n))
        x[r] = s / a[r][r]
    return x
