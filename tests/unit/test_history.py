"""PLAN 3.6: GET /api/history?tag= serves an AI tag's last ten minutes from a ring buffer sampled
at 2 Hz of simulated time, for the viewer's trend panel."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openfactory.core.history import MAXLEN, PERIOD_S, WINDOW_S, TagHistory
from openfactory.core.loader import load
from openfactory.pyplc import ScriptPlc
from openfactory.server.app import create_app
from openfactory.server.runtime import Runtime

REPO = Path(__file__).resolve().parents[2]
PID = REPO / "scenes" / "tank-level-pid.yaml"
SCRIPT = REPO / "examples" / "pyplc" / "tank-level-pid.py"


def pid_runtime() -> Runtime:
    loaded = load(PID)
    return Runtime(loaded, realtime=False, controller=ScriptPlc(SCRIPT, loaded.io))


def test_samples_every_half_second_of_simulated_time_whatever_the_tick():
    loaded = load(PID)
    history = TagHistory(loaded.io)
    assert history.tags == ["FV_01_ZT_AI", "TK_01_LT_AI"]  # the scene's AI tags, no AO or DI
    for _ in range(250):  # 5 s at 50 Hz
        loaded.step(0.02)
        history.observe(loaded.time)
    series = history.series("TK_01_LT_AI")
    assert len(series["t"]) == 11  # the first tick, 0.02, then the 2 Hz grid: 0.5 ... 5.0
    assert series["t"][1:] == pytest.approx([0.5 * k for k in range(1, 11)])
    slow = TagHistory(load(PID).io)
    for k in range(1, 51):  # 0.1 s ticks: the first, then the same 2 Hz of simulated time
        slow.observe(k * 0.1)
    assert slow.series("TK_01_LT_AI")["t"] == pytest.approx([0.1] + [0.5 * k for k in range(1, 11)])


def test_the_ring_keeps_ten_minutes_and_drops_the_oldest():
    assert (PERIOD_S, WINDOW_S, MAXLEN) == (0.5, 600.0, 1201)
    loaded = load(PID)
    history = TagHistory(loaded.io)
    for k in range(1, 1400):  # 700 s at 2 Hz, without stepping: only the clock matters here
        history.observe(k * 0.5)
    t = history.series("TK_01_LT_AI")["t"]
    assert len(t) == MAXLEN and t[0] == pytest.approx(99.5) and t[-1] == pytest.approx(699.5)
    last = history.series("TK_01_LT_AI", seconds=60)["t"]
    assert last[0] == pytest.approx(639.5) and len(last) == 121


def test_the_endpoint_serves_the_level_the_plc_reads(tmp_path):
    runtime = pid_runtime()
    for _ in range(1500):  # 30 s: the load step at 20 s included
        runtime._step(0.02)
    http = TestClient(create_app(runtime, web_dist=None), base_url="http://127.0.0.1")
    body = http.get("/api/history", params={"tag": "TK_01_LT_AI"}).json()
    assert (body["tag"], body["units"], body["range"]) == ("TK_01_LT_AI", "m", [0.0, 2.0])
    assert (body["period_s"], body["window_s"]) == (0.5, 600.0)
    assert len(body["t"]) == len(body["v"]) == 61  # 0.02, then 0.5 ... 30.0
    assert body["v"][-1] == pytest.approx(runtime.loaded.io.get("TK_01_LT_AI"), abs=0.01)
    assert min(body["v"][40:]) < 0.99 < max(body["v"][:40])  # the dip after the load step
    recent = http.get("/api/history", params={"tag": "TK_01_LT_AI", "seconds": 5}).json()
    assert len(recent["t"]) == 11 and recent["t"][-1] == body["t"][-1]


@pytest.mark.parametrize(
    ("params", "status", "detail"),
    [
        ({"tag": "FV_01_POS_AO"}, 404, "'FV_01_POS_AO' is an AO tag; only AI tags have a history"),
        ({"tag": "TK_01_LSH_DI"}, 404, "'TK_01_LSH_DI' is a DI tag; only AI tags have a history"),
        ({"tag": "NOPE_AI"}, 404, "no tag 'NOPE_AI' in this scene"),
        ({"tag": "TK_01_LT_AI", "seconds": 0}, 400, "seconds must be positive"),
        ({}, 422, None),
    ],
)
def test_bad_requests_say_why(params, status, detail):
    http = TestClient(create_app(pid_runtime(), web_dist=None), base_url="http://127.0.0.1")
    res = http.get("/api/history", params=params)
    assert res.status_code == status
    if detail is not None:
        assert res.json()["detail"] == detail
