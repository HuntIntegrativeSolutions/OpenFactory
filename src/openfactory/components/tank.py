"""Tank (PLAN 3.2, ``tank``; ADR-0005).

Behavior spec
-------------
An open tank of constant cross-section in the flow network (``core/flownet.py``): a vertical
cylinder of ``diameter`` or a box of ``length`` × ``width``, ``height`` tall, its bottom at the
origin's height. Its liquid starts at ``initial_level`` and ``initial_temperature_c``. Links
connect to its nozzles, ``TK_01.bottom`` (height 0), ``TK_01.top`` (the tank's height) and any
named in ``nozzles``; the solver moves its volume each tick (a nozzle at or above the surface
passes no outflow, liquid entering through it falls freely), and the tank overflows at its
height: the level holds and the excess is counted. Liquid is well mixed: one temperature and
one set of ingredient fractions; what it starts with is water.

Ports: ``level`` (AI, m, 0 to ``height``), ``temp`` (AI, °C, 0 to 100), and two level switches.
``hi_sw`` turns on at ``hi_level`` (default 90 % of height) and off 0.02 m below it; ``lo_sw``
turns on at ``lo_level`` (default 10 %) and off 0.02 m above it, so a level hovering at a switch
point does not chatter. The switches read the level the solver left this tick. In the shipped
scenes they are tagged by ISA-5.1 function, ``TK_02_LSH_DI`` and ``TK_02_LSL_DI``.

Faults: ``stuck_on``/``stuck_off`` (the switches), ``drift`` and ``noisy`` (the analog inputs),
``misaligned`` (the level reads ``offset_m`` high, switches included, as a level transmitter or
float set wrong).

Geometry: origin at the centre of the tank bottom. A nozzle given as ``{height, bearing_deg}``
(PLAN 4.1g2) is fixed on the wall at that bearing (degrees about +y in the tank's frame from
local +x, as ``rotation_deg``): a cylinder's at its radius, a box's where the bearing's ray meets
the wall. ``top`` is the centre of the top; any other nozzle faces the link that joins it.
"""

from __future__ import annotations

import math
from typing import Any

from openfactory.core import flownet
from openfactory.core.component import Component, num, register
from openfactory.core.snaps import nozzle_wall

#: Switch deadband, m: a switch turns off this far back from where it turned on.
DEADBAND = 0.02


@register("tank")
class Tank(Component):
    FAULTS = ("stuck_on", "stuck_off", "drift", "noisy", "misaligned")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        cfg = self.config
        area = cfg.area()  # type: ignore[attr-defined]
        self.node = flownet.Tank(
            self.id,
            cfg.position[1],
            area,
            cfg.height,  # type: ignore[attr-defined]
            area * cfg.initial_level,  # type: ignore[attr-defined]
            cfg.all_nozzles(),  # type: ignore[attr-defined]
            temperature=cfg.initial_temperature_c,  # type: ignore[attr-defined]
            fractions={"water": 1.0},  # what it starts with is water (PLAN 3.1 mixing)
        )
        self.high_level, self.low_level = cfg.high(), cfg.low()  # type: ignore[attr-defined]
        self.hi = self.node.level >= self.high_level
        self.lo = self.node.level <= self.low_level
        self._overflow = 0.0
        self.overflowing = False

    def flow_nodes(self) -> list[flownet.Tank]:
        return [self.node]

    def nozzle_point(self, nozzle: str, toward: tuple[float, float, float]) -> list[float]:
        """Where a pipe meets ``nozzle`` (for the viewer, and a pipe's drawn length): the top's
        centre; a nozzle with a fixed ``bearing_deg`` (PLAN 4.1g2) on the wall at that bearing
        (a cylinder at its radius, a box where the bearing's ray meets the wall); any other on
        the wall facing ``toward``. A nozzle at the bottom sits 0.05 m up, clear of the floor."""
        x, y, z = self.config.position
        height = self.node.nozzles[nozzle]
        if height >= self.node.height - 1e-9 and nozzle == "top":
            return [x, y + height, z]
        wall = nozzle_wall(self.config, nozzle)  # a fixed bearing (core/snaps.py, one copy)
        if wall is not None:
            return [float(v) for v in wall]
        if height >= self.node.height - 1e-9:
            return [x, y + height, z]  # a free nozzle at the top's height: as top, as before
        cfg = self.config
        box = cfg.shape == "box"  # type: ignore[attr-defined]
        dx, dz = toward[0] - x, toward[2] - z
        dist = math.hypot(dx, dz)
        reach = min(cfg.length, cfg.width) / 2 if box else cfg.diameter / 2  # type: ignore[attr-defined,type-var]
        ux, uz = (dx / dist, dz / dist) if dist > 1e-9 else (1.0, 0.0)
        return [x + ux * reach, y + max(height, 0.05), z + uz * reach]

    def step(self, dt: float) -> None:
        level = self.node.level
        if self.fault is not None and self.fault.kind == "misaligned":
            seen = level + float(self.fault.params["offset_m"])
        else:
            seen = level
        if seen >= self.high_level:
            self.hi = True
        elif seen < self.high_level - DEADBAND:
            self.hi = False
        if seen <= self.low_level:
            self.lo = True
        elif seen > self.low_level + DEADBAND:
            self.lo = False
        self.overflowing = self.node.overflow > self._overflow
        self._overflow = self.node.overflow
        self.write("level", level)  # misaligned adds its offset to the AI in write()
        self.write("hi_sw", self.hi)
        self.write("lo_sw", self.lo)
        self.write("temp", self.node.temperature)

    def state(self) -> dict[str, Any]:
        return {
            "level_m": self.node.level,
            "volume_m3": self.node.volume,
            "temperature_c": self.node.temperature,
            "overflowing": self.overflowing,
        }

    @classmethod
    def describe(cls, state: dict[str, Any], geometry: dict[str, Any]) -> str:
        """One sentence for ``openfactory look`` (PLAN 5.14b; :meth:`Component.describe`)."""
        level, height = float(state["level_m"]), float(geometry["height"])
        out = f"level {num(level)} m of {num(height)} m, {num(state['volume_m3'], 3)} m3"
        temperature = state.get("temperature_c")
        if temperature is not None:
            out += f", {num(temperature, 1)} degC"
        return out + (", overflowing" if state["overflowing"] else "")

    def geometry(self) -> dict[str, Any]:
        cfg = self.config
        return {
            **super().geometry(),
            "shape": cfg.shape,  # type: ignore[attr-defined]
            "diameter": cfg.diameter,  # type: ignore[attr-defined]
            "length": cfg.length,  # type: ignore[attr-defined]
            "width": cfg.width,  # type: ignore[attr-defined]
            "height": cfg.height,  # type: ignore[attr-defined]
            "nozzles": self.node.nozzles,
        }
