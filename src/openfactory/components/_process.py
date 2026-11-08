"""Helpers shared by the process link components (PLAN 3.2): where a link end is in the scene,
for the viewer and for a pipe's drawn length."""

from __future__ import annotations

from typing import Any

from openfactory.core.schema import LINK_TYPES


def end_point(
    by_id: dict[str, Any], me: Any, side: str, toward: tuple[float, float, float]
) -> list[float]:
    """Scene point of link ``me``'s ``side`` (``from`` or ``to``) end: a tank nozzle (on the tank,
    facing ``toward``); another link, joined at the downstream link's origin (the other's for a
    ``to`` end, ``me``'s own for a ``from`` end); or the named component's origin."""
    ref = me.config.from_ if side == "from" else me.config.to
    name, _, nozzle = ref.partition(".")
    comp = by_id[name]
    if nozzle:
        return comp.nozzle_point(nozzle, toward)  # type: ignore[no-any-return]
    if comp.type in LINK_TYPES and side == "from":
        return [float(v) for v in me.config.position]
    return [float(v) for v in comp.config.position]


def link_points(by_id: dict[str, Any], me: Any, via: list[list[float]]) -> list[list[float]]:
    """The points a link is drawn through: its ``from`` end, ``via``, its ``to`` end."""
    cfg = me.config
    raw_from = by_id[cfg.from_.partition(".")[0]].config.position
    raw_to = by_id[cfg.to.partition(".")[0]].config.position
    first = end_point(by_id, me, "from", tuple(via[0] if via else raw_to))
    last = end_point(by_id, me, "to", tuple(via[-1] if via else raw_from))
    return [first, *via, last]
