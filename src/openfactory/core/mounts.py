"""Mount ports (PLAN 4.1g1, ruled 2026-11-14): a conveyor's side rails, where a sensor, pusher,
stopper or diverter is placed by one rule per type, and the reach check W214.

Behavior spec
-------------
A ``belt_conveyor`` or ``roller_conveyor`` has two rails (ruling 5: not a turntable's or a lift's
deck, which turn or rise away from what stands beside them), seen looking along the flow:
``rail_left`` along local z = -width/2 and ``rail_right`` along local z = +width/2, at belt-top
height, from the infeed end for ``length`` along local +x, each facing in across the belt. They
are published as ``mount`` :class:`~openfactory.core.snaps.SnapPort` s (:func:`rails`).

:func:`place_on_rail` is the one copy of the drop rules (ruling 6; the editor asks the server for
it, ``POST /api/edit/mount``, and Milestone 5 places devices from a drawing with it). Given a
device's type and parameters, the conveyor, the rail and the distance along it (clamped to the
rail), it gives the device's ``position``, ``rotation_deg`` and, for a diverter, the sign its
``swing_deg`` must have to swing over the belt. In the belt's frame (x along the flow, z across,
y up from the belt top, ``s`` = -1 on the left rail, +1 on the right):

- ``photo_eye``: the beam midpoint over the centre line, the beam across the belt (the belt's
  heading); its height above the belt kept if it is 0.02 to 1 m, else 0.1 m.
- ``distance_sensor`` looking across (axis ``+z``): its face 0.05 m outside the rail, facing
  across (the belt's heading on the left rail, plus 180 on the right); height kept as an eye's.
- ``color_sensor``, ``distance_sensor`` looking down (``-y``): over the centre line, its height
  kept as an eye's (0.1 m when outside 0.02 to 1 m).
- ``pusher``: the retracted plate face 0.05 m outside the rail, extending across (heading as a
  distance sensor's); the plate's bottom 0.05 m above the belt: y = 0.05 + h/2 with
  ``plate_height`` h set. Unset, the loader sizes the plate to reach 3/4 of the tallest emitted
  part ``t`` (:func:`openfactory.components.pusher.plate_height_for`), so y = (0.05 + 0.75 t)/2
  gives a plate from 0.05 to 0.75 t; when that plate would be under the loader's 0.15 m minimum
  (or no part is emitted), y = 0.05 + 0.075.
- ``stopper``: on the centre line at belt-top height, the belt's heading.
- ``diverter_arm``: the pivot on the rail at belt-top height, at rest along the flow (the belt's
  heading), ``swing_deg`` positive on the left rail and negative on the right, so it swings over
  the belt.

W214 (:func:`short_of_belt`, a warning, 1 mm): a photo eye's beam, a stopper's blade (its
``width``, or the belt's when unset) or a diverter's tip at ``swing_deg`` that does not reach
across the belt or roller conveyor it stands on: the highest one whose footprint, widened by
0.1 m across the flow, holds the device's origin, with the belt top 0 to 1 m below it. An eye or
blade must reach both rails; a diverter's tip the rail across from its pivot. Pushers are W207's.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal

from openfactory.core.geometry import rotate_y, to_local, to_world
from openfactory.core.snaps import SnapPort

#: Conveyor types with side rails (ruling 5).
RAIL_TYPES = ("belt_conveyor", "roller_conveyor")
#: Device types the drop rules place.
MOUNT_TYPES = ("photo_eye", "distance_sensor", "color_sensor", "pusher", "stopper", "diverter_arm")
#: Each conveyor type's mount port names (the component reference lists them).
RAIL_NAMES = ("rail_left", "rail_right")
#: A face or plate stands this far outside the rail, clear of the parts.
OUTSIDE_M = 0.05
#: A pusher's plate bottom above the belt.
PLATE_CLEAR_M = 0.05
#: A sensor's height above the belt when its present one is not between these.
SENSOR_DEFAULT_M = 0.1
SENSOR_RANGE_M = (0.02, 1.0)
#: W214: how far short of a rail an eye, blade or arm may stop (ruling 8).
REACH_TOL_M = 0.001
#: W214: how far beside a belt a device's origin may be and still stand on it, and how high.
BESIDE_M = 0.1
ABOVE_M = 1.0

Rail = Literal["rail_left", "rail_right"]


def rails(conveyor: Any) -> list[SnapPort]:
    """The ``mount`` ports of a conveyor: none for a type without rails."""
    if conveyor.type not in RAIL_TYPES:
        return []
    rot = float(conveyor.rotation_deg)
    half = conveyor.width / 2
    out = []
    for name, side in (("rail_left", -1.0), ("rail_right", 1.0)):
        start = to_world(tuple(conveyor.position), rot, (0.0, 0.0, side * half))
        # facing in across the belt: toward +z from the left rail, -z from the right
        heading = rot - 90.0 if side < 0 else rot + 90.0
        out.append(SnapPort(name, "mount", start, heading, conveyor.width, "both", conveyor.length))
    return out


@dataclass(frozen=True)
class Placement:
    """Where a device dropped on a rail goes."""

    position: tuple[float, float, float]
    rotation_deg: float
    swing_sign: int | None = None  # a diverter's swing_deg sign: +1 or -1

    def document(self) -> dict[str, Any]:
        """As ``POST /api/edit/mount`` returns it (to 0.1 mm and 0.0001 degrees)."""
        doc: dict[str, Any] = {
            "position": [round(v, 4) for v in self.position],
            "rotation_deg": round(self.rotation_deg, 4),
        }
        if self.swing_sign is not None:
            doc["swing_sign"] = self.swing_sign
        return doc


def _heading(deg: float) -> float:
    """A heading as the shipped scenes write it, in (-180, 180]."""
    d = (deg + 180.0) % 360.0 - 180.0
    return 180.0 if d == -180.0 else d + 0.0


def _kept(height: float) -> float:
    lo, hi = SENSOR_RANGE_M
    return height if lo <= height <= hi else SENSOR_DEFAULT_M


def pusher_height_above(plate_height: float | None, tallest_part: float | None) -> float:
    """A dropped pusher's origin above the belt top (see the module docstring)."""
    from openfactory.components.pusher import DEFAULT_PLATE_HEIGHT_M, PLATE_REACH

    if plate_height is not None:
        return PLATE_CLEAR_M + plate_height / 2
    if tallest_part is not None and PLATE_REACH * tallest_part - PLATE_CLEAR_M >= (
        DEFAULT_PLATE_HEIGHT_M - 1e-9
    ):
        return (PLATE_CLEAR_M + PLATE_REACH * tallest_part) / 2
    return PLATE_CLEAR_M + DEFAULT_PLATE_HEIGHT_M / 2


def place_on_rail(
    device_type: str,
    params: dict[str, Any],
    conveyor: Any,
    rail: str,
    along: float,
    tallest_part: float | None = None,
) -> Placement:
    """The drop rule for ``device_type`` (ruling 6). ``params`` are the device's values as the
    scene has them (``position`` for the height a sensor keeps, ``plate_height``, ``axis``,
    ``swing_deg``); ``conveyor`` a belt or roller conveyor's config; ``along`` metres from its
    infeed end along ``rail``; ``tallest_part`` the tallest emitted part's height (a pusher with
    ``plate_height`` unset). ValueError for a type or rail it does not know."""
    if conveyor.type not in RAIL_TYPES:
        raise ValueError(
            f"{conveyor.id} is a {conveyor.type}: only belt and roller conveyors have rails"
        )
    if rail not in RAIL_NAMES:
        raise ValueError(f"no rail {rail!r} (rail_left or rail_right)")
    if device_type not in MOUNT_TYPES:
        raise ValueError(f"a {device_type} is not placed on a rail")
    side = -1.0 if rail == "rail_left" else 1.0
    half = conveyor.width / 2
    x = min(max(float(along), 0.0), float(conveyor.length))
    rot = float(conveyor.rotation_deg)
    top = float(conveyor.position[1])
    current = params.get("position") or [0.0, top + SENSOR_DEFAULT_M, 0.0]
    above = _kept(float(current[1]) - top)
    across = rot if side < 0 else rot + 180.0  # local +z of the device points over the belt
    swing = None
    if device_type == "photo_eye" or device_type == "color_sensor":
        local, heading = (x, above, 0.0), rot
    elif device_type == "distance_sensor":
        if params.get("axis", "+z") == "-y":
            local, heading = (x, above, 0.0), rot
        else:
            local, heading = (x, above, side * (half + OUTSIDE_M)), across
    elif device_type == "pusher":
        y = pusher_height_above(params.get("plate_height"), tallest_part)
        local, heading = (x, y, side * (half + OUTSIDE_M)), across
    elif device_type == "stopper":
        local, heading = (x, 0.0, 0.0), rot
    else:  # diverter_arm
        local, heading = (x, 0.0, side * half), rot
        swing = 1 if side < 0 else -1
    world = to_world(tuple(conveyor.position), rot, local)
    return Placement(
        tuple(round(v, 10) + 0.0 for v in world),  # type: ignore[arg-type]
        _heading(heading),
        swing,
    )


def _belt_under(point: tuple[float, float, float], belts: list[Any]) -> Any | None:
    """The belt a device at ``point`` stands on: of the belts whose top it is on or just above
    (within BESIDE_M of an edge), the highest; of two at one height, the one whose centre line is
    nearer (two belts side by side, a device beside each one's facing rail: review of
    2026-11-20, second set, 1b)."""
    best, key = None, None
    for belt in belts:
        lx, _, lz = to_local(tuple(belt.position), belt.rotation_deg, point)
        rise = point[1] - belt.position[1]
        on = -1e-9 <= lx <= belt.length + 1e-9 and abs(lz) <= belt.width / 2 + BESIDE_M + 1e-9
        if not on or not -0.01 <= rise <= ABOVE_M:
            continue
        mine = (-float(belt.position[1]), abs(lz))
        if key is None or mine < key:
            best, key = belt, mine
    return best


def _across(belt: Any, point: tuple[float, float, float]) -> float:
    """``point``'s belt-local z (across the flow)."""
    return to_local(tuple(belt.position), belt.rotation_deg, point)[2]


def _span(comp: Any, belt: Any, half: float) -> tuple[float, float]:
    """The belt-local z the ends of a span ``half`` either side of ``comp``'s origin along its
    local z reach."""
    o = tuple(comp.position)
    dx, _, dz = rotate_y((0.0, 0.0, 1.0), comp.rotation_deg)
    ends = [(o[0] + s * half * dx, o[1], o[2] + s * half * dz) for s in (-1.0, 1.0)]
    zs = sorted(_across(belt, e) for e in ends)
    return zs[0], zs[1]


def short_of_belt(components: list[Any]) -> list[tuple[str, str]]:
    """W214: ``(component id, message)`` for each eye, stopper or diverter that stands on a belt
    or roller conveyor and does not reach across it. ``components`` are configs (a composite's
    parts expanded)."""
    belts = [c for c in components if c.type in RAIL_TYPES]
    out: list[tuple[str, str]] = []
    for comp in components:
        if comp.type not in ("photo_eye", "stopper", "diverter_arm"):
            continue
        belt = _belt_under(tuple(comp.position), belts)
        if belt is None:
            continue
        half = belt.width / 2
        if comp.type in ("photo_eye", "stopper"):
            if comp.type == "photo_eye":
                reach, what = comp.beam_length / 2, f"beam ({comp.beam_length:g} m)"
            else:
                width = comp.width if comp.width is not None else belt.width
                reach, what = width / 2, f"blade ({width:g} m)"
            lo, hi = _span(comp, belt, reach)
            short = []
            if lo > -half + REACH_TOL_M:
                short.append(f"{(lo + half) * 1000:.0f} mm short of the left rail")
            if hi < half - REACH_TOL_M:
                short.append(f"{(half - hi) * 1000:.0f} mm short of the right rail")
            if short:
                out.append((comp.id, f"its {what} does not reach across {belt.id} "
                            f"({belt.width:g} m wide): {' and '.join(short)}. Lengthen it, or "
                            "drop it on a rail in the editor"))  # fmt: skip
            continue
        # a diverter: its tip at swing_deg must reach the rail across from its pivot
        pivot = _across(belt, tuple(comp.position))
        swing = math.radians(comp.swing_deg)
        # at rest the arm lies along local +x; positive swing turns it toward local +z
        local_tip = (comp.length * math.cos(swing), 0.0, comp.length * math.sin(swing))
        tip = _across(belt, to_world(tuple(comp.position), comp.rotation_deg, local_tip))
        if abs(pivot) > 1e-9:
            far = -half if pivot > 0 else half
        else:
            far = half if tip >= pivot else -half
        reached = tip >= far - REACH_TOL_M if far > 0 else tip <= far + REACH_TOL_M
        if not reached:
            rail = "right" if far > 0 else "left"
            gap = abs(far - tip)
            out.append((comp.id, f"its arm's tip at {comp.swing_deg:g} degrees stops "
                        f"{gap * 1000:.0f} mm short of {belt.id}'s {rail} rail, across from its "
                        "pivot; parts pass it. Lengthen the arm, swing it further, or check the "
                        "sign of swing_deg (it must swing over the belt)"))  # fmt: skip
    return out
