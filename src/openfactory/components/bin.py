"""Bin (PLAN 5.7a, ``bin``; ADR-0006): a silo or a hopper.

Behavior spec
-------------
A node of the solids network (``core/solidsnet.py``) holding a mass of one ``material`` (a name in
the scene's ``materials:``, whose bulk density sets its capacity and its level). Its inside is a
cylinder of ``diameter`` or a box of ``length`` × ``width``, with ``height`` of straight wall over
a cone or a pyramid of ``cone_height`` down to an outlet ``outlet_diameter`` across (a flat
bottom when ``cone_height`` is 0). It starts with ``initial_mass_kg``. Links take from its outlet
and put into its top; it never holds more than its volume × bulk density (a link into it when
full is blocked). The surface is flat: the level is the height, from the outlet, of the volume
(mass / bulk density) through its shape.

Ports: ``level`` (AI, m, 0 to ``cone_height + height``), ``weight`` (AI, kg: the contents plus
``tare_kg``, as load cells read it; range 0 to ``load_cell_kg``, default full plus tare), and two
level switches: ``hi_sw`` on at ``hi_level`` (default 90 % of the height) and off 0.02 m lower,
``lo_sw`` on at ``lo_level`` (default 10 %) and off 0.02 m higher. They read this tick's mass.

Faults: ``bridge`` (the material bridges over the outlet: it gives nothing while it holds mass,
until the fault is cleared), ``stuck_on``/``stuck_off`` (the switches), ``drift`` (the analog
inputs).

Geometry: origin at the centre of the outlet, the outlet at the origin's height; the top of the
wall at ``cone_height + height`` above it.
"""

from __future__ import annotations

from typing import Any

from openfactory.core import solidsnet
from openfactory.core.component import Component, num, register

HYSTERESIS_M = 0.02


@register("bin")
class Bin(Component):
    FAULTS = ("bridge", "stuck_on", "stuck_off", "drift")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        cfg = self.config
        self.shape = cfg.shape_of()  # type: ignore[attr-defined]
        self.density = cfg._density  # type: ignore[attr-defined]
        if self.density is None:
            raise ValueError(f"{self.id}: names no declared material (lint E106, E107)")
        self.node = solidsnet.Bin(self.id, capacity=cfg.capacity_kg(),  # type: ignore[attr-defined]
                                  mass=float(cfg.initial_mass_kg))  # type: ignore[attr-defined]  # fmt: skip
        self.hi = False
        self.lo = False
        self.level = self.shape.level(self.node.mass / self.density)
        self._switch(self.level)

    def solids_nodes(self) -> list[solidsnet.Node]:
        return [self.node]

    def _switch(self, level: float) -> None:
        cfg = self.config
        high, low = cfg.high(), cfg.low()  # type: ignore[attr-defined]
        self.hi = level >= high if not self.hi else level > high - HYSTERESIS_M
        self.lo = level <= low if not self.lo else level < low + HYSTERESIS_M

    def step(self, dt: float) -> None:
        self.node.bridged = self.fault is not None and self.fault.kind == "bridge"
        self.level = self.shape.level(self.node.mass / self.density)
        self._switch(self.level)
        self.write("level", self.level)
        self.write("weight", self.node.mass + self.config.tare_kg)  # type: ignore[attr-defined]
        self.write("hi_sw", self.hi)
        self.write("lo_sw", self.lo)

    def state(self) -> dict[str, Any]:
        cap = self.node.capacity
        return {"mass_kg": self.node.mass, "level_m": self.level,
                "fill": self.node.mass / cap if cap > 0 else 0.0,
                "bridged": self.node.bridged}  # fmt: skip

    @classmethod
    def describe(cls, state: dict[str, Any], geometry: dict[str, Any]) -> str:
        """One sentence for ``openfactory look`` (PLAN 5.14b; :meth:`Component.describe`)."""
        text = (f"{num(state['mass_kg'], 1)} kg, level {num(state['level_m'])} m, "
                f"{num(state['fill'] * 100, 0)} % full")  # fmt: skip
        return text + (", bridged over its outlet" if state.get("bridged") else "")

    def geometry(self) -> dict[str, Any]:
        cfg = self.config
        return {**super().geometry(), "shape": cfg.shape, "diameter": cfg.diameter,  # type: ignore[attr-defined]
                "length": cfg.length, "width": cfg.width, "height": cfg.height,  # type: ignore[attr-defined]
                "cone_height": cfg.cone_height, "outlet_diameter": cfg.outlet_diameter,  # type: ignore[attr-defined]
                "material": cfg.material, "capacity_kg": self.node.capacity}  # type: ignore[attr-defined]  # fmt: skip
