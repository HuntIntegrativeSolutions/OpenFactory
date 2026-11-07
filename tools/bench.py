"""Tick-cost gate (PLAN 2.9): the mean step time of fixed scenes against a committed baseline.

Usage::

    uv run python tools/bench.py             # measure, compare, exit 1 on a regression over 25 %
    uv run python tools/bench.py --update    # measure and rewrite tools/bench_baseline.json

Each scene in ``SCENES`` is loaded with every PLC output forced on, run ``WARM_TICKS`` ticks to its
steady state (200 parts), then measured for ``BLOCKS`` blocks of ``BLOCK_TICKS`` ticks. Before each
block the process runs a fixed pure-Python calibration workload (``calibrate``), and the score is
the scene's mean step time divided by the calibration's mean time over the same blocks. A slower
or faster machine, or the same machine on a slower day (about 30 % apart on the development
machine, PLAN 2.9), scales both, so the score moves with the code, not the CPU; raw milliseconds
are printed but not compared. Interleaving the two block by block keeps drift during the run out
of the score as well.

The gate fails when a scene's score is more than ``LIMIT`` times its baseline score. The baseline
is regenerated with ``--update`` and committed with the change that moves it (ruled 2026-11-04),
so a deliberate cost shows up in the diff of ``tools/bench_baseline.json``.

On GitHub Actions (``GITHUB_ACTIONS=true``) each scene's result line and its measurement as JSON
also go out as a workflow annotation (``::notice``, or ``::error`` on a failure). Annotations are
readable through the public check-runs API without signing in, unlike the job log, so the score a
CI runner measured can be read back and, if the runner's score differs from this machine's,
committed as the baseline (PLAN 2.9).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import statistics
import sys
import time
from bisect import insort
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BASELINE = REPO / "tools" / "bench_baseline.json"
#: The fixed scenes: many lanes at the 200-part cap, and two lanes queued nose to tail.
SCENES = {
    "load-200": REPO / "tests" / "scenes" / "load-200.yaml",
    "queue-200": REPO / "tests" / "scenes" / "queue-200.yaml",
}
DT = 0.02
WARM_TICKS = 3000
BLOCKS = 40
BLOCK_TICKS = 50
#: A score above the baseline's times this fails (a regression over 25 %, ruled 2026-11-04).
LIMIT = 1.25
#: Calibration rounds, sized so one calibration takes about as long as one block of ticks.
CALIBRATION_ROUNDS = 600


def calibrate(rounds: int = CALIBRATION_ROUNDS) -> float:
    """A fixed pure-Python workload of the kind a tick does (tuples, dict lookups, float math,
    sorted inserts). Deterministic; it returns a checksum so the work cannot be skipped."""
    total = 0.0
    for r in range(rounds):
        boxes: dict[int, tuple[float, float, float]] = {}
        order: list[tuple[float, int]] = []
        for i in range(200):
            x = (i * 0.37 + r) % 11.0
            boxes[i] = (x, math.sin(x) * 0.25, 0.15)
            insort(order, (x, i))
        for x, i in order:
            bx, bz, half = boxes[i]
            total += math.hypot(bx - x, bz) + half
    return total


def measure(scene: Path) -> dict[str, float]:
    """Mean step and calibration times (ms) over interleaved blocks, and their ratio."""
    from openfactory.core.iotable import Writer
    from openfactory.core.loader import load

    loaded = load(scene)
    for tag in loaded.io.tags():
        if tag.kind == "DO":
            loaded.io.set(tag.name, True, Writer.PLC)
    for _ in range(WARM_TICKS):
        loaded.step(DT)
    step_s, cal_s = [], []
    for _ in range(BLOCKS):
        t0 = time.perf_counter()
        calibrate()
        cal_s.append(time.perf_counter() - t0)
        t0 = time.perf_counter()
        for _ in range(BLOCK_TICKS):
            loaded.step(DT)
        step_s.append((time.perf_counter() - t0) / BLOCK_TICKS)
    step_ms, cal_ms = statistics.fmean(step_s) * 1000, statistics.fmean(cal_s) * 1000
    return {
        "score": round(step_ms / cal_ms, 4),
        "step_ms": round(step_ms, 3),
        "calibration_ms": round(cal_ms, 3),
        "parts": len(loaded.world.parts),
    }


def compare(name: str, got: dict[str, float], base: dict[str, float] | None) -> tuple[bool, str]:
    """(passed, report line) for one scene's measurement against its baseline entry."""
    line = (
        f"{name}: step {got['step_ms']:.3f} ms, calibration {got['calibration_ms']:.3f} ms, "
        f"score {got['score']:.4f}"
    )
    if base is None:
        return False, f"{line}; no baseline (run tools/bench.py --update and commit it)"
    change = got["score"] / base["score"] - 1
    ok = got["score"] <= base["score"] * LIMIT
    verdict = "ok" if ok else f"FAIL: more than {LIMIT - 1:.0%} slower"
    return ok, f"{line}; baseline {base['score']:.4f} ({change:+.1%}), {verdict}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tick-cost gate against a committed baseline.")
    parser.add_argument("--update", action="store_true", help=f"rewrite {BASELINE.name}")
    args = parser.parse_args(argv)
    results = {name: measure(path) for name, path in SCENES.items()}
    if args.update:
        doc = {
            "comment": "tools/bench.py --update; commit with the change that moves it (PLAN 2.9)",
            "measured_on": f"{platform.python_implementation()} {platform.python_version()}, "
            f"{platform.machine()}",
            "warm_ticks": WARM_TICKS,
            "blocks": BLOCKS,
            "block_ticks": BLOCK_TICKS,
            "scenes": results,
        }
        BASELINE.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
        for name, got in results.items():
            print(f"{name}: score {got['score']:.4f} (step {got['step_ms']:.3f} ms)")
        print(f"wrote {BASELINE.relative_to(REPO)}")
        return 0
    if not BASELINE.is_file():
        print(f"no {BASELINE.relative_to(REPO)}; run tools/bench.py --update", file=sys.stderr)
        return 2
    base = json.loads(BASELINE.read_text(encoding="utf-8"))["scenes"]
    passed = True
    for name, got in results.items():
        ok, line = compare(name, got, base.get(name))
        print(line)
        if os.environ.get("GITHUB_ACTIONS") == "true":
            level = "notice" if ok else "error"
            print(f"::{level} title=tick-cost gate {name}::{line} {json.dumps(got)}")
        passed &= ok
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
