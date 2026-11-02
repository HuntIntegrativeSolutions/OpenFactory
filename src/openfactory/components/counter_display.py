"""Counter display (PLAN 2.5b, ``counter_display``).

Behavior spec
-------------
A panel readout with no PLC-visible outputs: it shows the ``value`` AO the PLC writes, on
``digits`` seven-segment digits (1 to 6, default 4) with ``decimals`` of them after the decimal
point (default 0). Each step samples the value and formats it with ``decimals`` places, right
aligned; a minus sign takes a digit. A value that does not fit the digits (9999 is the largest
for four digits and no decimals), or is not a finite number, shows a dash in every digit. The
display does not clamp to ``range``: ``range`` only scales the tag in the ``int16_scaled``
encoding (raw 0 is lo, raw 32767 is hi; default 0 to the largest number shown), so a count
written over int16_scaled comes back within 0.31 of itself for 0 to 9999 and the display's
rounding gives the count. Its one fault is ``stuck_off``, a dead display: blank whatever the
value (as a pilot light's burned bulb, PLAN 2.6).

State: ``text``, exactly what the digits show (``digits`` characters, plus one for a decimal
point; blank when off), and ``value``, the number sampled.

Geometry: the origin is the centre of the display face; the face looks along local +z.
"""

from __future__ import annotations

import math
from typing import Any

from openfactory.core.component import Component, register


def display_text(value: float, digits: int, decimals: int) -> str:
    """What ``digits`` seven-segment digits show for ``value``, right aligned."""
    width = digits + (1 if decimals else 0)
    if not math.isfinite(value):
        return _dashes(digits, decimals)
    text = f"{value:.{decimals}f}"
    if text.startswith("-") and float(text) == 0:
        text = text[1:]  # -0.0 shows as 0
    shown = sum(c.isdigit() or c == "-" for c in text)
    if shown > digits:
        return _dashes(digits, decimals)
    return text.rjust(width)


def _dashes(digits: int, decimals: int) -> str:
    if decimals:
        return "-" * (digits - decimals) + "." + "-" * decimals
    return "-" * digits


@register("counter_display")
class CounterDisplay(Component):
    FAULTS = ("stuck_off",)  # a dead display: blank whatever the value

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.value = 0.0
        self.text = display_text(0.0, self.config.digits, self.config.decimals)

    def step(self, dt: float) -> None:
        self.value = float(self.read("value", default=0.0))
        dead = self.fault is not None and self.fault.kind == "stuck_off"
        width = self.config.digits + (1 if self.config.decimals else 0)
        self.text = (
            " " * width
            if dead
            else display_text(self.value, self.config.digits, self.config.decimals)
        )

    def state(self) -> dict[str, Any]:
        return {"text": self.text, "value": self.value}

    def geometry(self) -> dict[str, Any]:
        c = self.config
        return {**super().geometry(), "digits": c.digits, "decimals": c.decimals, "color": c.color}
