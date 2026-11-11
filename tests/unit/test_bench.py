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


def got(score: float, old: float = 0.025) -> dict[str, float]:
    """A measurement whose gated score (the candidate's, ruled 2026-11-10) is ``score``."""
    return {
        "score": old, "step_ms": 2.5, "calibration_ms": 100.0, "parts": 200,
        "candidate_ms": 36.0, "candidate_score": score,
    }  # fmt: skip


def test_a_score_up_to_25_percent_over_the_baseline_passes_and_above_fails():
    base = {"score": 0.0200, "candidate_score": 0.0200}
    assert bench.compare("s", got(0.0200 * 1.25), base)[0]
    assert bench.compare("s", got(0.0150), base)[0]  # faster is fine
    ok, line = bench.compare("s", got(0.0200 * 1.26), base)
    assert not ok and "+26.0%" in line and "FAIL: more than 25% slower" in line


def test_the_gate_is_one_sided_much_faster_passes_with_a_warning_naming_entry_and_cpu(
    monkeypatch, capsys
):
    """Ruled 2026-11-10: more than 25 % slower fails; more than 25 % faster passes and is a
    warning annotation naming the entry and the runner's CPU; within 25 % either way is plain."""
    base = {"score": 0.02, "candidate_score": 0.0800}
    fast = {**got(0.0800 / 1.26), "cpu": "AMD EPYC 9V74 80-Core Processor"}
    ok, line = bench.compare("tank-level-pid", fast, base)
    assert ok and "WARNING: more than 25% faster" in line and "AMD EPYC 9V74" in line
    ok, line = bench.compare("s", got(0.0800 / 1.25), base)  # exactly 25 % faster: plain ok
    assert ok and "WARNING" not in line
    assert not bench.compare("s", got(0.0800 * 1.26), base)[0]  # slower still fails
    baseline = json.loads(bench.BASELINE.read_text(encoding="utf-8"))["scenes"]
    slow = min(e["candidate_score"] for e in baseline.values()) / 1.3  # faster than every entry
    monkeypatch.setattr(bench, "measure", lambda *paths: {**got(slow), "cpu": "Test CPU 9000"})
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert bench.main([]) == 0  # all much faster: passes
    notes = [x for x in capsys.readouterr().out.splitlines() if x.startswith("::")]
    assert len(notes) == len(bench.SCENES) + len(bench.PROCESS)
    load = next(n for n in notes if "tick-cost gate load-200::" in n)
    assert load.startswith("::warning title=tick-cost gate load-200::") and "Test CPU 9000" in load


def test_a_scene_without_a_baseline_fails_and_says_how_to_make_one():
    ok, line = bench.compare("s", got(0.02), None)
    assert not ok and "tools/bench.py --update" in line
    ok, line = bench.compare("s", got(0.02), {"score": 0.02})  # an entry from before the switch
    assert not ok and "tools/bench.py --update" in line


def test_the_gate_is_the_candidate_score_and_the_old_score_is_only_reported():
    """Ruled 2026-11-10: the switch. The old calibration's score no longer fails or passes."""
    assert bench.GATE == "candidate_score"
    base = {"score": 0.0200, "candidate_score": 0.0700}
    ok, line = bench.compare("s", got(0.0700, old=0.0400), base)  # old score +100 %
    assert ok and "not gated" in line
    assert not bench.compare("s", got(0.0700 * 1.3, old=0.0200), base)[0]


def test_the_committed_baseline_covers_every_scene_at_the_200_part_cap_and_the_process_scene():
    doc = json.loads(bench.BASELINE.read_text(encoding="utf-8"))
    assert set(doc["scenes"]) == set(bench.SCENES) | set(bench.PROCESS)
    assert all(path.is_file() for pair in bench.PROCESS.values() for path in pair)
    assert all(path.is_file() for path in bench.SCENES.values())
    assert all(doc["scenes"][name]["parts"] >= 190 for name in bench.SCENES)
    assert all(s["score"] > 0 for s in doc["scenes"].values())
    assert all(s["candidate_score"] > 0 and s["cpu"] for s in doc["scenes"].values())
    assert (doc["warm_ticks"], doc["blocks"], doc["block_ticks"]) == (
        bench.WARM_TICKS, bench.BLOCKS, bench.BLOCK_TICKS,
    )  # fmt: skip


def test_the_calibration_is_deterministic():
    assert bench.calibrate(3) == bench.calibrate(3)


def test_on_github_actions_each_result_is_also_an_annotation(monkeypatch, capsys):
    """The score a CI runner measured is readable from the public check-runs API (PLAN 2.9)."""
    base = json.loads(bench.BASELINE.read_text(encoding="utf-8"))["scenes"]
    gate = base["load-200"]["candidate_score"] * 1.3
    monkeypatch.setattr(bench, "measure", lambda *paths: got(gate))
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert bench.main([]) == 1
    lines = capsys.readouterr().out.splitlines()
    notes = [line for line in lines if line.startswith("::")]
    assert len(notes) == len(bench.SCENES) + len(bench.PROCESS)
    load = next(n for n in notes if "tick-cost gate load-200::" in n)
    assert load.startswith("::error title=tick-cost gate load-200::load-200: step 2.500 ms")
    assert json.loads(load[load.index("{") :]) == got(gate)
    monkeypatch.delenv("GITHUB_ACTIONS")
    bench.main([])
    assert not [line for line in capsys.readouterr().out.splitlines() if line.startswith("::")]


def test_the_candidate_calibration_is_deterministic_and_the_machine_is_named():
    """PLAN 2.9, bench drift: the candidate is timed beside the gate, with the runner's CPU."""
    assert bench.calibrate_tick(3) == bench.calibrate_tick(3) != 0
    named = bench.machine()
    assert named["cpu"] and named["python"].startswith(("CPython", "PyPy"))


def test_the_process_bench_runs_the_pid_scene_under_its_script_inside_the_solver_caps():
    """Ruled 2026-11-08: the gate covers a real process scene, tank-level-pid under its stand-in
    script for the fixed tick count, its loop live (the valve moving) and the solver converging."""
    from openfactory.core.loader import load
    from openfactory.pyplc import ScriptPlc

    scene, script = bench.PROCESS["tank-level-pid"]
    loaded = load(scene)
    plc = ScriptPlc(script, loaded.io)
    seen = set()
    for _ in range(bench.WARM_TICKS + bench.BLOCKS * bench.BLOCK_TICKS):
        plc.scan(loaded.time)
        loaded.step(bench.DT)
        seen.add(round(loaded.io.get("FV_01_POS_AO")))
    stats = loaded.flownet.stats
    assert stats.cap_hits == 0 and stats.split_cap_hits == 0 and stats.max_iterations <= 20
    assert len(seen) > 10  # the PI moves the valve: a live loop, not a forced one
    assert 0.95 < loaded.component("TK_01").node.level < 1.05


def test_update_only_rewrites_one_entry_and_keeps_the_others(monkeypatch, tmp_path):
    baseline = tmp_path / "bench_baseline.json"
    baseline.write_text(bench.BASELINE.read_text(encoding="utf-8"), encoding="utf-8")
    before = json.loads(baseline.read_text(encoding="utf-8"))
    monkeypatch.setattr(bench, "BASELINE", baseline)
    monkeypatch.setattr(bench, "REPO", tmp_path)
    monkeypatch.setattr(bench, "measure", lambda *paths: got(0.5))
    assert bench.main(["--update", "--only", "tank-level-pid"]) == 0
    after = json.loads(baseline.read_text(encoding="utf-8"))
    assert after["scenes"]["tank-level-pid"]["candidate_score"] == 0.5
    assert after["scenes"]["load-200"] == before["scenes"]["load-200"]
    assert list(after["scenes"]) == [*bench.SCENES, *bench.PROCESS]
