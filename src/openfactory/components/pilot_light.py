"""Pilot light (PLAN §5, ``pilot_light``).

Behavior spec
-------------
A panel indicator with no PLC-visible outputs. It is lit while ``on`` (DO) is true; the viewer shows
it in its configured ``color``. Each step samples the command, so the lit state seen by the viewer
changes at a step boundary.
"""

from __future__ import annotations

from typing import Any

from openfactory.core.component import Component, register


@register("pilot_light")
class PilotLight(Component):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.lit = False

    def step(self, dt: float) -> None:
        self.lit = bool(self.read("on", default=False))

    def state(self) -> dict[str, Any]:
        return {"lit": self.lit}

    def geometry(self) -> dict[str, Any]:
        return {**super().geometry(), "color": self.config.color}
