"""Transmitter (PLAN 3.2, ``transmitter``; ADR-0005 decision 7).

Behavior spec
-------------
A generic 4-20 mA-style transmitter of one process value, ``measures``: a tank's ``level`` (m),
``temperature`` (°C) or ``fraction.<ingredient>`` (%), a link's ``flow`` (m³/h: a pipe, valve or
pump, positive from its ``from`` to its ``to``), or a junction's (or consumer drain's)
``pressure`` (bar, gauge). ``pv`` (AI) reads it over ``range`` in ``units`` (default by quantity),
so under ``int16_scaled`` raw 0 to 32767 spans the range as 4 to 20 mA would. ``lag_s`` is a
first-order filter; ``noise`` adds Gaussian noise of that fraction of the span, drawn from the
transmitter's own seeded generator so a run replays and no other random draw shifts. It reads
the value the network left this tick.

Faults: ``drift`` and ``noisy`` (as for any AI), ``stuck_on``/``stuck_off`` (pv reads the top or
the bottom of its range), ``signal_fail`` (the loop fails and pv reads the NAMUR NE 43 failure
level: 3.6 mA, a 40th of the span below its range, or with ``high=1`` 21 mA, 1/16 above it).
Under ``float32`` the PLC sees those values; under ``int16_scaled`` the raw count clamps at 0 or
32767 (ADR-0003), so a PLC sees the bottom or the top of the range.

Geometry: the origin is where it is drawn.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from openfactory.core.component import Component, register


@register("transmitter")
class Transmitter(Component):
    FAULTS = ("drift", "noisy", "stuck_on", "stuck_off", "signal_fail")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._read: Callable[[], float] = lambda: 0.0
        self.value: float | None = None
        self._noise = self.world.rng(f"{self.id}:noise")

    def flow_built(self, by_id: dict[str, Any]) -> None:
        name, quantity, *rest = self.config.measures.split(".")  # type: ignore[attr-defined]
        target = by_id[name]
        if quantity == "level":
            self._read = lambda: target.node.level
        elif quantity == "temperature":
            self._read = lambda: target.node.temperature
        elif quantity == "fraction":
            self._read = lambda: target.node.fractions.get(rest[0], 0.0) * 100
        elif quantity == "flow":
            self._read = lambda: target.link.flow * 3600
        else:
            self._read = lambda: target.node.pressure_bar

    def step(self, dt: float) -> None:
        cfg = self.config
        lo, hi = cfg.range  # type: ignore[attr-defined]
        span = hi - lo
        measured = self._read()
        lag = cfg.lag_s  # type: ignore[attr-defined]
        if self.value is None or lag <= 0:
            self.value = measured
        else:
            self.value += (measured - self.value) * min(dt / lag, 1.0)
        pv = self.value
        if cfg.noise > 0:  # type: ignore[attr-defined]
            pv += self._noise.gauss(0.0, cfg.noise * span)  # type: ignore[attr-defined]
        kind = self.fault.kind if self.fault is not None else None
        if kind == "stuck_on":
            pv = hi
        elif kind == "stuck_off":
            pv = lo
        elif kind == "signal_fail":
            high = bool(self.fault.params["high"])  # type: ignore[union-attr]
            pv = lo + span * (17 / 16 if high else -0.4 / 16)  # 21 mA or 3.6 mA on 4-20 mA
        self.write("pv", pv)

    def state(self) -> dict[str, Any]:
        return {"pv": self.raw.get("pv", 0.0)}

    def geometry(self) -> dict[str, Any]:
        return {**super().geometry(), "measures": self.config.measures}  # type: ignore[attr-defined]
