"""Helpers shared by the bulk components (PLAN 5.7a, ADR-0006): where material leaves and enters
each one, for the viewer's chutes."""

from __future__ import annotations

import math
from typing import Any


def _ahead(comp: Any, along: float, rise: float) -> list[float]:
    """The point ``along`` metres along the component's local +x and ``rise`` up."""
    from openfactory.core.geometry import to_world

    at = to_world(tuple(comp.config.position), float(comp.config.rotation_deg), (along, rise, 0.0))
    return [float(v) for v in at]


def outlet_point(comp: Any) -> list[float]:
    """Where material leaves ``comp``: a bin's outlet, a screw's far end, a feeder's body."""
    if comp.type == "screw_conveyor":
        cfg = comp.config
        slope = math.radians(cfg.incline_deg)
        return _ahead(comp, cfg.length * math.cos(slope), cfg.length * math.sin(slope))
    return [float(v) for v in comp.config.position]


def inlet_point(comp: Any) -> list[float]:
    """Where material enters ``comp``: the centre of a bin's top, a screw's inlet end, a
    feeder's body, a discharge."""
    if comp.type == "bin":
        return _ahead(comp, 0.0, comp.config.level_span)
    return [float(v) for v in comp.config.position]


def chute(by_id: dict[str, Any], me: Any) -> list[list[float]]:
    """The points a link's chutes are drawn through: from its ``from`` end's outlet, through its
    own body (a feeder's) or its inlet (a screw's), and from its outlet to its ``to`` end's
    inlet."""
    src, dst = by_id[me.config.from_], by_id[me.config.to]
    return [outlet_point(src), inlet_point(me), outlet_point(me), inlet_point(dst)]


def speed_fraction(comp: Any) -> float:
    """A feeder's speed command (``speed``, %, unbound: 100 %) as a fraction 0 to 1."""
    return min(max(float(comp.read("speed", default=100.0)), 0.0), 100.0) / 100.0
