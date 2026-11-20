"""Footprints: the space each component's body takes in the scene (PLAN 5.5).

Behavior spec
-------------
:func:`boxes` gives a component's body as one or more axis-aligned boxes in scene metres,
``(x0, x1, y0, y1, z0, z1)``, from its configuration alone (no build, no browser). Each is the
body the viewer draws for that type (``web/src/components``), turned by the component's
``rotation_deg`` where the viewer turns it and then bounded on the scene's axes. It is the body
only: no beam, no label, no default model, no swept stroke, and no run of pipe (a link's runs are
:func:`runs`). Three dimensions, not a plan: an agitator on a tank's top, or a supply above it,
does not overlap the tank because it is above it.

Per type (local axes; a conveyor's origin is the centre of its infeed end at belt top):

- belt and roller conveyor, lift: the belt and its rails, ``x`` 0 to ``length``, ``z`` across
  ``width`` plus the rails (0.03 m each side), ``y`` from 0.1 m below the belt top to it (the
  legs below are not a body: a device's actuator under the belt is not an overlap);
- turntable: its deck about its centre, ``y`` as a belt;
- tank: its shell, ``y`` 0 to ``height``, a cylinder's ``diameter`` or a box's ``length`` by
  ``width``;
- supply and drain: the flanged stub hanging 0.3 m below the connection point (a drain's funnel
  above it); junction: its fitting; heater: its element;
- valves: the body and its actuator and handle, drawn on the scene's axes (a valve is not turned
  as a body); pump: volute, motor and base, likewise;
- transmitter: head and neck; drive cabinet (``vfd_motor``): the cabinet;
- photo eye: its two heads, beside the beam's ends; pusher: its cylinder behind the plate and
  the retracted plate; stopper: blade and actuator; diverter arm: the arm at rest;
  pick-and-place: its gantry's beams and legs (the space they frame is clear);
- box emitter: its hopper, 1.15 times the largest part made (at least 0.2 m) each way; part
  remover and scenery: their ``size`` about the origin; panel devices: their plate.

:func:`runs` gives the straight runs a link is drawn through (a pipe from its ``points``, a valve
or pump through its own origin), each a box as thick as the line, for routing and the overlap
lint.

Not a safety-rated simulation. OpenFactory is a training and demo tool.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from typing import Any

Box = tuple[float, float, float, float, float, float]

#: How deep a conveyor's body is below its belt top, m.
BELT_DEPTH_M = 0.1
#: A rail stands this far outside the belt edge, m (conveyor.ts: 0.03 wide at width/2).
RAIL_M = 0.03
#: Two boxes overlap when they share more than this in every axis, m.
OVERLAP_M = 1e-3

#: Types the viewer draws on the scene's axes whatever their rotation (a link's body).
UNTURNED = frozenset({"valve_onoff", "valve_control", "pump", "pipe"})


def _local(cfg: Any, type_name: str) -> list[Box]:  # noqa: C901, PLR0911, PLR0912 - one table
    g = getattr
    if type_name in ("belt_conveyor", "roller_conveyor", "lift"):
        half = g(cfg, "width") / 2 + RAIL_M
        return [(0.0, g(cfg, "length"), -BELT_DEPTH_M, 0.0, -half, half)]
    if type_name == "turntable":
        hl, hw = g(cfg, "length") / 2, g(cfg, "width") / 2
        return [(-hl, hl, -BELT_DEPTH_M, 0.0, -hw, hw)]
    if type_name == "tank":
        if g(cfg, "shape", "cylinder") == "box":
            hl, hw = g(cfg, "length") / 2, g(cfg, "width") / 2
        else:
            hl = hw = g(cfg, "diameter") / 2
        return [(-hl, hl, 0.0, g(cfg, "height"), -hw, hw)]
    if type_name == "supply":
        return [(-0.09, 0.09, -0.3, 0.015, -0.09, 0.09)]
    if type_name == "drain":
        return [(-0.16, 0.16, -0.3, 0.14, -0.16, 0.16)]
    if type_name == "junction":
        return [(-0.07, 0.07, -0.07, 0.07, -0.07, 0.07)]
    if type_name == "heater":
        return [(-0.17, 0.17, -0.02, 0.02, -0.17, 0.17)]
    if type_name in ("valve_onoff", "valve_control"):
        actuator = 0.14 if type_name == "valve_onoff" else 0.24
        return [(-0.1, 0.1, -0.06, 0.08 + actuator, -0.1, 0.1)]
    if type_name == "pump":
        return [(-0.15, 0.15, -0.17, 0.13, -0.35, 0.15)]
    if type_name == "transmitter":
        return [(-0.06, 0.06, -0.02, 0.14, -0.03, 0.03)]
    if type_name == "vfd_motor":
        return [(-0.2, 0.2, 0.0, 0.8, -0.15, 0.17)]
    if type_name == "photo_eye":
        far = g(cfg, "beam_length") / 2 + 0.02
        return [(-0.025, 0.025, -0.045, 0.045, s * far - 0.02, s * far + 0.02) for s in (-1, 1)]
    if type_name == "pusher":
        body = g(cfg, "stroke") + 0.1
        pw, ph = g(cfg, "plate_width") / 2, (g(cfg, "plate_height") or 0.15) / 2
        return [
            (-0.045, 0.045, -0.045, 0.045, -0.06 - body, -0.06),
            (-pw, pw, -ph, ph, -0.02, 0.0),
        ]
    if type_name == "stopper":
        width = (g(cfg, "width") or 0.5) / 2
        return [
            (0.0, 0.02, -g(cfg, "height"), 0.0, -width, width),
            (-0.03, 0.05, -0.14, -0.08, -0.06, 0.06),
        ]
    if type_name == "diverter_arm":
        return [(0.0, g(cfg, "length"), 0.0, g(cfg, "height") + 0.06, -0.03, 0.03)]
    if type_name == "pick_and_place":  # the gantry's members, not the space they frame
        travel, floor = g(cfg, "travel"), -float(cfg.position[1])
        out = [(-0.33, travel + 0.33, 0.32, 0.38, -0.04, 0.04)]  # the beam
        for x in (-0.3, travel + 0.3):
            out.append((x - 0.03, x + 0.03, 0.32, 0.38, -0.45, 0.45))  # an end's cross beam
            out += [(x - 0.03, x + 0.03, floor, 0.38, z - 0.03, z + 0.03) for z in (-0.42, 0.42)]
        return out
    if type_name == "box_emitter":
        sizes = [p.size for p in g(cfg, "parts") or []]
        half = [max([0.2, *(s[i] for s in sizes)]) * 1.15 / 2 for i in range(3)]
        return [(-half[0], half[0], -half[1], half[1], -half[2], half[2])]
    if type_name in ("part_remover", "scenery"):
        sx, sy, sz = (v / 2 for v in g(cfg, "size"))
        return [(-sx, sx, -sy, sy, -sz, sz)]
    if type_name in ("pushbutton", "pilot_light", "selector_switch"):
        return [(-0.04, 0.04, -0.04, 0.04, -0.04, 0.03)]
    if type_name == "counter_display":
        half = 0.03 * int(g(cfg, "digits", 4)) + 0.02
        return [(-half, half, -0.05, 0.05, -0.02, 0.0)]
    if type_name in ("distance_sensor", "color_sensor"):
        return [(-0.03, 0.03, -0.03, 0.03, -0.03, 0.03)]
    if type_name == "pipe":
        return []
    return [(-0.075, 0.075, -0.075, 0.075, -0.075, 0.075)]  # the viewer's fallback marker


def turned(box: Box, heading_deg: float) -> Box:
    """A local box turned about +y by ``heading_deg`` (the scene's rotation), bounded again."""
    if abs(heading_deg) < 1e-9:
        return box
    r = math.radians(heading_deg)
    c, s = math.cos(r), math.sin(r)
    x0, x1, y0, y1, z0, z1 = box
    xs, zs = [], []
    for lx in (x0, x1):
        for lz in (z0, z1):  # core/geometry.rotate_y: x' = x cos + z sin, z' = -x sin + z cos
            xs.append(lx * c + lz * s)
            zs.append(-lx * s + lz * c)
    return (min(xs), max(xs), y0, y1, min(zs), max(zs))


def boxes(cfg: Any, type_name: str | None = None) -> list[Box]:
    """The component's body in scene metres (see the module docstring)."""
    kind = type_name or cfg.type
    x, y, z = (float(v) for v in cfg.position)
    heading = 0.0 if kind in UNTURNED else float(getattr(cfg, "rotation_deg", 0.0) or 0.0)
    out = []
    for b in _local(cfg, kind):
        x0, x1, y0, y1, z0, z1 = turned(b, heading)
        out.append((x + x0, x + x1, y + y0, y + y1, z + z0, z + z1))
    return out


def bounds(items: Iterable[Box]) -> Box | None:
    """The box around ``items``, or None when there are none."""
    items = list(items)
    if not items:
        return None
    return (
        min(b[0] for b in items), max(b[1] for b in items),
        min(b[2] for b in items), max(b[3] for b in items),
        min(b[4] for b in items), max(b[5] for b in items),
    )  # fmt: skip


def overlap(a: Box, b: Box, margin: float = OVERLAP_M) -> tuple[float, float, float] | None:
    """How much two boxes share in x, y and z, when it is more than ``margin`` in all three."""
    dx = min(a[1], b[1]) - max(a[0], b[0])
    dy = min(a[3], b[3]) - max(a[2], b[2])
    dz = min(a[5], b[5]) - max(a[4], b[4])
    if dx > margin and dy > margin and dz > margin:
        return dx, dy, dz
    return None


def segment_box(a: Sequence[float], b: Sequence[float], radius: float) -> Box:
    """The box around a straight run from ``a`` to ``b`` as thick as ``2 * radius``."""
    return (
        min(a[0], b[0]) - radius, max(a[0], b[0]) + radius,
        min(a[1], b[1]) - radius, max(a[1], b[1]) + radius,
        min(a[2], b[2]) - radius, max(a[2], b[2]) + radius,
    )  # fmt: skip


def runs(points: Sequence[Sequence[float]], radius: float) -> list[Box]:
    """The boxes of a link's straight runs through ``points``."""
    return [
        segment_box(p, q, radius)
        for p, q in zip(points, points[1:], strict=False)
        if math.dist(p, q) > 1e-9
    ]
