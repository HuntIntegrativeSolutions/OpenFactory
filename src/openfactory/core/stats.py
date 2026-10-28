"""Run statistics (PLAN 2.11): the plant manager's view of a run, from counters the core keeps.

Behavior spec
-------------
:class:`SceneStats` observes a :class:`~openfactory.core.loader.LoadedScene` after every tick
(:meth:`SceneStats.observe`) and reports (:meth:`SceneStats.report`):

- **throughput** per part remover, by part name: parts taken, and parts per hour of simulated time;
- **utilization** per conveyor (belt, roller, turntable deck): the share of ticks its ``run``
  command was on;
- **cycles** per actuator (pusher, stopper, diverter arm, turntable, pick and place): completed
  cycles and their mean time. A cycle starts when the actuator leaves its home position and ends
  when it is back: a pusher retracted, an arm or deck at 0 degrees, a gantry's carriage at the
  pick side (one trip to the place side and back per part; the pick's lower and rise come before
  it leaves), a stopper fully raised (its holding position). A stopper starts lowered, so its first
  rise is not a cycle;
- **dwell** of the parts removed, from the tick they spawned to the tick a remover took them:
  mean and max seconds, plus the oldest part still in the scene;
- **lost** parts by name (fallen out of the scene).

It uses only state the components already keep (PLAN 2.11: counters and timers, not
discrete-event simulation). A remover reports each part it takes (:attr:`World.removals`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Conveyor types whose ``run`` command is counted for utilization.
CONVEYOR_TYPES = ("belt_conveyor", "roller_conveyor", "turntable")
_EPS = 1e-9


def _tick_s(loaded: Any) -> float:
    return loaded.time / loaded.tick if loaded.tick else 0.0


def _home(comp: Any) -> bool | None:
    """True at the actuator's home position, False away from it, None for other types."""
    kind = comp.type
    if kind == "pusher":
        return comp.position <= _EPS
    if kind in ("diverter_arm", "turntable"):
        return abs(comp.angle) <= _EPS
    if kind == "stopper":
        return comp.position >= 1.0 - _EPS
    if kind == "pick_and_place":
        return comp.x <= _EPS  # at the pick side: lowering there to pick is part of the cycle
    return None


@dataclass
class _Cycles:
    count: int = 0
    total_s: float = 0.0
    left_at: float | None = None  # sim time it left home in the cycle under way
    seen_home: bool = False


@dataclass
class SceneStats:
    loaded: Any
    ticks: int = 0
    run_ticks: dict[str, int] = field(default_factory=dict)
    cycles: dict[str, _Cycles] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for comp in self.loaded.components:
            if comp.type in CONVEYOR_TYPES:
                self.run_ticks[comp.id] = 0
            if _home(comp) is not None:
                self.cycles[comp.id] = _Cycles()

    def observe(self) -> None:
        """Count one tick; call after each :meth:`LoadedScene.step`."""
        self.ticks += 1
        now = self.loaded.time
        for comp in self.loaded.components:
            if comp.id in self.run_ticks and comp.read("run", default=False):
                self.run_ticks[comp.id] += 1
            c = self.cycles.get(comp.id)
            if c is None:
                continue
            home = bool(_home(comp))
            if home:
                if c.left_at is not None:
                    c.count += 1
                    c.total_s += now - c.left_at
                    c.left_at = None
                c.seen_home = True
            elif c.seen_home and c.left_at is None:
                c.left_at = now

    def dwell_s(self) -> list[float]:
        dt = _tick_s(self.loaded)
        return [(r.tick - r.spawn_tick) * dt for r in self.loaded.world.removals]

    def oldest_s(self) -> float:
        """Age of the oldest part still in the scene, seconds (0 with none)."""
        world, dt = self.loaded.world, _tick_s(self.loaded)
        return max(((world.tick - p.spawn_tick) * dt for p in world.parts.values()), default=0.0)

    def report(self) -> dict[str, Any]:
        loaded = self.loaded
        hours = loaded.time / 3600.0
        throughput: dict[str, dict[str, Any]] = {}
        for comp in loaded.components:
            if comp.type == "part_remover":
                by_name = dict(sorted(comp.removed.items()))
                per_hour = {n: round(k / hours, 1) if hours else 0.0 for n, k in by_name.items()}
                throughput[comp.id] = {"parts": by_name, "per_hour": per_hour}
        dwell = self.dwell_s()
        return {
            "sim_time_s": round(loaded.time, 3),
            "throughput": throughput,
            "utilization_pct": {
                cid: round(100.0 * n / self.ticks, 1) if self.ticks else 0.0
                for cid, n in self.run_ticks.items()
            },
            "cycles": {
                cid: {
                    "count": c.count,
                    "mean_s": round(c.total_s / c.count, 3) if c.count else None,
                }
                for cid, c in self.cycles.items()
            },
            "dwell_s": {
                "removed": len(dwell),
                "mean": round(sum(dwell) / len(dwell), 2) if dwell else None,
                "max": round(max(dwell), 2) if dwell else None,
                "oldest_in_scene": round(self.oldest_s(), 2),
            },
            "lost": dict(loaded.world.lost),
        }


def format_report(report: dict[str, Any]) -> str:
    """The statistics block ``run`` and ``test`` print, one line per item."""
    lines = [f"statistics over {report['sim_time_s']:g} s simulated:"]
    for rid, t in report["throughput"].items():
        parts = ", ".join(f"{n} {k} ({t['per_hour'][n]:g}/h)" for n, k in t["parts"].items())
        lines.append(f"  throughput {rid}: {parts or 'none'}")
    for cid, pct in report["utilization_pct"].items():
        lines.append(f"  utilization {cid}: {pct:g} % of the run with run on")
    for cid, c in report["cycles"].items():
        mean = f", mean {c['mean_s']:g} s" if c["mean_s"] is not None else ""
        lines.append(f"  cycles {cid}: {c['count']}{mean}")
    d = report["dwell_s"]
    if d["removed"]:
        lines.append(
            f"  dwell (spawn to remover): {d['removed']} parts, mean {d['mean']:g} s, "
            f"max {d['max']:g} s; oldest still in the scene {d['oldest_in_scene']:g} s"
        )
    else:
        lines.append(f"  dwell: no part removed; oldest in the scene {d['oldest_in_scene']:g} s")
    lines.append(f"  lost: {report['lost'] or 'none'}")
    return "\n".join(lines)
