"""What a run's process checks need to see every tick (PLAN 3.3, ADR-0005 decision 10).

Behavior spec
-------------
Built only when a scene's ``checks:`` names a process check, and then observed after every tick
(``LoadedScene.step``), in constant memory: for each ``level_within`` tank, the lowest and highest
level from ``after_s`` on, and whether the run got there; for each pump, how many times it
started (``run`` turning on, from the drive's or the PLC's command) and how many ticks it ran
while its suction tank was dry. ``never_overflow`` reads the tank's overflow count at the end.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Simulated time within which a tick counts as at ``after_s`` (half a 50 Hz tick).
TIME_EPS = 0.01


@dataclass
class Band:
    lo: float = float("inf")
    hi: float = float("-inf")
    reached: bool = False


@dataclass
class ProcessWatch:
    after: dict[str, float]
    tanks: dict[str, Any]
    pumps: dict[str, Any]
    bands: dict[str, Band] = field(default_factory=dict)
    starts: dict[str, int] = field(default_factory=dict)
    dry_ticks: dict[str, int] = field(default_factory=dict)
    _was_on: dict[str, bool] = field(default_factory=dict)

    @classmethod
    def build(cls, checks: Any, components: list[Any]) -> ProcessWatch | None:
        if checks is None or not checks.watches_process():
            return None
        tanks = {c.id: c for c in components if c.type == "tank"}
        pumps = {c.id: c for c in components if c.type == "pump"}
        after = {tid: band.after_s for tid, band in checks.level_within.items()}
        watch = cls(after, tanks, pumps)
        watch.bands = {tid: Band() for tid in after}
        watch.starts = dict.fromkeys(pumps, 0)
        watch.dry_ticks = dict.fromkeys(pumps, 0)
        watch._was_on = dict.fromkeys(pumps, False)
        return watch

    def record(self, time: float) -> None:
        for tid, after_s in self.after.items():
            if time + TIME_EPS >= after_s:
                level = self.tanks[tid].node.level
                band = self.bands[tid]
                band.lo, band.hi, band.reached = min(band.lo, level), max(band.hi, level), True
        for pid, pump in self.pumps.items():
            on = bool(pump.run)
            if on and not self._was_on[pid]:
                self.starts[pid] += 1
            self._was_on[pid] = on
            if on and pump.dry:
                self.dry_ticks[pid] += 1
