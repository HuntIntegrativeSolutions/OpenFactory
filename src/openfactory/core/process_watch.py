"""What a run's process checks need to see every tick (PLAN 3.3, ADR-0005 decision 10).

Behavior spec
-------------
Built only when a scene's ``checks:`` names a process check, and then observed after every tick
(``LoadedScene.step``), in constant memory: for each ``level_within`` tank, the lowest and highest
level from ``after_s`` on, and whether the run got there; for each pump, how many times it
started (``run`` turning on, from the drive's or the PLC's command) and how many ticks it ran
while its suction tank was dry; for each ``max_reversals`` valve or pump, how many times its
command turned back (:class:`Reversals`). ``never_overflow`` reads the tank's overflow count at
the end.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Simulated time within which a tick counts as at ``after_s`` (half a 50 Hz tick).
TIME_EPS = 0.01
#: A command reverses only when it turns back by more than this, in % of its 0 to 100 % span:
#: transmitter noise passed through a controller's gain and small trims are not wear. Measured on
#: tank-level-pid (2026-11-08): its PI's command, which carries the level noise times KP, turns
#: back 7812 times in 300 s at 1 %, 1324 at 5 %, 123 at 8 % and 12 at 10 %; an on/off program on
#: the same valve 3390 times at every deadband from 0 to 10 %.
REVERSAL_DEADBAND_PCT = 10.0


@dataclass
class Band:
    lo: float = float("inf")
    hi: float = float("-inf")
    reached: bool = False


@dataclass
class Reversals:
    """Direction changes of one command (0 to 100 %): moving more than the deadband from where it
    started sets a direction; moving more than the deadband back from the furthest point reached
    in that direction is one reversal, and starts the other direction from there. ``moved`` is
    whether the command left its start at all."""

    start: float | None = None
    extreme: float = 0.0
    direction: int = 0
    count: int = 0
    deadband: float = REVERSAL_DEADBAND_PCT

    @property
    def moved(self) -> bool:
        return self.direction != 0

    def record(self, command: float) -> None:
        if self.start is None:
            self.start = self.extreme = command
            return
        if self.direction == 0:
            if abs(command - self.start) > self.deadband:
                self.direction = 1 if command > self.start else -1
                self.extreme = command
            return
        if (command - self.extreme) * self.direction > 0:
            self.extreme = command  # still going the same way
        elif abs(command - self.extreme) > self.deadband:
            self.count += 1
            self.direction = -self.direction
            self.extreme = command


@dataclass
class ProcessWatch:
    after: dict[str, float]
    tanks: dict[str, Any]
    pumps: dict[str, Any]
    bands: dict[str, Band] = field(default_factory=dict)
    starts: dict[str, int] = field(default_factory=dict)
    dry_ticks: dict[str, int] = field(default_factory=dict)
    commanded: dict[str, Any] = field(default_factory=dict)
    reversals: dict[str, Reversals] = field(default_factory=dict)
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
        by_id = {c.id: c for c in components}
        watch.commanded = {cid: by_id[cid] for cid in checks.max_reversals}
        watch.reversals = {cid: Reversals() for cid in checks.max_reversals}
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
        for cid, comp in self.commanded.items():
            self.reversals[cid].record(comp.command)  # each type's command, 0 to 100 %
