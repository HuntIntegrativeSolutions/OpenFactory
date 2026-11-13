"""Snap ports (PLAN 4.1d, ruled 2026-11-10 decision 7 and 2026-11-12): where a component can be
joined to another, declared here once and read by the viewer's editor (``/api/scene``), the
lint and the component reference.

Behavior spec
-------------
This module declares ``load`` ports only: the two ends of every conveyor (``belt_conveyor``,
``roller_conveyor``, ``turntable``, ``lift``), at belt-top height. ``pipe`` and ``mount`` ports
are PLAN 4.1g.

A :class:`SnapPort` has a ``name``, its ``kind``, a world ``position`` (m), a ``heading_deg`` (the
way the port faces outward, a rotation about +y as ``rotation_deg``: an end that discharges along
the flow faces along it, an infeed faces back against it), the conveyor's ``width`` and a
``role``: ``out`` (parts leave here), ``in`` (parts arrive here) or ``both`` (a ``bidirectional``
belt's ends, which swap with ``reverse``).

- ``belt_conveyor``, ``roller_conveyor``: ``infeed`` at the origin, ``discharge`` ``length`` along
  local +x.
- ``lift``: the same at the bottom, and ``infeed_top``, ``discharge_top`` ``lift_height`` above.
- ``turntable``: ``infeed`` and ``discharge`` at -length/2 and +length/2 from the deck centre at
  rest, and ``infeed_turned``, ``discharge_turned`` with the deck at ``turn_deg``.

Two ports *face* each other (:func:`facing`) when an ``out`` (or ``both``) port and an ``in`` (or
``both``) port of two different conveyors are within a distance of each other in the floor plane,
level within a height, and face opposite ways within an angle. The editor snaps at
:data:`CAPTURE_M` and aligns to :data:`ALIGN_M` and :data:`ALIGN_DEG`; the lint's W212 (two ends
that meet with no connection, replacing W203, any heading as W203) uses :data:`LINT_M`; W213 (a
connection whose ends do not meet) uses :data:`JOIN_M` and :data:`JOIN_DEG`.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Literal

from openfactory.core.geometry import Vec3, to_world

#: The editor offers a snap when a dragged end comes this close to another (decision 7).
CAPTURE_M = 0.15
#: A snapped join: ends within 1 mm and headings within 0.1 degrees (decision 7).
ALIGN_M = 0.001
ALIGN_DEG = 0.1
#: W212: two ends this close in the floor plane and in height, with no connection.
LINT_M = 0.02
#: W213: a connection's ends further apart than this, or this far out of level, or headings that
#: differ by more than JOIN_DEG, do not meet.
JOIN_M = 0.01
JOIN_DEG = 1.0

Role = Literal["in", "out", "both"]


@dataclass(frozen=True)
class SnapPort:
    name: str
    kind: Literal["load"]
    position: Vec3
    heading_deg: float
    width: float
    role: Role

    def document(self) -> dict[str, Any]:
        """The port as ``/api/scene`` publishes it (rounded to 0.1 mm and 0.001 degrees)."""
        d = asdict(self)
        d["position"] = [round(v, 4) for v in self.position]
        d["heading_deg"] = round(self.heading_deg % 360.0, 3)
        return d


def _ends(
    comp: Any, centre_local_x: tuple[float, float], heading: float, y: float, suffix: str
) -> list[SnapPort]:
    both = bool(getattr(comp, "bidirectional", False))
    origin = (comp.position[0], comp.position[1] + y, comp.position[2])
    a, b = centre_local_x
    return [
        SnapPort(
            f"infeed{suffix}", "load", to_world(origin, heading, (a, 0.0, 0.0)),
            heading + 180.0, comp.width, "both" if both else "in",
        ),
        SnapPort(
            f"discharge{suffix}", "load", to_world(origin, heading, (b, 0.0, 0.0)),
            heading, comp.width, "both" if both else "out",
        ),
    ]  # fmt: skip


def snap_ports(comp: Any) -> list[SnapPort]:
    """The ``load`` ports of a component's configuration (none for a type that has none)."""
    rot = float(comp.rotation_deg)
    if comp.type in ("belt_conveyor", "roller_conveyor"):
        return _ends(comp, (0.0, comp.length), rot, 0.0, "")
    if comp.type == "lift":
        return _ends(comp, (0.0, comp.length), rot, 0.0, "") + _ends(
            comp, (0.0, comp.length), rot, comp.lift_height, "_top"
        )
    if comp.type == "turntable":
        half = (-comp.length / 2, comp.length / 2)
        return _ends(comp, half, rot, 0.0, "") + _ends(
            comp, half, rot + comp.turn_deg, 0.0, "_turned"
        )
    return []


#: Each type's ``load`` port names, as :func:`snap_ports` gives them (the component reference
#: lists them; tests/unit/test_component_reference.py checks).
PORT_NAMES: dict[str, tuple[str, ...]] = {
    "belt_conveyor": ("infeed", "discharge"),
    "roller_conveyor": ("infeed", "discharge"),
    "lift": ("infeed", "discharge", "infeed_top", "discharge_top"),
    "turntable": ("infeed", "discharge", "infeed_turned", "discharge_turned"),
}


def angle_between(a: float, b: float) -> float:
    """The smallest difference between two headings, 0 to 180 degrees."""
    return abs((a - b + 180.0) % 360.0 - 180.0)


@dataclass(frozen=True)
class Gap:
    """How far an ``out`` port is from meeting an ``in`` port."""

    out: SnapPort
    into: SnapPort
    distance_m: float  # in the floor plane
    rise_m: float  # height difference
    turn_deg: float  # how far from facing exactly opposite

    def within(self, distance: float, angle: float) -> bool:
        return self.distance_m <= distance and self.rise_m <= distance and self.turn_deg <= angle


def gap(out: SnapPort, into: SnapPort) -> Gap:
    d = math.hypot(out.position[0] - into.position[0], out.position[2] - into.position[2])
    rise = abs(out.position[1] - into.position[1])
    return Gap(out, into, d, rise, angle_between(out.heading_deg, into.heading_deg + 180.0))


def best_gap(up: Any, down: Any) -> Gap | None:
    """The closest an end of ``up`` that parts leave comes to an end of ``down`` they arrive at,
    or None when either is not a conveyor."""
    outs = [p for p in snap_ports(up) if p.role in ("out", "both")]
    ins = [p for p in snap_ports(down) if p.role in ("in", "both")]
    gaps = [gap(o, i) for o in outs for i in ins]
    if not gaps:
        return None
    return min(gaps, key=lambda g: (g.distance_m + g.rise_m, g.turn_deg))


def facing(up: Any, down: Any, distance: float, angle: float) -> Gap | None:
    """The pair of ends of ``up`` and ``down`` that face each other within ``distance`` and
    ``angle``, or None."""
    outs = [p for p in snap_ports(up) if p.role in ("out", "both")]
    ins = [p for p in snap_ports(down) if p.role in ("in", "both")]
    for o in outs:
        for i in ins:
            g = gap(o, i)
            if g.within(distance, angle):
                return g
    return None


def unjoined(scene: Any) -> list[str]:
    """W212: an end parts leave and an end parts arrive at, of two conveyors, within LINT_M in the
    floor plane and in height, with no connection joining them; the snap-port form of the
    Milestone 1 touching check (W203). Like W203 it ignores headings, so a handoff through a
    turn (an end meeting a perpendicular conveyor's infeed) still warns."""
    carriers = [c for c in scene.components if snap_ports(c)]
    joined = {(c.from_, c.to) for c in scene.connections}
    out = []
    for up in carriers:
        for down in carriers:
            if down.id == up.id or (up.id, down.id) in joined:
                continue
            if (down.id, up.id) in joined and (
                getattr(up, "bidirectional", False) or getattr(down, "bidirectional", False)
            ):
                continue  # a reversible pair is joined once, in its forward direction
            g = facing(up, down, LINT_M, 180.0)  # any heading: a handoff through a turn counts
            if g is not None:
                out.append(
                    f"conveyors {up.id} ({g.out.name}) and {down.id} ({g.into.name}) meet end to "
                    f"end but no connection joins them; parts will fall off {up.id}'s "
                    f"{g.out.name} end. Add '{{from: {up.id}, to: {down.id}}}' to connections if "
                    f"{up.id} feeds {down.id}"
                )
    return out


def misjoined(scene: Any) -> list[str]:
    """W213: a connection whose ends do not meet (further apart than JOIN_M in the floor plane or
    in height, or headings more than JOIN_DEG from facing)."""
    by_id = {c.id: c for c in scene.components}
    out = []
    for conn in scene.connections:
        up, down = by_id.get(conn.from_), by_id.get(conn.to)
        if up is None or down is None:
            continue
        g = best_gap(up, down)
        if g is None or g.within(JOIN_M, JOIN_DEG):
            continue
        out.append(
            f"connection {conn.from_} -> {conn.to}: {conn.from_}'s {g.out.name} end and "
            f"{conn.to}'s {g.into.name} end do not meet ({g.distance_m * 1000:.0f} mm apart, "
            f"{g.rise_m * 1000:.0f} mm out of level, {g.turn_deg:.1f} degrees off facing); "
            "parts are handed across the gap as if by a transfer plate. Move them together "
            "(the editor snaps ends within 0.15 m) if they should meet"
        )
    return out
