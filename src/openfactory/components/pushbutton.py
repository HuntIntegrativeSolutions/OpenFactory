"""Operator pushbutton (PLAN §5, ``pushbutton``).

Behavior spec
-------------
A viewer click calls :meth:`Pushbutton.press`, which may come from the server thread. The press is
applied on the next :meth:`step`, so PLC-visible changes always happen at a step boundary:

- ``momentary`` (default): ``pressed`` (DI) goes true for 300 ms, then false. Pressing again while
  it is still true restarts the 300 ms.
- ``maintained``: each press toggles ``pressed``, like a push-on/push-off button.

Several presses between two steps count as one for a momentary button. For a maintained button each
press toggles, so an even number of presses between two steps cancels out.

Geometry: the origin is the center of the button face.
"""

from __future__ import annotations

import threading
from typing import Any

from openfactory.core.component import Component, register

PULSE_S = 0.300
_EPS = 1e-9


@register("pushbutton")
class Pushbutton(Component):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.pressed = False
        self._remaining_s = 0.0
        self._presses = 0
        self._lock = threading.Lock()

    def press(self) -> None:
        """Register a viewer click; applied on the next step."""
        with self._lock:
            self._presses += 1

    def step(self, dt: float) -> None:
        with self._lock:
            presses, self._presses = self._presses, 0
        if self.config.mode == "maintained":
            if presses % 2:
                self.pressed = not self.pressed
        elif presses:
            self.pressed = True
            self._remaining_s = PULSE_S
        elif self.pressed:
            self._remaining_s -= dt
            if self._remaining_s <= _EPS:
                self.pressed = False
        self.write("pressed", self.pressed)

    def state(self) -> dict[str, Any]:
        return {"pressed": self.pressed}

    def geometry(self) -> dict[str, Any]:
        return {**super().geometry(), "mode": self.config.mode, "color": self.config.color}
