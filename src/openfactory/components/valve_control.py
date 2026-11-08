"""Control valve (PLAN 3.2, ``valve_control``; ADR-0005 decision 6).

Behavior spec
-------------
A throttling valve, a link of the flow network from ``from`` to ``to``. The PLC commands its
``position`` (AO, 0 to 100 %); the stem follows with a first-order lag (``lag_s``) and at most
100 % per ``stroke_s`` (the actuator's rate limit; ``slow`` scales it, ``jammed`` stops it), and
``position_fb`` (AI) reports where the stem is, so a program can alarm on deviation. Its flow is
``Q[m³/h] = Kv·f(x)·√(Δp[bar])`` at stem position ``x`` (0 to 1), with the inherent
characteristic ``f``: ``linear`` (``f = x``) or ``equal_percentage`` (``f = R^(x−1)``, R the
``rangeability``, and 0 when shut: the valve closes tight). ``cv`` or ``kv`` as on ``valve_onoff``.

``fail`` is where the stem goes while the command reads below 0 %: ``closed``, ``open``, or
``last`` (it holds). Only a ``float32`` AO can carry a value below 0 %: under ``int16_scaled``
the raw count clamps at 0 (ADR-0003), the command never reads below 0 %, and ``fail`` never acts.
With ``position`` unbound it is a hand valve held at ``opening`` (default 100 %). The stem starts
at its command. Its state reports the stem position and the command (%) and the flow (m³/h).

Faults: ``jammed`` (the stem sticks), ``slow``, ``drift`` and ``noisy`` (the position feedback).

Geometry: the origin is the valve body; it is drawn joined to its ends.
"""

from __future__ import annotations

import math
from typing import Any

from openfactory.components._process import link_points
from openfactory.core import flownet
from openfactory.core.component import Component, register


def capacity(characteristic: str, rangeability: float, x: float) -> float:
    """Relative flow capacity (0 to 1) at stem position ``x`` (0 to 1)."""
    if x <= 0:
        return 0.0
    if characteristic == "linear":
        return min(x, 1.0)
    return float(rangeability ** (min(x, 1.0) - 1))


@register("valve_control")
class ValveControl(Component):
    FAULTS = ("jammed", "slow", "drift", "noisy")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        cfg = self.config
        self.kv = cfg.kv_value()  # type: ignore[attr-defined]
        self.command = self._command()
        self.stem = self.command
        self.link = flownet.Resistance(
            self.id,
            cfg.from_,  # type: ignore[attr-defined]
            cfg.to,  # type: ignore[attr-defined]
            position=tuple(cfg.position),  # type: ignore[arg-type]
        )
        self._size()
        self.points: list[list[float]] = []

    def _command(self) -> float:
        """The command, %, with the fail position applied below 0 %."""
        cfg = self.config
        value = float(self.read("position", default=cfg.opening))  # type: ignore[attr-defined]
        if value < 0:
            fail = cfg.fail  # type: ignore[attr-defined]
            if fail == "closed":
                return 0.0
            if fail == "open":
                return 100.0
            return getattr(self, "stem", 0.0)
        return min(value, 100.0)

    def _size(self) -> None:
        cfg = self.config
        f = capacity(cfg.characteristic, cfg.rangeability, self.stem / 100)  # type: ignore[attr-defined]
        self.link.r = flownet.valve_resistance(self.kv, f)

    def flow_links(self) -> list[flownet.Link]:
        return [self.link]

    def flow_built(self, by_id: dict[str, Any]) -> None:
        self.points = link_points(by_id, self, [list(self.config.position)])

    def step(self, dt: float) -> None:
        cfg = self.config
        self.command = self._command()
        gap = self.command - self.stem
        limit = 100.0 / cfg.stroke_s * self.speed_factor  # type: ignore[attr-defined]
        lag = cfg.lag_s  # type: ignore[attr-defined]
        rate = min(abs(gap) / lag, limit) if lag > 0 else limit
        self.stem += math.copysign(min(rate * dt, abs(gap)), gap)
        self._size()
        self.write("position_fb", self.stem)

    def state(self) -> dict[str, Any]:
        return {"position": self.stem, "command": self.command, "flow_m3h": self.link.flow * 3600}

    def geometry(self) -> dict[str, Any]:
        return {**super().geometry(), "points": self.points, "kv": self.kv}
