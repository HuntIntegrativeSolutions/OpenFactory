"""``openfactory layout`` (PLAN 5.5; design note §7): positions for what a drawing did not place.

Behavior spec
-------------
:func:`lay_out` takes the plant's items (each with a position from a drawing, or none) and its
pipes, and gives every item with no position a position and heading, and every free pipe an
origin and right-angle ``path`` points. It runs in Python with no browser, and it is
deterministic: lists are walked in the order given (a takeoff's row order or a scene's component
order), no set order decides anything, and numbers are rounded to 0.1 mm, so the same input
gives the same positions byte for byte. **It never moves an item that has a position**; that
item's body is an obstacle.

- **Process equipment** (supplies, drains, junctions, tanks, valves, pumps) goes on one line
  along +x in flow order: an item's rank is the longest chain of connections from a supply to
  it (supplies first, drains last), equal ranks in the order given, bodies :data:`GAP_M` clear.
- **Elevation**: a tank stands :data:`TANK_Y_M` up, so its bottom nozzle (drawn 0.05 m up) is on
  the low line at :data:`LINE_Y_M`, where pumps are. A valve, supply, drain or junction whose
  connections lead (through links) into one tank's top, and to no pump or low nozzle, is on that
  tank's high line, :data:`HIGH_ABOVE_M` above its top; otherwise on the low line.
- **Instruments** beside what they measure (a level transmitter outside its tank's wall at half
  its height, a flow transmitter :data:`IN_FRONT_M` in front of its link); a heater in its tank,
  a drive behind its load.
- **Conveyors** end to end along their connections, one row per chain, :data:`BAND_GAP_M` behind
  the process line; **devices on a belt** spread on a belt's left rail (``place_on_rail``), the
  belt whose tag shares the device's loop number, else the first; **an emitter** at the infeed
  of a belt nothing feeds, **a remover** past the discharge of a belt that feeds nothing.
- **Anything else** in a row behind the conveyors, standing on the floor.
- **Pipes**: a pipe from a link starts at its own origin, put on the link's line :data:`JOIN_M`
  downstream of it; a low nozzle gets a stub along the line so it faces along it. The route is
  the first of: a straight run, the one-axis-at-a-time routes in the axis orders x-y-z, x-z-y,
  y-x-z, y-z-x, z-x-y, z-y-x, over everything, in front of everything, whose runs pass through no
  body but its ends' (:mod:`openfactory.core.footprints`). None clear: the straight line, and a
  note.

With items already placed, the free items start :data:`BAND_GAP_M` past the largest x of the
placed ones. A pipe's default length is its drawn length, so a routed pipe has a new length and
friction, and elevation changes heads: a laid-out plant's flows are not the drawn plant's.

Not a safety-rated simulation. OpenFactory is a training and demo tool.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from openfactory.core import footprints as fp
from openfactory.core.geometry import rotate_y

Vec3 = tuple[float, float, float]

#: Clear space between two bodies along a line or a row, m.
GAP_M = 1.0
#: Between the process line, the conveyor rows and the row of anything else, m.
BAND_GAP_M = 2.0
#: How high a laid-out tank stands, m: its bottom nozzle (0.05 m up) is then on the low line.
TANK_Y_M = 0.25
#: The low line, m: pumps, and the lines that join a tank's bottom.
LINE_Y_M = 0.3
#: A high line is this far above the top of the tank it fills, m.
HIGH_ABOVE_M = 0.3
#: A pipe from a link starts this far downstream of the link's origin, clear of its body, m.
JOIN_M = 0.35
#: A low nozzle's stub, out from the wall along the line, m.
STUB_M = 0.3
#: A flow transmitter stands this far in front of (scene -z) the link it measures, m.
IN_FRONT_M = 0.45
#: A level transmitter stands this far outside its tank's wall, m.
OUTSIDE_M = 0.15
#: A second instrument on the same item steps out this much further, m; and one whose spot is
#: taken steps out by this until it is clear, at most :data:`MAX_STEPS` times.
STEP_M = 0.2
MAX_STEPS = 50

NODE_TYPES = ("tank", "supply", "drain", "junction")
LINK_TYPES = ("valve_onoff", "valve_control", "pump")
PROCESS = (*NODE_TYPES, *LINK_TYPES)
BELTS = ("belt_conveyor", "roller_conveyor")
CONVEYORS = (*BELTS, "turntable", "lift")
RAIL_DEVICES = ("photo_eye", "distance_sensor", "color_sensor", "pusher", "stopper", "diverter_arm")


def _num(v: float) -> float:
    r = round(float(v), 4)
    return 0.0 if r == 0 else r


def _vec(v: Any) -> Vec3:
    return (_num(v[0]), _num(v[1]), _num(v[2]))


@dataclass
class Item:
    """One component to place or to avoid."""

    id: str
    type: str
    cfg: Any  # what footprints and place_on_rail read: the type's fields and its parameters
    tag: str = ""
    position: Vec3 | None = None
    heading: float = 0.0
    from_: str | None = None
    to: str | None = None
    #: A stand-in's or composite's body at its origin, heading 0 (else from footprints).
    local: list[fp.Box] | None = None
    #: A composite's body where its parts are, exact, used while it stands at ``position``.
    world: list[fp.Box] | None = None

    @property
    def fixed(self) -> bool:
        return self.position is not None

    def target(self) -> str | None:
        for key in ("measures", "tank", "drives"):
            value = getattr(self.cfg, key, None)
            if isinstance(value, str) and value:
                return value.partition(".")[0]
        return None


@dataclass
class Pipe:
    """A pipe to route (or, with ``fixed``, one whose route is the drawing's)."""

    id: str
    from_: str
    to: str
    diameter: float = 0.05
    fixed: bool = False
    position: Vec3 | None = None
    path: list[Vec3] = field(default_factory=list)


@dataclass
class Placed:
    position: Vec3
    heading: float = 0.0
    path: list[Vec3] | None = None
    note: str = ""


@dataclass
class Result:
    placed: dict[str, Placed]
    notes: list[str]


def body(item: Item, position: Vec3, heading: float) -> list[fp.Box]:
    """The item's body at ``position``, turned by ``heading``."""
    if item.world is not None and position == item.position and heading == item.heading:
        return list(item.world)
    if item.local is not None:
        x, y, z = position
        out = []
        for b in item.local:
            x0, x1, y0, y1, z0, z1 = fp.turned(b, heading)
            out.append((x + x0, x + x1, y + y0, y + y1, z + z0, z + z1))
        return out
    cfg = SimpleNamespace(**{**vars(item.cfg), "position": list(position), "rotation_deg": heading})
    return fp.boxes(cfg, item.type)


def _local_bounds(item: Item) -> fp.Box:
    b = fp.bounds(body(item, (0.0, 0.0, 0.0), 0.0))
    return b if b is not None else (-0.1, 0.1, 0.0, 0.2, -0.1, 0.1)


def loop_number(tag: str) -> str | None:
    """The tag's loop number, its first run of digits (``CV-201`` -> ``201``)."""
    m = re.search(r"\d+", tag)
    return m.group(0) if m else None


def _base(ref: str | None) -> str:
    return (ref or "").partition(".")[0]


def _nozzle(ref: str | None) -> str:
    return (ref or "").partition(".")[2]


def nozzle_height(tank: Item, name: str) -> float:
    cfg = tank.cfg
    if name in ("", "bottom"):
        return 0.0
    if name == "top":
        return float(cfg.height)
    spec = (getattr(cfg, "nozzles", None) or {}).get(name)
    if isinstance(spec, dict):
        return float(spec.get("height", 0.0))
    if spec is not None and not isinstance(spec, int | float):
        return float(spec.height)
    return float(spec or 0.0)


def _bearing(tank: Item, name: str) -> float | None:
    spec = (getattr(tank.cfg, "nozzles", None) or {}).get(name)
    if isinstance(spec, dict):
        return spec.get("bearing_deg")
    return getattr(spec, "bearing_deg", None)


def _is_low(tank: Item, name: str) -> bool:
    return not (name == "top" or nozzle_height(tank, name) >= float(tank.cfg.height) - 1e-9)


class _Plant:
    """The items and pipes, and what joins what."""

    def __init__(self, items: list[Item], pipes: list[Pipe]) -> None:
        self.items = items
        self.by_id = {i.id: i for i in items}
        self.pipes = pipes
        self.pipe_by_id = {p.id: p for p in pipes}
        # (a, a's nozzle, b, b's nozzle): a joint between two process items, through pipes
        self.edges: list[tuple[str, str, str, str]] = []
        for i in items:
            if i.type in LINK_TYPES:
                if i.from_:
                    self._edge(i.from_, i.id)
                if i.to:
                    self._edge(i.id, i.to)
        for p in pipes:
            if p.from_ in self.by_id or p.from_.partition(".")[0] in self.by_id:
                self._edge(p.from_, p.to)

    def _far(self, ref: str, forward: bool, depth: int = 0) -> str | None:
        """A reference through any pipes to the item at its end."""
        base = _base(ref)
        if base in self.by_id:
            return ref
        pipe = self.pipe_by_id.get(base)
        if pipe is None or depth > len(self.pipes):
            return None
        return self._far(pipe.to if forward else pipe.from_, forward, depth + 1)

    def _edge(self, a: str, b: str) -> None:
        fa, fb = self._far(a, False), self._far(b, True)
        if fa is not None and fb is not None:
            self.edges.append((_base(fa), _nozzle(fa), _base(fb), _nozzle(fb)))

    def flow_order(self, free: list[Item]) -> list[Item]:
        """``free`` in flow order along the connections, depth first so each branch stays
        together (review fix 1e, 2026-11-19: by rank alone, two tanks off one junction stood
        side by side and the first's pump ran through the second): supplies first in row order;
        then, of the items whose upstream items are all placed, a successor of the latest
        placed item that has one, else the first in row order; drains last in row order. A
        loop with nothing ready takes the first remaining item in row order."""
        ids = [i.id for i in free]
        ups: dict[str, list[str]] = {i: [] for i in ids}
        downs: dict[str, list[str]] = {i: [] for i in ids}
        for a, _, b, _ in self.edges:
            if a in ups and b in ups and a != b and self.by_id[b].type != "supply":
                if a not in ups[b]:
                    ups[b].append(a)
                if b not in downs[a]:
                    downs[a].append(b)
        drains = [i for i in ids if self.by_id[i].type == "drain"]
        done: list[str] = [i for i in ids if self.by_id[i].type == "supply"]
        left = [i for i in ids if i not in done and i not in drains]
        while left:
            ready = [i for i in left if all(u in done or u in drains for u in ups[i])]
            pick = None
            for last in reversed(done):
                pick = next((d for d in downs[last] if d in ready), None)
                if pick is not None:
                    break
            if pick is None:
                pick = ready[0] if ready else left[0]
            done.append(pick)
            left.remove(pick)
        by = {i.id: i for i in free}
        return [by[i] for i in done + drains]

    def line_group(self, start: str) -> tuple[list[tuple[str, str]], bool]:
        """From ``start``, through links, junctions, supplies and drains: the tank ends reached
        (tank, nozzle) and whether a pump is on the way."""
        seen, queue, tanks, pump = {start}, [start], [], False
        while queue:
            here = queue.pop(0)
            if self.by_id[here].type == "pump":
                pump = True
            for a, na, b, nb in self.edges:
                for me, other, n_other in ((a, b, nb), (b, a, na)):
                    if me != here or other not in self.by_id:
                        continue
                    if self.by_id[other].type == "tank":
                        if (other, n_other) not in tanks:
                            tanks.append((other, n_other))
                    elif other not in seen:
                        seen.add(other)
                        queue.append(other)
        return tanks, pump


def lay_out(  # noqa: C901, PLR0912, PLR0915 - one pass per kind of item, in order
    items: list[Item],
    pipes: list[Pipe],
    conveyor_links: list[tuple[str, str]] | None = None,
    tallest_part: float | None = None,
) -> Result:
    """Positions for every item with none and routes for every pipe not ``fixed`` (see the
    module docstring)."""
    from openfactory.core.mounts import place_on_rail

    links = conveyor_links or []
    plant = _Plant(items, pipes)
    out: dict[str, Placed] = {}
    notes: list[str] = []

    def where(item: Item) -> tuple[Vec3, float] | None:
        if item.position is not None:
            return item.position, item.heading
        if item.id in out:
            return out[item.id].position, out[item.id].heading
        return None

    def bodies() -> list[fp.Box]:
        boxes = []
        for i in items:
            w = where(i)
            if w is not None:
                boxes += body(i, *w)
        return boxes

    def put(item: Item, pos: Vec3, heading: float = 0.0, note: str = "") -> None:
        out[item.id] = Placed(_vec(pos), _num(heading), None, note)

    fixed = [b for i in items if i.fixed for b in body(i, i.position, i.heading)]  # type: ignore[arg-type]
    extent = fp.bounds(fixed)
    x0 = extent[1] + BAND_GAP_M if extent else 0.0
    z0 = extent[4] if extent else 0.0
    free = [i for i in items if not i.fixed]

    # 1. process equipment, in flow order, on one line
    order = plant.flow_order([i for i in free if i.type in PROCESS])

    def tank_top(tank_id: str) -> float:
        t = plant.by_id[tank_id]
        y = t.position[1] if t.position is not None else TANK_Y_M
        return y + float(t.cfg.height)

    def elevation(item: Item) -> float:
        if item.type == "tank":
            return TANK_Y_M
        if item.type == "pump":
            return LINE_Y_M
        tanks, pump = plant.line_group(item.id)
        names = {t for t, _ in tanks}
        tops = all(not _is_low(plant.by_id[t], n) for t, n in tanks)
        if tanks and tops and not pump and len(names) == 1:
            return tank_top(tanks[0][0]) + HIGH_ABOVE_M
        return LINE_Y_M

    cursor = x0
    for item in order:
        lx0, lx1, *_ = _local_bounds(item)
        x = cursor - lx0
        put(item, (x, elevation(item), z0), 0.0, "process line")
        cursor = x + lx1 + GAP_M

    # 2. pipes (they must be routed before instruments that measure them)
    for pipe in pipes:
        if pipe.fixed:
            continue
        routed = _route(plant, pipe, where)
        if routed is None:
            notes.append(f"{pipe.id}: no clear route; drawn straight")
            _, _, pos = _ends(plant, pipe, where)
            out[pipe.id] = Placed(_vec(pos), 0.0, [], "no clear route")
        else:
            pos, path = routed
            out[pipe.id] = Placed(_vec(pos), 0.0, [_vec(p) for p in path], "routed")

    # 3. instruments, heaters and drives beside what they serve
    count: dict[str, int] = {}
    for item in free:
        if item.id in out or item.type not in ("transmitter", "heater", "vfd_motor"):
            continue
        target = item.target()
        at = None
        if target in plant.by_id:
            at = where(plant.by_id[target])
        elif target in out:
            at = (out[target].position, 0.0)
        if at is None:
            continue
        k = count.get(target or "", 0)
        count[target or ""] = k + 1
        (tx, ty, tz), _ = at
        tgt = plant.by_id.get(target or "")
        if item.type == "heater" and tgt is not None and tgt.type == "tank":
            put(item, (tx, ty + 0.1, tz), 0.0, f"in {target}")
            continue
        if item.type == "vfd_motor":
            spot, step, note = (tx, 0.0, tz + 0.8 + STEP_M * k), (0.0, 0.0, STEP_M), "behind"
        elif tgt is not None and tgt.type == "tank":
            wall = fp.bounds(body(tgt, *where(tgt)))  # type: ignore[misc]
            assert wall is not None
            x = wall[1] + OUTSIDE_M + 0.06 + STEP_M * k
            spot, step = (x, ty + float(tgt.cfg.height) / 2, tz), (STEP_M, 0.0, 0.0)
            note = "beside"
        else:
            spot, step = (tx, ty, tz - IN_FRONT_M - STEP_M * k), (0.0, 0.0, -STEP_M)
            note = "in front of"
        # ruled 2026-11-19 (second set): step out, the same way, until clear of every body but
        # what it serves; bounded, and what stays blocked is the overlap check's to report
        for _ in range(MAX_STEPS):
            mine = body(item, spot, 0.0)
            others = [b for i in items if i.id not in (item.id, target) and where(i) is not None
                      for b in body(i, *where(i))]  # type: ignore[misc]  # fmt: skip
            if not any(fp.overlap(m, o) for m in mine for o in others):
                break
            spot = (spot[0] + step[0], spot[1] + step[1], spot[2] + step[2])
        put(item, spot, 0.0, f"{note} {target}")

    # 4. conveyors, end to end along their connections
    def length(i: Item) -> float:
        return float(i.cfg.length)

    def ends(i: Item, pos: Vec3, heading: float) -> tuple[Vec3, Vec3]:
        """Infeed and discharge points of a conveyor at ``pos``."""
        back = -length(i) / 2 if i.type == "turntable" else 0.0
        a = rotate_y((back, 0.0, 0.0), heading)
        b = rotate_y((back + length(i), 0.0, 0.0), heading)
        return (
            (pos[0] + a[0], pos[1], pos[2] + a[2]),
            (pos[0] + b[0], pos[1], pos[2] + b[2]),
        )

    def origin_for_infeed(i: Item, infeed: Vec3, heading: float) -> Vec3:
        back = -length(i) / 2 if i.type == "turntable" else 0.0
        a = rotate_y((back, 0.0, 0.0), heading)
        return (infeed[0] - a[0], infeed[1], infeed[2] - a[2])

    def row_z(local_z0: float) -> float:
        b = fp.bounds(bodies())
        return (b[5] if b else z0) + BAND_GAP_M - local_z0

    belts = [i for i in free if i.type in CONVEYORS]
    for start in belts:
        if start.id in out:
            continue
        ups = [a for a, b in links if b == start.id and a in plant.by_id]
        downs = [b for a, b in links if a == start.id and b in plant.by_id]
        up = next((plant.by_id[a] for a in ups if where(plant.by_id[a])), None)
        down = next((plant.by_id[b] for b in downs if where(plant.by_id[b])), None)
        if up is not None:
            pos, heading = where(up)  # type: ignore[misc]
            put(start, origin_for_infeed(start, ends(up, pos, heading)[1], heading), heading,
                f"after {up.id}")  # fmt: skip
        elif down is not None:
            pos, heading = where(down)  # type: ignore[misc]
            infeed = ends(down, pos, heading)[0]
            back = rotate_y((length(start), 0.0, 0.0), heading)
            at = (infeed[0] - back[0], infeed[1], infeed[2] - back[2])
            put(start, origin_for_infeed(start, at, heading), heading, f"before {down.id}")
        else:
            lb = _local_bounds(start)
            put(start, origin_for_infeed(start, (x0, 0.0, row_z(lb[4])), 0.0), 0.0, "conveyor row")
        chain = start
        while True:  # follow the chain downstream
            nxt = next(
                (plant.by_id[b] for a, b in links
                 if a == chain.id and b in plant.by_id and where(plant.by_id[b]) is None),
                None,
            )  # fmt: skip
            if nxt is None:
                break
            pos, heading = where(chain)  # type: ignore[misc]
            put(nxt, origin_for_infeed(nxt, ends(chain, pos, heading)[1], heading), heading,
                f"after {chain.id}")  # fmt: skip
            chain = nxt

    # 5. devices on a belt's rail, emitters and removers at its ends
    rail_belts = [i for i in items if i.type in BELTS and where(i) is not None]

    def belt_for(item: Item, among: list[Item]) -> Item | None:
        loop = loop_number(item.tag)
        same = [b for b in among if loop is not None and loop_number(b.tag) == loop]
        return (same or among or [None])[0]

    def conveyor_ns(b: Item) -> Any:
        pos, heading = where(b)  # type: ignore[misc]
        return SimpleNamespace(id=b.id, type=b.type, position=list(pos), rotation_deg=heading,
                               length=float(b.cfg.length), width=float(b.cfg.width))  # fmt: skip

    on: dict[str, list[Item]] = {}
    for item in free:
        if item.type in RAIL_DEVICES and item.id not in out:
            belt = belt_for(item, rail_belts)
            if belt is not None:
                on.setdefault(belt.id, []).append(item)
    for belt_id, devices in on.items():
        belt = plant.by_id[belt_id]
        for k, item in enumerate(devices):
            along = length(belt) * (k + 1) / (len(devices) + 1)
            params = {
                n: v for n, v in vars(item.cfg).items() if n not in ("position", "rotation_deg")
            }
            done = place_on_rail(item.type, params, conveyor_ns(belt), "rail_left", along,
                                 tallest_part)  # fmt: skip
            put(item, tuple(done.position), done.rotation_deg,  # type: ignore[arg-type]
                f"on {belt_id}'s left rail, {_num(along)} m from its infeed")  # fmt: skip
    fed = {b for _, b in links}
    feeding = {a for a, _ in links}
    infeeds = [b for b in rail_belts if b.id not in fed]
    discharges = [b for b in rail_belts if b.id not in feeding]
    for item in free:
        if item.id in out or item.type not in ("box_emitter", "part_remover"):
            continue
        among = infeeds if item.type == "box_emitter" else discharges
        belt = belt_for(item, among)
        if belt is None:
            continue
        among.remove(belt)
        pos, heading = where(belt)  # type: ignore[misc]
        if item.type == "box_emitter":
            sizes = [p.size for p in getattr(item.cfg, "parts", None) or []]
            deep = max((s[0] for s in sizes), default=0.3)
            tall = max((s[1] for s in sizes), default=0.3)
            local = (deep, tall / 2 + 0.005, 0.0)
            note = f"at {belt.id}'s infeed"
        else:
            local = (length(belt) + float(item.cfg.size[0]) / 2, 0.0, 0.0)
            note = f"past {belt.id}'s discharge"
        d = rotate_y(local, heading)
        put(item, (pos[0] + d[0], pos[1] + d[1], pos[2] + d[2]), heading, note)

    # 6. anything else, in a row on the floor
    rest = [i for i in free if i.id not in out]
    if rest:
        z = row_z(min(_local_bounds(i)[4] for i in rest))
        cursor = x0
        for item in rest:
            lx0, lx1, ly0, *_ = _local_bounds(item)
            x = cursor - lx0
            put(item, (x, -ly0, z), 0.0, "in the row of other items")
            cursor = x + lx1 + GAP_M
    return Result(out, notes)


# --- pipe routes -------------------------------------------------------------------------------


def _point_of(plant: _Plant, ref: str, where: Any, toward_x: float) -> tuple[Vec3, Vec3 | None]:
    """Where a pipe meets ``ref``, and for a low nozzle the stub's outer point, which the
    nozzle then faces (along x, toward ``toward_x``)."""
    item = plant.by_id[_base(ref)]
    (x, y, z), heading = where(item)
    name = _nozzle(ref)
    if item.type != "tank" or not name:
        return (x, y, z), None
    h = nozzle_height(item, name)
    if not _is_low(item, name):
        return (x, y + h, z), None
    bearing = _bearing(item, name)
    if bearing is not None:  # a fixed bearing: on the wall there (core/snaps.nozzle_wall)
        lx, _, lz = rotate_y((1.0, 0.0, 0.0), float(heading) + float(bearing))
        r = float(item.cfg.diameter) / 2 if getattr(item.cfg, "shape", "") != "box" else min(
            float(item.cfg.length), float(item.cfg.width)) / 2  # fmt: skip
        wall = (x + lx * r, y + max(h, 0.05), z + lz * r)
        stub = (x + lx * (r + STUB_M), wall[1], z + lz * (r + STUB_M))
        return wall, stub
    sign = 1.0 if toward_x >= x else -1.0
    if getattr(item.cfg, "shape", "") == "box":
        r = min(float(item.cfg.length), float(item.cfg.width)) / 2
    else:
        r = float(item.cfg.diameter) / 2
    wall = (x + sign * r, y + max(h, 0.05), z)
    return wall, (x + sign * (r + STUB_M), wall[1], z)


def _ends(plant: _Plant, pipe: Pipe, where: Any) -> tuple[Vec3, Vec3, Vec3]:
    """The pipe's start, its end and its origin, for a straight line."""
    a = plant.by_id.get(_base(pipe.from_))
    b = plant.by_id.get(_base(pipe.to))
    bx = where(b)[0][0] if b is not None else 0.0
    if a is not None and a.type in LINK_TYPES:
        (ax, ay, az), _ = where(a)
        sign = 1.0 if bx >= ax else -1.0
        start = (ax + sign * JOIN_M, ay, az)
        pos = start
    else:
        start, _ = _point_of(plant, pipe.from_, where, bx)
        pos = start
    ax0 = start[0]
    end = where(b)[0] if b is not None and b.type in LINK_TYPES else _point_of(
        plant, pipe.to, where, ax0)[0]  # fmt: skip
    return start, end, pos


def _orthogonal(a: Vec3, b: Vec3) -> list[list[Vec3]]:
    """The routes from ``a`` to ``b`` that move along one axis at a time, in a fixed order."""
    routes = []
    for order in ((0, 1, 2), (0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0)):
        p = list(a)
        pts = [tuple(p)]
        for axis in order:
            if abs(p[axis] - b[axis]) > 1e-9:
                p[axis] = b[axis]
                pts.append(tuple(p))
        if pts not in routes:
            routes.append(pts)  # type: ignore[arg-type]
    return routes  # type: ignore[return-value]


def _route(plant: _Plant, pipe: Pipe, where: Any) -> tuple[Vec3, list[Vec3]] | None:
    """The pipe's origin and its ``path`` points, or None when no candidate is clear."""
    a = plant.by_id.get(_base(pipe.from_))
    b = plant.by_id.get(_base(pipe.to))
    if a is None or b is None or where(a) is None or where(b) is None:
        return None
    bx = where(b)[0][0]
    if a.type in LINK_TYPES:
        (ax, ay, az), _ = where(a)
        sign = 1.0 if bx >= ax else -1.0
        start: Vec3 = (ax + sign * JOIN_M, ay, az)
        pos, head = start, [start]
    else:
        start, stub = _point_of(plant, pipe.from_, where, bx)
        pos, head = start, [start] + ([stub] if stub else [])
    if b.type in LINK_TYPES:
        end, tail = where(b)[0], [where(b)[0]]
    else:
        end, stub = _point_of(plant, pipe.to, where, head[-1][0])
        tail = ([stub] if stub else []) + [end]
    radius = max(pipe.diameter / 2, 0.015)
    ends = {a.id, b.id}
    obstacles = [
        box for i in plant.items if i.id not in ends and where(i) is not None
        for box in body(i, *where(i))
    ]  # fmt: skip

    def clear(points: list[Vec3]) -> bool:
        runs = fp.runs(points, radius)
        return not any(fp.overlap(r, o) for r in runs for o in obstacles)

    p, q = head[-1], tail[0]
    candidates = _orthogonal(p, q)
    span = (min(p[0], q[0]) - 0.3, max(p[0], q[0]) + 0.3, min(p[2], q[2]) - 0.3,
            max(p[2], q[2]) + 0.3)  # fmt: skip
    over = [o for o in obstacles if o[1] > span[0] and o[0] < span[1] and o[5] > span[2]
            and o[4] < span[3]]  # fmt: skip
    top = max([o[3] for o in over] + [p[1], q[1]]) + HIGH_ABOVE_M
    candidates.append([p, (p[0], top, p[2]), (q[0], top, p[2]), (q[0], top, q[2]), q])
    front = min([o[4] for o in over] + [p[2], q[2]]) - IN_FRONT_M
    candidates.append([p, (p[0], p[1], front), (q[0], p[1], front), (q[0], q[1], front), q])
    for route in candidates:
        points = [*head[:-1], *_dedupe(route), *tail[1:]]
        if clear(points):
            return pos, points[1:-1]
    return None


def _dedupe(points: list[Vec3]) -> list[Vec3]:
    out: list[Vec3] = []
    for p in points:
        if not out or math.dist(out[-1], p) > 1e-9:
            out.append(p)
    return out


# --- checking what it placed (ruled 2026-11-19, second set, ruling 4) --------------------------


def placed_overlaps(text: str, base_dir: Any, placed: Any, source: Any = None) -> list[str]:
    """W217 over the scene ``text``, every pair with at least one side among ``placed`` (ids of
    components or composite instances layout placed in this run): what it placed against
    everything. Each finding as the lint words it, ``W217 ...``."""
    from openfactory.core.loader import build
    from openfactory.core.schema import parse_scene
    from openfactory.lint import overlaps

    ids = set(placed)
    scene = parse_scene(text, base_dir, source=source) if source else parse_scene(text, base_dir)
    loaded = build(scene, base_dir=base_dir)

    def named(cid: str) -> str:
        inst = scene.composite_of(cid)
        return f"{cid} (part of {inst}, a {scene.instance_type(inst)})" if inst else cid

    def mine(cid: str) -> bool:
        return (scene.composite_of(cid) or cid) in ids

    return [f"W217 {m}" for m in overlaps(loaded.components, named, mine)]


# --- a scene: re-lay what is marked laid out ---------------------------------------------------


def _scene_cfg(comp: Any) -> Any:
    return SimpleNamespace(**{n: getattr(comp, n) for n in type(comp).model_fields})


@dataclass
class SceneItems:
    """A scene file read for layout: its own component entries, and what :func:`lay_out` takes."""

    entries: list[dict[str, Any]]
    items: list[Item]
    pipes: list[Pipe]
    links: list[tuple[str, str]]
    tallest: float | None
    #: Marked devices standing on a belt: placed again on it after it is (not by lay_out).
    followers: list[Follower] = field(default_factory=list)


@dataclass
class Follower:
    """A marked device on a belt (ruled 2026-11-19, second set, ruling 1): it keeps its rail and
    its distance from the belt's infeed, read back from where it stands, and follows the belt as
    a rigid body; it is never spread again."""

    item: Item
    belt: str
    rail: str
    along: float
    rise: float  # its height above the belt top
    offset: Vec3  # its origin in the belt's frame (a composite, moved rigidly)
    turn: float  # its heading less the belt's


def _on_belt(item: Item, pos: Vec3, heading: float, belts: list[tuple[Item, Vec3, float]]
             ) -> Follower | None:  # fmt: skip
    """The belt ``item`` stands on, by W214's rule (``core/mounts._belt_under``), as a
    :class:`Follower`; None when it stands on none."""
    from openfactory.core.geometry import to_local
    from openfactory.core.mounts import _belt_under

    spans = [SimpleNamespace(id=b.id, position=list(p), rotation_deg=h, length=float(b.cfg.length),
                             width=float(b.cfg.width)) for b, p, h in belts]  # fmt: skip
    under = _belt_under(pos, spans)
    if under is None:
        return None
    lx, ly, lz = to_local(tuple(under.position), under.rotation_deg, pos)
    rail = "rail_right" if lz > 1e-6 else "rail_left"  # an eye or stopper on the centre: either
    turn = heading - under.rotation_deg
    return Follower(item, under.id, rail, _num(lx), _num(ly), (lx, ly, lz), turn)


def scene_items(text: str, base_dir: Any = None, source: Any = None) -> SceneItems:
    """The scene's components as layout items: a component marked ``laid_out: true`` with no
    position (to place), every other one at its position (an obstacle). A plant file (its
    ``source`` path given) adds every included component as an obstacle: layout never moves
    what another file holds (review fix 1d, 2026-11-19)."""
    import yaml as _yaml

    from openfactory.core.schema import COMPONENT_MODELS, parse_scene
    from openfactory.core.sizing import tallest_part

    raw = _yaml.safe_load(text) or {}
    entries = [c for c in raw.get("components") or [] if isinstance(c, dict)]
    scene = parse_scene(text, base_dir, source=source) if source else parse_scene(text, base_dir)
    flat = {c.id: c for c in scene.components}
    items: list[Item] = []
    pipes: list[Pipe] = []
    for entry in entries:
        cid, kind = entry["id"], entry["type"]
        marked = bool(entry.get("laid_out"))
        pos = None if marked else _vec(entry["position"])
        heading = float(entry.get("rotation_deg", 0.0))
        if kind == "pipe":
            comp = flat[cid]
            pipes.append(Pipe(cid, comp.from_, comp.to, float(comp.diameter), not marked,
                              pos, [_vec(p) for p in comp.path]))  # fmt: skip
            continue
        if kind in COMPONENT_MODELS:
            comp = flat[cid]
            items.append(Item(cid, kind, _scene_cfg(comp), cid, pos, heading,
                              getattr(comp, "from_", None), getattr(comp, "to", None)))  # fmt: skip
            continue
        parts = [c for c in scene.components if scene.composite_of(c.id) == cid]
        origin = [float(v) for v in entry["position"]]
        world: list[fp.Box] = []
        local: list[fp.Box] = []
        for part in parts:
            cfg = part.config if hasattr(part, "config") else part
            world += fp.boxes(cfg, part.type)  # where the parts are: the obstacle, exactly
            # the instance's body about its own origin at heading 0 (its parts turned back), so
            # body() turns it once (review fix 1b, 2026-11-19: it was turned twice)
            d = rotate_y(tuple(float(cfg.position[k]) - origin[k] for k in range(3)), -heading)
            back = SimpleNamespace(**{n: getattr(cfg, n) for n in type(cfg).model_fields})
            back.position = list(d)
            back.rotation_deg = float(getattr(cfg, "rotation_deg", 0.0) or 0.0) - heading
            local += fp.boxes(back, part.type)
        items.append(Item(cid, kind, SimpleNamespace(), cid, pos, heading,
                          local=local or None, world=world or None))  # fmt: skip
    own = {e["id"] for e in entries}
    for comp in scene.components:  # a plant's included components: obstacles, never moved
        if comp.id in own or scene.composite_of(comp.id) in own:
            continue
        pos = _vec(comp.position)
        if comp.type == "pipe":
            pipes.append(Pipe(comp.id, comp.from_, comp.to, float(comp.diameter), True, pos,
                              [_vec(p) for p in comp.path]))  # fmt: skip
        else:
            items.append(Item(comp.id, comp.type, _scene_cfg(comp), comp.id, pos,
                              float(comp.rotation_deg), getattr(comp, "from_", None),
                              getattr(comp, "to", None)))  # fmt: skip
    links = [(c.from_, c.to) for c in scene.connections]
    # a marked device on a belt keeps its rail and distance (ruling 1, 2026-11-19 second set)
    olds = {i.id: (i.position, i.heading) for i in items if i.position is not None}
    olds.update({e["id"]: (_vec(e["position"]), float(e.get("rotation_deg", 0.0)))
                 for e in entries})  # fmt: skip
    belts = [(i, *olds[i.id]) for i in items if i.type in BELTS]
    followers = []
    for item in items:
        if item.fixed or not (item.type in RAIL_DEVICES or item.local is not None):
            continue
        follow = _on_belt(item, *olds[item.id], belts)
        if follow is not None:
            followers.append(follow)
    held = {f.item.id for f in followers}
    items = [i for i in items if i.id not in held]
    return SceneItems(entries, items, pipes, links, tallest_part(scene), followers)


def follow_belts(read: SceneItems, result: Result) -> None:
    """Each follower placed again on its belt where the belt now is: a rail device by
    ``place_on_rail`` at its rail and distance (as scaffold placed it), anything else moved with
    the belt as a rigid body."""
    from openfactory.core.mounts import MOUNT_TYPES, place_on_rail

    by_id = {i.id: i for i in read.items}
    for f in read.followers:
        belt = by_id[f.belt]
        spot = result.placed.get(belt.id)
        pos, heading = (spot.position, spot.heading) if spot else (belt.position, belt.heading)
        assert pos is not None
        if f.item.type in MOUNT_TYPES:
            ns = SimpleNamespace(id=belt.id, type=belt.type, position=list(pos),
                                 rotation_deg=heading, length=float(belt.cfg.length),
                                 width=float(belt.cfg.width))  # fmt: skip
            params = {n: v for n, v in vars(f.item.cfg).items() if n != "rotation_deg"}
            params["position"] = [0.0, pos[1] + f.rise, 0.0]  # the height it keeps
            done = place_on_rail(f.item.type, params, ns, f.rail, f.along, read.tallest)
            at, turn = tuple(done.position), done.rotation_deg
        else:
            d = rotate_y(f.offset, heading)
            at, turn = (pos[0] + d[0], pos[1] + d[1], pos[2] + d[2]), heading + f.turn
        result.placed[f.item.id] = Placed(_vec(at), _num(turn), None, f"on {belt.id}")  # type: ignore[arg-type]


def relayout_scene(
    text: str, base_dir: Any = None, source: Any = None
) -> tuple[str, list[str], list[str]]:
    """``openfactory layout SCENE``: the scene's text with every component marked
    ``laid_out: true`` placed again (and every such pipe routed again), each by a splice through
    the 4.7 Document writer, so every other byte stays; the ids moved, and layout's notes. A
    component not marked is never moved."""
    from openfactory.core.sceneedit import apply_ops

    read = scene_items(text, base_dir, source)
    entries = read.entries
    result = lay_out(read.items, read.pipes, read.links, read.tallest)
    follow_belts(read, result)
    ops: list[dict[str, Any]] = []
    for entry in entries:
        spot = result.placed.get(entry["id"])
        if spot is None:
            continue
        ops.append({"op": "set", "path": ["components", entry["id"], "position"],
                    "value": list(spot.position)})  # fmt: skip
        if spot.heading or "rotation_deg" in entry:
            ops.append({"op": "set", "path": ["components", entry["id"], "rotation_deg"],
                        "value": spot.heading})  # fmt: skip
        if entry["type"] == "pipe" and (spot.path or "path" in entry):
            ops.append({"op": "set", "path": ["components", entry["id"], "path"],
                        "value": [list(p) for p in spot.path or []]})  # fmt: skip
    new_text, _ = apply_ops(text, ops)
    moved = [e["id"] for e in entries if e["id"] in result.placed]
    return new_text, moved, result.notes
