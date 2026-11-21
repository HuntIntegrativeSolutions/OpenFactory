"""What a run's process checks need to see every tick (PLAN 3.3, ADR-0005 decision 10).

Behavior spec
-------------
Built only when a scene's ``checks:`` names a process check, and then observed after every tick
(``LoadedScene.step``), in constant memory: for each ``level_within`` tank, the lowest and highest
level from ``after_s`` on, and whether the run got there; for each pump, how many times it
started (``run`` turning on, from the drive's or the PLC's command) and how many ticks it ran
while its suction tank was dry; for each ``max_reversals`` valve or pump, how many times its
command turned back (:class:`Reversals`); for each ``fractions_within`` and ``temperature_within``
tank, the lowest and highest share of each named ingredient (%) and temperature (°C) from its
``after_s`` on, and the first time from then on it was empty (level at or below
:data:`EMPTY_LEVEL_M`: an empty tank holds no recipe, and its temperature is no batch's).
``never_overflow`` reads the tank's overflow count at the end.
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
#: A tank at or below this level is empty for the recipe checks (1 mm).
EMPTY_LEVEL_M = 0.001


@dataclass
class Band:
    lo: float = float("inf")
    hi: float = float("-inf")
    reached: bool = False

    def add(self, value: float) -> None:
        self.lo, self.hi, self.reached = min(self.lo, value), max(self.hi, value), True


@dataclass
class Recipe:
    """What a recipe check saw of one tank from its ``after_s``: a band per watched quantity
    (an ingredient's share in %, or ``temperature`` in °C) and when it was first empty."""

    after_s: float
    bands: dict[str, Band]
    reached: bool = False
    empty_at: float | None = None


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


#: A bin holding no more than this is empty, and one this close to its capacity is full, kg.
EMPTY_MASS_KG = 1e-6


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
    fractions: dict[str, Recipe] = field(default_factory=dict)
    temperatures: dict[str, Recipe] = field(default_factory=dict)
    _was_on: dict[str, bool] = field(default_factory=dict)
    #: Bins (PLAN 5.7a): the first time each was empty and full, and each batch's mass.
    bins: dict[str, Any] = field(default_factory=dict)
    empty_at: dict[str, float | None] = field(default_factory=dict)
    full_at: dict[str, float | None] = field(default_factory=dict)
    least: dict[str, float] = field(default_factory=dict)
    most: dict[str, float] = field(default_factory=dict)
    batches: dict[str, list[float]] = field(default_factory=dict)
    _outlets: dict[str, list[Any]] = field(default_factory=dict)
    _taking: dict[str, bool] = field(default_factory=dict)

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
        watch.fractions = {
            tid: Recipe(spec.after_s, {name: Band() for name in spec.fractions})
            for tid, spec in checks.fractions_within.items()
        }
        watch.temperatures = {
            tid: Recipe(spec.after_s, {"temperature": Band()})
            for tid, spec in checks.temperature_within.items()
        }
        watched = [*checks.never_empty, *checks.never_full, *checks.batch_within]
        watch.bins = {bid: by_id[bid] for bid in dict.fromkeys(watched)}
        for bid, comp in watch.bins.items():
            watch.empty_at[bid] = watch.full_at[bid] = None
            watch.least[bid] = watch.most[bid] = comp.node.mass
            watch.batches[bid] = []
            watch._outlets[bid] = [
                c.link
                for c in components
                if getattr(c, "solids_links", None) and c.config.from_ == bid
            ]
            watch._taking[bid] = False
        return watch

    def record(self, time: float) -> None:
        for tid, after_s in self.after.items():
            if time + TIME_EPS >= after_s:
                self.bands[tid].add(self.tanks[tid].node.level)
        for watched, read in ((self.fractions, _shares), (self.temperatures, _temperature)):
            for tid, recipe in watched.items():
                if time + TIME_EPS < recipe.after_s:
                    continue
                node = self.tanks[tid].node
                recipe.reached = True
                if node.level <= EMPTY_LEVEL_M:
                    if recipe.empty_at is None:
                        recipe.empty_at = time
                    continue
                for name, value in read(node, recipe.bands).items():
                    recipe.bands[name].add(value)
        for pid, pump in self.pumps.items():
            on = bool(pump.run)
            if on and not self._was_on[pid]:
                self.starts[pid] += 1
            self._was_on[pid] = on
            if on and pump.dry:
                self.dry_ticks[pid] += 1
        for cid, comp in self.commanded.items():
            self.reversals[cid].record(comp.command)  # each type's command, 0 to 100 %
        for bid, comp in self.bins.items():
            node = comp.node
            mass = node.mass
            self.least[bid] = min(self.least[bid], mass)
            self.most[bid] = max(self.most[bid], mass)
            if mass <= EMPTY_MASS_KG and self.empty_at[bid] is None:
                self.empty_at[bid] = time
            if mass >= node.capacity - EMPTY_MASS_KG and self.full_at[bid] is None:
                self.full_at[bid] = time
            out = sum(lk.moved for lk in self._outlets[bid])
            if out > 0 and not self._taking[bid]:  # a link starts taking: a batch
                self.batches[bid].append(mass + out)  # its mass before this tick's take
            self._taking[bid] = out > 0


def _shares(node: Any, bands: dict[str, Band]) -> dict[str, float]:
    return {name: node.fractions.get(name, 0.0) * 100 for name in bands}


def _temperature(node: Any, bands: dict[str, Band]) -> dict[str, float]:
    return {"temperature": node.temperature}
