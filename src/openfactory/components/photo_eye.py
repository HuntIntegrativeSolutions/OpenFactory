"""Through-beam photo eye (PLAN §5, ``photo_eye``).

Behavior spec
-------------
The beam is a line segment ``beam_length`` long through the component origin, along local z (across
the flow). Each step the raw beam state is *blocked* when any part's box touches that segment
(within 1 nm, so float drift cannot miss an exact touch). ``detect`` (DI) follows the raw state
through a 20 ms debounce on both edges: a change must be seen on consecutive steps for at least
20 ms, measured from the first step that saw it, before ``detect`` changes. At the default 50 Hz
step that is one tick of delay; a block or a gap shorter than the debounce never reaches the PLC.

Geometry: the origin is the beam midpoint.
"""

from __future__ import annotations

from typing import Any

from openfactory.core.component import Component, register
from openfactory.core.geometry import segment_hits_aabb, to_world

DEBOUNCE_S = 0.020
_EPS = 1e-9
#: Contact tolerance in meters, so float drift in part positions cannot miss an exact touch.
_TOUCH_M = 1e-9


@register("photo_eye")
class PhotoEye(Component):
    FAULTS = ("stuck_on", "stuck_off", "misaligned", "noisy")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.blocked = False  # raw beam state this step
        self.detect = False  # debounced state written to the PLC
        self._pending_s: float | None = None  # time the raw state has differed from detect

    @property
    def beam_offset(self) -> float:
        """How far a ``misaligned`` fault has moved the beam along local x (the flow), meters."""
        if self.fault is not None and self.fault.kind == "misaligned":
            return float(self.fault.params["offset_m"])
        return 0.0

    @property
    def beam(self) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        half, x = self.config.beam_length / 2, self.beam_offset
        pos, rot = self.config.position, self.config.rotation_deg
        return to_world(pos, rot, (x, 0.0, -half)), to_world(pos, rot, (x, 0.0, half))

    def beam_blocked(self) -> bool:
        p0, p1 = self.beam
        for part in self.world.parts.values():
            lo, hi = part.aabb()
            lo = (lo[0] - _TOUCH_M, lo[1] - _TOUCH_M, lo[2] - _TOUCH_M)
            hi = (hi[0] + _TOUCH_M, hi[1] + _TOUCH_M, hi[2] + _TOUCH_M)
            if segment_hits_aabb(p0, p1, lo, hi):
                return True
        return False

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
        return {"detect": self.detect, "blocked": self.blocked, "beam_offset_m": self.beam_offset}

    def geometry(self) -> dict[str, Any]:
        return {**super().geometry(), "beam_length": self.config.beam_length}
