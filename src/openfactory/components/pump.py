"""Pump (PLAN 3.2, ``pump``; ADR-0005).

Behavior spec
-------------
A centrifugal pump, a link of the flow network from its suction (``from``) to its discharge
(``to``). Its head gain at flow ``q`` and relative speed ``ω`` is ``ω²·h0 − c·ω^(2−n)·qⁿ``
(EPANET 2.2 equation 12.2), fitted to ``curve``'s three points or, from the design point alone,
EPANET's single-point curve: shutoff head 133 % of ``design_head_m`` and zero head at twice
``design_flow_m3h``. It never runs backwards, and a pump that cannot lift against its discharge
head delivers nothing (a check valve).

While ``run`` (DO) is on, its speed ramps to ``speed`` (AO, 0 to 100 %; unbound: 100 %) over
``ramp_s`` from stopped to full; off, it ramps down the same way. ``running`` (DI) follows ``run``
in the same tick, the contactor's auxiliary contact, as on ``belt_conveyor``; ``flow`` (AI, m³/h,
0 to the curve's zero-head flow) is what it delivers. A pump drawing from a tank that runs dry
delivers nothing until the tank refills (``dry`` in its state). Shaft power for a
``vfd_motor``'s overload: ``ρ·g·q·H / efficiency``.

Faults: ``jammed`` (seized: no flow; ``running`` stays honest), ``slow`` (a worn impeller: its
head times ``factor``), ``stuck_on``/``stuck_off`` (``running``), ``drift``/``noisy``
(``flow``).

Geometry: the origin is the pump centre; it is drawn joined to its ends.
"""

from __future__ import annotations

from typing import Any

from openfactory.components._process import link_points
from openfactory.core import flownet
from openfactory.core.component import Component, register


@register("pump")
class Pump(Component):
    FAULTS = ("jammed", "slow", "stuck_on", "stuck_off", "drift", "noisy")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        cfg = self.config
        self.shutoff, self.coefficient, self.exponent = flownet.pump_curve(cfg.points_m3s())  # type: ignore[attr-defined]
        self.link = flownet.Pump(
            self.id,
            cfg.from_,  # type: ignore[attr-defined]
            cfg.to,  # type: ignore[attr-defined]
            position=tuple(cfg.position),  # type: ignore[arg-type]
            shutoff=self.shutoff,
            coefficient=self.coefficient,
            exponent=self.exponent,
            speed=0.0,
            running=False,
        )
        self.speed = 0.0  # relative, 0 to 1
        self.run = False
        #: Set by a vfd_motor driving this pump: (run, speed 0 to 1), in place of the ports.
        self.drive: tuple[bool, float] | None = None
        self.points: list[list[float]] = []

    def flow_links(self) -> list[flownet.Link]:
        return [self.link]

    def flow_built(self, by_id: dict[str, Any]) -> None:
        self.points = link_points(by_id, self, [list(self.config.position)])

    def step(self, dt: float) -> None:
        if self.drive is not None:
            self.run, target = self.drive
        else:
            self.run = bool(self.read("run", default=False))
            target = float(self.read("speed", default=100.0)) / 100 if self.run else 0.0
        target = min(max(target, 0.0), 1.0) if self.run else 0.0
        step = dt / self.config.ramp_s  # type: ignore[attr-defined]
        if self.speed < target:
            self.speed = min(target, self.speed + step)
        elif self.speed > target:
            self.speed = max(target, self.speed - step)
        fault = self.fault.kind if self.fault is not None else None
        worn = float(self.fault.params["factor"]) if fault == "slow" else 1.0  # type: ignore[union-attr]
        self.link.shutoff = self.shutoff * worn
        self.link.coefficient = self.coefficient * worn
        self.link.speed = self.speed
        self.link.running = self.speed > 0 and fault != "jammed"
        self.write("running", self.run)
        self.write("flow", self.link.flow * 3600)

    @property
    def dry(self) -> bool:
        node, nozzle = self.link.start  # type: ignore[misc]
        return isinstance(node, flownet.Tank) and node.dry(nozzle)  # type: ignore[arg-type]

    def head_m(self) -> float:
        """Head the pump adds at its current flow, m."""
        return self.link.gain(self.link.flow) if self.link.running else 0.0

    def power_kw(self) -> float:
        """Shaft power, kW: ρ·g·q·H / efficiency."""
        q = max(self.link.flow, 0.0)
        hydraulic = flownet.DENSITY * flownet.GRAVITY * q * self.head_m()
        return hydraulic / self.config.efficiency / 1000  # type: ignore[attr-defined]

    def state(self) -> dict[str, Any]:
        return {
            "running": self.run,
            "speed": self.speed * 100,
            "flow_m3h": self.link.flow * 3600,
            "dry": self.dry,
        }

    def geometry(self) -> dict[str, Any]:
        return {**super().geometry(), "points": self.points}
