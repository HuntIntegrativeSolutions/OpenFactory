"""On/off debounce for sensor outputs (photo_eye, color_sensor; PLAN 2.5b).

Behavior spec
-------------
:class:`Debounce` holds one debounced boolean. Each step, :meth:`Debounce.update` takes the raw
state and the step length: when the raw state differs from the debounced one, the change must be
seen on consecutive steps for at least ``seconds``, measured from the first step that saw it,
before the debounced state follows; a change that reverts sooner never shows. Both edges are
debounced alike. ``seconds`` 0 follows the raw state in the same step. At 50 Hz, 20 ms is one tick
of delay (the photo eye's long-standing default).
"""

from __future__ import annotations

_EPS = 1e-9


class Debounce:
    def __init__(self, seconds: float, state: bool = False) -> None:
        self.seconds = seconds
        self.state = state
        self._pending_s: float | None = None  # time the raw state has differed from ``state``

    def update(self, raw: bool, dt: float) -> bool:
        if raw == self.state:
            self._pending_s = None
        elif self._pending_s is None:
            self._pending_s = 0.0  # first step that sees the change
        else:
            self._pending_s += dt
        if self._pending_s is not None and self._pending_s >= self.seconds - _EPS:
            self.state = raw
            self._pending_s = None
        return self.state
