"""Helpers shared by the process link components (PLAN 3.2): where a link end is in the scene,
for the viewer and for a pipe's drawn length."""

from __future__ import annotations

from typing import Any


def end_point(by_id: dict[str, Any], ref: str, toward: tuple[float, float, float]) -> list[float]:
    """Scene point of a link end: a tank nozzle (on the tank, facing ``toward``), or the named
    component's origin (a supply, drain, junction, or the link an implicit junction sits on)."""
    name, _, nozzle = ref.partition(".")
    comp = by_id[name]
    if nozzle:
        return comp.nozzle_point(nozzle, toward)  # type: ignore[no-any-return]
    return [float(v) for v in comp.config.position]
