"""Exercise checks (PLAN 2.11): grade a run against a scene's ``checks:`` block.

Behavior spec
-------------
:func:`evaluate` reads the end state of a run (the world's removal log, the parts still in the
scene, and the lost count) and returns one :class:`CheckResult` per check the scene declares:

- ``parts_reached: {REMOVER: name | [names]}``, one result per remover and name: at least one part
  of that name reached that remover, and none reached another. A stopped line therefore fails
  (nothing reached anything), as does a sorter that sends a part the wrong way.
- ``none_lost: true``: no part fell out of the scene.
- ``max_cycle_s: S``: every removed part took at most S seconds from its spawn tick to the tick a
  remover took it, and no part still in the scene at the end is older than S. A jammed line, where
  nothing is removed, fails on the parts piling up behind the jam.

Process checks (PLAN 3.3, ADR-0005 decision 10), from the run's
:class:`~openfactory.core.process_watch.ProcessWatch`:

- ``level_within: {TANK: {band: [lo, hi], after_s: T}}``: from T to the end the level stays in the
  band, and the run reached T (a run that ends first fails, as does a tank that never filled).
- ``never_overflow: [TANK, ...]``: the tank never overflowed.
- ``never_dry: [PUMP, ...]``: the pump never ran while its suction tank was dry.
- ``max_starts: {PUMP: N}``: the pump started at most N times, and at least once (a pump that
  never ran fails: the easy pass is closed, as for ``parts_reached``).
- ``max_reversals: {VALVE_OR_PUMP: N}``: its command (a control valve's position, an on/off
  valve's open command or a pump's speed setpoint while it runs, each as 0 to 100 %) turned back
  at most N times, counting only turns back by more than 10 % of the span
  (:data:`~openfactory.core.process_watch.REVERSAL_DEADBAND_PCT`), and it moved at least once.
- ``fractions_within: {TANK: {fractions: {INGREDIENT: [lo, hi]}, after_s: T}}``: from T to the
  end, each ingredient's share of the tank's volume (%) stays in its band, the tank is never
  empty, and the run reached T. An ingredient no inflow brought is 0 %.
- ``temperature_within: {TANK: {band: [lo, hi], after_s: T}}``: from T to the end the tank's
  temperature (°C) stays in the band, the tank is never empty, and the run reached T. An empty
  tank fails both: it holds no batch to grade.

``duration_s`` is not a check; it is how long ``openfactory test`` runs.

Live (PLAN 4.1i, 2026-11-13): each result also says whether a failure is ``settled``, that is,
running longer cannot undo it (a part reached the wrong remover, a part was lost, a part took or
has already taken longer than ``max_cycle_s``, a level left its band after ``after_s``, a tank
overflowed, a pump ran dry or started too often, a command reversed too often, a batch tank
emptied or left its band). :func:`live` turns that into what the viewer's checks panel shows
while a scene runs: ``failed`` as soon as a failure is settled, ``pending`` otherwise, and at
``duration_s`` (when ``openfactory test`` would grade the run) the graded result, ``passed`` or
``failed``, which then stays.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from openfactory.core.schema import Checks


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str
    #: A failure that running longer cannot undo (see the module docstring).
    settled: bool = False


def tick_s(loaded: Any) -> float:
    """Seconds per tick of a run (the scheduler's fixed dt), from its elapsed time."""
    return loaded.time / loaded.tick if loaded.tick else 0.0


def evaluate(checks: Checks, loaded: Any) -> list[CheckResult]:
    world, dt = loaded.world, tick_s(loaded)
    results: list[CheckResult] = []
    by_name: dict[str, Counter[str]] = defaultdict(Counter)  # part name -> remover -> count
    for r in world.removals:
        by_name[r.name][r.by] += 1
    for remover, names in checks.reached().items():
        for name in names:
            got = by_name.get(name, Counter())
            here, elsewhere = got[remover], {k: v for k, v in got.items() if k != remover}
            passed = here > 0 and not elsewhere
            if passed:
                detail = f"{here} reached {remover}, none anywhere else"
            elif here == 0 and not elsewhere:
                detail = f"no {name} part reached any remover"
            else:
                wrong = ", ".join(f"{v} to {k}" for k, v in sorted(elsewhere.items()))
                detail = f"{here} reached {remover}, but {wrong}"
            results.append(
                CheckResult(f"parts_reached {remover}: {name}", passed, detail, bool(elsewhere))
            )
    if checks.none_lost:
        lost = dict(world.lost)
        detail = f"lost {lost}" if lost else "no part lost"
        results.append(CheckResult("none_lost", not lost, detail, bool(lost)))
    if checks.max_cycle_s is not None:
        limit = checks.max_cycle_s
        dwell = [(r.tick - r.spawn_tick) * dt for r in world.removals]
        age = [(world.tick - p.spawn_tick) * dt for p in world.parts.values()]
        worst_dwell, oldest = max(dwell, default=0.0), max(age, default=0.0)
        passed = worst_dwell <= limit + 1e-9 and oldest <= limit + 1e-9
        detail = (
            f"longest spawn-to-remover {worst_dwell:.2f} s over {len(dwell)} parts; oldest part "
            f"still in the scene {oldest:.2f} s; limit {limit:g} s"
        )
        # a part over the limit, removed or not, can only fail: it only gets older
        results.append(CheckResult(f"max_cycle_s {limit:g}", passed, detail, not passed))
    results += _process(checks, loaded)
    return results


def _process(checks: Checks, loaded: Any) -> list[CheckResult]:
    watch = getattr(loaded, "watch", None)
    if not checks.watches_process():
        return []
    assert watch is not None, "a scene with process checks builds a ProcessWatch"
    out: list[CheckResult] = []
    for tid, spec in checks.level_within.items():
        lo, hi = spec.band
        band = watch.bands[tid]
        name = f"level_within {tid}: {lo:g} to {hi:g} m after {spec.after_s:g} s"
        if not band.reached:
            out.append(CheckResult(name, False, f"the run ended before {spec.after_s:g} s"))
            continue
        passed = lo - 1e-9 <= band.lo and band.hi <= hi + 1e-9
        out.append(CheckResult(name, passed, f"level {band.lo:.3f} to {band.hi:.3f} m", not passed))
    for tid in checks.never_overflow:
        spilled = loaded.component(tid).node.overflow
        detail = f"overflowed {spilled * 1000:.1f} L" if spilled else "never overflowed"
        out.append(CheckResult(f"never_overflow {tid}", spilled == 0, detail, spilled != 0))
    for pid in checks.never_dry:
        ticks = watch.dry_ticks[pid]
        detail = f"ran dry {ticks * tick_s(loaded):.2f} s" if ticks else "never ran dry"
        out.append(CheckResult(f"never_dry {pid}", ticks == 0, detail, ticks != 0))
    for pid, limit in checks.max_starts.items():
        starts = watch.starts[pid]
        detail = (
            f"{starts} start{'s' if starts != 1 else ''}; limit {limit}"
            if starts
            else ("never started")
        )
        out.append(
            CheckResult(f"max_starts {pid}: {limit}", 1 <= starts <= limit, detail, starts > limit)
        )
    for cid, limit in checks.max_reversals.items():
        rev = watch.reversals[cid]
        detail = (
            f"{rev.count} reversal{'s' if rev.count != 1 else ''}; limit {limit}"
            if rev.moved
            else "the command never moved"
        )
        passed = rev.moved and rev.count <= limit
        out.append(CheckResult(f"max_reversals {cid}: {limit}", passed, detail, rev.count > limit))
    for tid, fspec in checks.fractions_within.items():
        recipe = watch.fractions[tid]
        wanted = ", ".join(f"{n} {lo:g} to {hi:g} %" for n, (lo, hi) in fspec.fractions.items())
        name = f"fractions_within {tid}: {wanted} after {fspec.after_s:g} s"
        out.append(_recipe(name, recipe, fspec.fractions, "%"))
    for tid, tspec in checks.temperature_within.items():
        name = f"temperature_within {tid}: {tspec.band[0]:g} to {tspec.band[1]:g} °C after "
        name += f"{tspec.after_s:g} s"
        out.append(_recipe(name, watch.temperatures[tid], {"temperature": tspec.band}, "°C"))
    return out


def _recipe(
    name: str, recipe: Any, bands: dict[str, tuple[float, float]], unit: str
) -> CheckResult:
    if not recipe.reached:
        return CheckResult(name, False, f"the run ended before {recipe.after_s:g} s")
    if recipe.empty_at is not None:
        return CheckResult(name, False, f"the tank was empty at {recipe.empty_at:.2f} s", True)
    parts, passed = [], True
    for key, (lo, hi) in bands.items():
        seen = recipe.bands[key]
        passed &= lo - 1e-9 <= seen.lo and seen.hi <= hi + 1e-9
        label = "" if key == "temperature" else f"{key} "
        parts.append(f"{label}{seen.lo:.2f} to {seen.hi:.2f} {unit}")
    return CheckResult(name, passed, "; ".join(parts), not passed)


def live(checks: Checks, loaded: Any, graded: list[CheckResult] | None) -> list[dict[str, Any]]:
    """Each check's state for the viewer's live panel: the graded results once the run reached
    ``duration_s`` (``graded``), else ``failed`` for a settled failure and ``pending`` for the
    rest, each with ``evaluate``'s detail as of now."""
    if graded is not None:
        return [
            {"name": r.name, "state": "passed" if r.passed else "failed", "detail": r.detail}
            for r in graded
        ]
    return [
        {
            "name": r.name,
            "state": "failed" if r.settled and not r.passed else "pending",
            "detail": r.detail,
        }
        for r in evaluate(checks, loaded)
    ]


def format_results(results: list[CheckResult]) -> str:
    lines = [f"  {'PASS' if r.passed else 'FAIL'}  {r.name}: {r.detail}" for r in results]
    passed = sum(r.passed for r in results)
    verdict = "PASS" if passed == len(results) else "FAIL"
    return "\n".join(["checks:", *lines, f"{verdict}: {passed} of {len(results)} checks passed"])
