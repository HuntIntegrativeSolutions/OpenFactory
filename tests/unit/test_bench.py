"""PLAN 2.9: the tick-cost gate's comparison and its committed baseline (``tools/bench.py``).
The measurement itself runs as its own CI step, not here."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("bench", REPO / "tools" / "bench.py")
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


def got(score: float) -> dict[str, float]:
    return {"score": score, "step_ms": 2.5, "calibration_ms": 100.0, "parts": 200}


def test_a_score_up_to_25_percent_over_the_baseline_passes_and_above_fails():
    base = {"score": 0.0200}
    assert bench.compare("s", got(0.0200 * 1.25), base)[0]
    assert bench.compare("s", got(0.0150), base)[0]  # faster is fine
    ok, line = bench.compare("s", got(0.0200 * 1.26), base)
    assert not ok and "+26.0%" in line and "FAIL: more than 25% slower" in line


def test_a_scene_without_a_baseline_fails_and_says_how_to_make_one():
    ok, line = bench.compare("s", got(0.02), None)
    assert not ok and "tools/bench.py --update" in line


def test_the_committed_baseline_covers_every_scene_at_the_200_part_cap():
    doc = json.loads(bench.BASELINE.read_text(encoding="utf-8"))
    assert set(doc["scenes"]) == set(bench.SCENES)
    assert all(path.is_file() for path in bench.SCENES.values())
    assert all(s["parts"] >= 190 and s["score"] > 0 for s in doc["scenes"].values())
    assert (doc["warm_ticks"], doc["blocks"], doc["block_ticks"]) == (
        bench.WARM_TICKS, bench.BLOCKS, bench.BLOCK_TICKS,
    )  # fmt: skip


def test_the_calibration_is_deterministic():
    assert bench.calibrate(3) == bench.calibrate(3)


def test_on_github_actions_each_result_is_also_an_annotation(monkeypatch, capsys):
    """The score a CI runner measured is readable from the public check-runs API (PLAN 2.9)."""
    base = json.loads(bench.BASELINE.read_text(encoding="utf-8"))["scenes"]
    monkeypatch.setattr(bench, "measure", lambda path: got(base["load-200"]["score"] * 1.3))
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert bench.main([]) == 1
    lines = capsys.readouterr().out.splitlines()
    notes = [line for line in lines if line.startswith("::")]
    assert len(notes) == len(bench.SCENES)
    load = next(n for n in notes if "tick-cost gate load-200::" in n)
    assert load.startswith("::error title=tick-cost gate load-200::load-200: step 2.500 ms")
    assert json.loads(load[load.index("{") :]) == got(base["load-200"]["score"] * 1.3)
    monkeypatch.delenv("GITHUB_ACTIONS")
    bench.main([])
    assert not [line for line in capsys.readouterr().out.splitlines() if line.startswith("::")]


def test_the_candidate_calibration_is_deterministic_and_the_machine_is_named():
    """PLAN 2.9, bench drift: the candidate is timed beside the gate, with the runner's CPU."""
    assert bench.calibrate_tick(3) == bench.calibrate_tick(3) != 0
    named = bench.machine()
    assert named["cpu"] and named["python"].startswith(("CPython", "PyPy"))
