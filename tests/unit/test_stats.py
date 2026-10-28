"""PLAN 2.11 statistics: throughput, utilization, actuator cycles, dwell, and losses, from one run,
served at /api/stats and printed before the run summary."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openfactory.core.loader import load
from openfactory.core.stats import SceneStats, format_report
from openfactory.pyplc import ScriptPlc
from openfactory.server.app import create_app
from openfactory.server.runtime import Runtime

REPO = Path(__file__).resolve().parents[2]


def scripted(name: str, seconds: float = 60.0) -> SceneStats:
    loaded = load(REPO / "scenes" / f"{name}.yaml")
    plc = ScriptPlc(REPO / "examples" / "pyplc" / f"{name}.py", loaded.io)
    stats, t = SceneStats(loaded), 0.0
    for _ in range(round(seconds / 0.02)):
        plc.scan(t)
        loaded.step(0.02)
        stats.observe()
        t += 0.02
    return stats


def test_sorter_statistics():
    report = scripted("sorting-by-height").report()
    print(format_report(report))
    assert report["throughput"]["REM_02"] == {"parts": {"tall": 9}, "per_hour": {"tall": 540.0}}
    assert report["utilization_pct"] == {"CONV_01": 100.0, "CONV_02": 100.0}
    cyl = report["cycles"]["CYL_01"]
    assert cyl["count"] == 9 and cyl["mean_s"] == pytest.approx(0.8, abs=0.03)  # 0.4 out, 0.4 back
    dwell = report["dwell_s"]
    assert dwell["removed"] == 18 and dwell["max"] == pytest.approx(7.7)
    assert report["lost"] == {}


def test_a_stopper_cycle_is_a_release_and_its_first_rise_is_not_counted():
    report = scripted("two-lane-merge").report()
    print(format_report(report))
    for stopper in ("STP_A", "STP_B"):  # spring-raised: up from the start, one cycle per box
        assert report["cycles"][stopper]["count"] == 7
    assert report["cycles"]["TT_01"]["count"] == 7  # one turn out and back per lane-B box
    assert 0 < report["utilization_pct"]["TT_01"] < 100
    buffer = scripted("buffer-and-release").report()  # starts lowered: the first rise is free
    assert buffer["cycles"]["STP_01"]["count"] == 7  # seven batches completed in 60 s


def test_an_idle_run_reports_zeros_and_api_stats_serves_the_report():
    loaded = load(REPO / "scenes" / "conveyor-start-stop.yaml")
    runtime = Runtime(loaded, realtime=False)
    for _ in range(50):
        runtime._step(0.02)  # the scheduler's tick, without a thread
    report = TestClient(create_app(runtime, web_dist=None)).get("/api/stats").json()
    print(report)
    assert report["sim_time_s"] == 1.0
    assert report["utilization_pct"] == {"CONV_01": 0.0}  # nothing commands the belt
    assert report["throughput"] == {"REM_01": {"parts": {}, "per_hour": {}}}
    assert report["dwell_s"] == {"removed": 0, "mean": None, "max": None, "oldest_in_scene": 0.0}
    assert "dwell: no part removed" in format_report(report)
