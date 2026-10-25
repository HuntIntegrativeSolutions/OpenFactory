"""Through-beam photo eye (PLAN §5, ``photo_eye``).

Behavior spec
-------------
The beam is a line segment ``beam_length`` long through the component origin, along local z (across
the flow). Each step the raw beam state is *blocked* when any part's box intersects that segment.
``detect`` (DI) follows the raw state through a 20 ms debounce on both edges: a change must be seen
on consecutive steps for at least 20 ms, measured from the first step that saw it, before ``detect``
changes. At the default 50 Hz step that is one tick of delay; a block or a gap shorter than the
debounce never reaches the PLC.

Geometry: the origin is the beam midpoint.
"""

from __future__ import annotations

from typing import Any

from openfactory.core.component import Component, register
from openfactory.core.geometry import segment_hits_aabb, to_world

DEBOUNCE_S = 0.020
_EPS = 1e-9


@register("photo_eye")
class PhotoEye(Component):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        half = self.config.beam_length / 2
        pos, rot = self.config.position, self.config.rotation_deg
        self.beam = (to_world(pos, rot, (0.0, 0.0, -half)), to_world(pos, rot, (0.0, 0.0, half)))
        self.blocked = False  # raw beam state this step
        self.detect = False  # debounced state written to the PLC
        self._pending_s: float | None = None  # time the raw state has differed from detect

    def beam_blocked(self) -> bool:
        p0, p1 = self.beam
        return any(segment_hits_aabb(p0, p1, *part.aabb()) for part in self.world.parts.values())

    def step(self, dt: float) -> None:
        self.blocked = self.beam_blocked()
        if self.blocked == self.detect:
            self._pending_s = None
        elif self._pending_s is None:
            self._pending_s = 0.0  # first step that sees the change
        else:
            self._pending_s += dt
        if self._pending_s is not None and self._pending_s >= DEBOUNCE_S - _EPS:
            self.detect = self.blocked
            self._pending_s = None
        self.write("detect", self.detect)

    def state(self) -> dict[str, Any]:
        return {"detect": self.detect, "blocked": self.blocked}

    def geometry(self) -> dict[str, Any]:
        return {**super().geometry(), "beam_length": self.config.beam_length}
