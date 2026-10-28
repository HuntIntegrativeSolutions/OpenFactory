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

``duration_s`` is not a check; it is how long ``openfactory test`` runs.
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
            results.append(CheckResult(f"parts_reached {remover}: {name}", passed, detail))
    if checks.none_lost:
        lost = dict(world.lost)
        results.append(
            CheckResult("none_lost", not lost, f"lost {lost}" if lost else "no part lost")
        )
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
        results.append(CheckResult(f"max_cycle_s {limit:g}", passed, detail))
    return results


def format_results(results: list[CheckResult]) -> str:
    lines = [f"  {'PASS' if r.passed else 'FAIL'}  {r.name}: {r.detail}" for r in results]
    passed = sum(r.passed for r in results)
    verdict = "PASS" if passed == len(results) else "FAIL"
    return "\n".join(["checks:", *lines, f"{verdict}: {passed} of {len(results)} checks passed"])
