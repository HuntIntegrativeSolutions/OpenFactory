"""Selector switch (PLAN §5, ``selector_switch``).

Behavior spec
-------------
A maintained two- or three-position switch. Two-position switches have positions ``a`` and ``b``;
three-position switches add ``center`` between them (for example HAND–OFF–AUTO). ``pos_a`` (DI) is
true only in position ``a`` and ``pos_b`` (DI) only in position ``b``, so both are false at center.
The switch starts at ``initial``; its outputs are written from the first step. A viewer action calls
:meth:`SelectorSwitch.set_position`, possibly from the server thread; the newest request is applied
on the next step. Asking a two-position switch for ``center`` raises ``ValueError``.

Geometry: the origin is the center of the switch face.
"""

from __future__ import annotations

import threading
from typing import Any

from openfactory.core.component import Component, register


@register("selector_switch")
class SelectorSwitch(Component):
    FAULTS = ("stuck_on", "stuck_off")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.position: str = self.config.initial
        self._requested: str | None = None
        self._lock = threading.Lock()

    def valid_positions(self) -> tuple[str, ...]:
        return ("a", "center", "b") if self.config.positions == 3 else ("a", "b")

    def set_position(self, position: str) -> None:
        """Request a new position; applied on the next step."""
        if position not in self.valid_positions():
            raise ValueError(f"{self.id}: position {position!r} not in {self.valid_positions()}")
        with self._lock:
            self._requested = position

    def step(self, dt: float) -> None:
        with self._lock:
            requested, self._requested = self._requested, None
        if requested is not None:
            self.position = requested
        self.write("pos_a", self.position == "a")
        self.write("pos_b", self.position == "b")

    def state(self) -> dict[str, Any]:
        return {"position": self.position}

    def geometry(self) -> dict[str, Any]:
        return {**super().geometry(), "positions": self.config.positions}
