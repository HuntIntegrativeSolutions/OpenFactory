"""Color sensor (PLAN §5, ``color_sensor``; added for the sorting-by-color scene, PLAN 2.7).

Behavior spec
-------------
A diffuse color sensor taught one color. Each step it looks along its beam, from the sensor face
for ``beam_length`` meters, for the nearest part or conveyor belt the beam enters (the same ray
test as ``distance_sensor``, a belt being a slab under its top surface). ``detect`` (DI) is true
while that nearest object is a part; ``match`` (DI) is true while it is a part whose ``color``
(from its emitter's part spec) is ``teach``. A plain part (no color) never matches, and a belt
stops the beam, so a sensor looking down at a belt sees only the part on it. With ``debounce_ms``
0 (the default) both outputs follow the part at the step boundary; set, each output passes through
its own on/off debounce of that length (PLAN 2.5b, :mod:`openfactory.core.debounce`), so a
color flicker or a part edge shorter than it never reaches the PLC.

Geometry: the origin is the sensor face. ``axis: -y`` (the default) aims the beam straight down;
``axis: +z`` aims it along local +z, across the flow from one side.
"""

from __future__ import annotations

from typing import Any

from openfactory.components.distance_sensor import DistanceSensor, ray_box
from openfactory.core.component import register
from openfactory.core.debounce import Debounce


@register("color_sensor")
class ColorSensor(DistanceSensor):
    FAULTS = ("stuck_on", "stuck_off", "noisy")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.seen: str | None = None  # color of the part in range ("plain" if none), or None
        self.detected = False  # debounced detect
        self.matched = False  # debounced match
        seconds = self.config.debounce_ms / 1000.0
        self._detect = Debounce(seconds)
        self._match = Debounce(seconds)

    def step(self, dt: float) -> None:
        nearest, part_seen = float(self.config.beam_length), None
        for part in self.world.parts.values():
            lo, hi = part.aabb()
            t = ray_box(self.config.position, self.direction, lo, hi)
            if t is not None and t < nearest:
                nearest, part_seen = t, part
        for belt in self.world.conveyors:
            t = self._belt_hit(belt)
            if t is not None and t < nearest:
                nearest, part_seen = t, None  # the belt is nearer: the beam stops there
        self.distance = nearest
        self.seen = (part_seen.color or "plain") if part_seen is not None else None
        self.detected = self._detect.update(part_seen is not None, dt)
        self.matched = self._match.update(self.seen == self.config.teach, dt)
        self.write("detect", self.detected)
        self.write("match", self.matched)

    def state(self) -> dict[str, Any]:
        return {
            "seen": self.seen,
            "match": self.matched,
            "distance_m": self.distance,  # where the beam stops, for the viewer to draw it
        }

    def geometry(self) -> dict[str, Any]:
        return {**super().geometry(), "teach": self.config.teach}
