"""PLAN 5.14h: pause and single-step a live run. A paused scripted run's tick holds for two wall
seconds, five steps advance it five frames' worth (5 x 0.1 s = 25 ticks), and the stand-in
script pauses with it. Resuming does not catch up the paused time. Simulated time stands, so a
``checks:`` deadline is never failed by waiting. ``POST /api/pause`` and ``/api/step`` are write
endpoints guarded as ``/api/fault`` is and off for a trainee in exam mode; frames carry
``sim_paused`` while paused, the snapshot never does."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openfactory.core.loader import load
from openfactory.pyplc import ScriptPlc
from openfactory.record import ReplayRuntime, read_recording
from openfactory.server.app import FAULT_TOKEN_HEADER, create_app
from openfactory.server.runtime import Runtime

REPO = Path(__file__).resolve().parents[2]
SORTER = REPO / "scenes" / "sorting-by-height.yaml"
SCRIPT = REPO / "examples" / "pyplc" / "sorting-by-height.py"
KIT = REPO / "kits" / "sorting-by-height" / "sorting-by-height.ofrec"


def scripted(exam: bool = False, duration_s: float | None = None) -> Runtime:
    loaded = load(SORTER)
    if exam:
        loaded.enable_exam()
    if duration_s is not None:
        loaded.scene.checks = loaded.scene.checks.model_copy(update={"duration_s": duration_s})
    return Runtime(loaded, controller=ScriptPlc(SCRIPT, loaded.io))  # real time


def http(rt: Runtime, **kw) -> TestClient:  # noqa: ANN003
    return TestClient(create_app(rt, web_dist=None, **kw), base_url="http://127.0.0.1:8000")


@pytest.fixture
def running():  # noqa: ANN201
    rt = scripted()
    rt.start()
    yield rt
    rt.stop()


def wait_tick(rt: Runtime, tick: int, timeout: float = 5.0) -> None:
    end = time.monotonic() + timeout
    while rt.loaded.tick < tick and time.monotonic() < end:
        time.sleep(0.01)
    assert rt.loaded.tick >= tick


def test_a_paused_run_holds_for_two_seconds_and_five_steps_advance_five_frames(running):
    rt, client = running, http(running)
    wait_tick(rt, 20)
    answer = client.post("/api/pause", json={"paused": True}).json()
    assert answer["paused"] is True
    held, scans = rt.loaded.tick, rt.controller.scans
    assert answer["tick"] == held
    time.sleep(2.0)
    assert rt.loaded.tick == held and rt.controller.scans == scans  # the script paused too
    assert client.get("/api/state").json()["tick"] == held
    ticks = [client.post("/api/step", json={}).json()["tick"] for _ in range(5)]
    assert ticks == [held + 5 * (i + 1) for i in range(5)]  # 0.1 s each, answered once run
    assert rt.loaded.tick == held + 25 and rt.controller.scans == scans + 25
    time.sleep(0.3)
    assert rt.loaded.tick == held + 25  # still paused after the steps
    events = [e for e in rt.events if e["type"] in ("pause", "step")]
    assert [e["type"] for e in events] == ["pause"] + ["step"] * 5
    assert client.post("/api/step", json={"steps": 2}).json()["tick"] == held + 35


def test_resuming_starts_from_now_without_catching_up(running):
    rt, client = running, http(running)
    wait_tick(rt, 10)
    client.post("/api/pause", json={"paused": True})
    time.sleep(1.0)
    at = rt.loaded.tick
    started = time.monotonic()
    assert client.post("/api/pause", json={"paused": False}).json()["paused"] is False
    time.sleep(0.5)
    ran = rt.loaded.tick - at
    expected = (time.monotonic() - started) / rt.scheduler.dt
    print(f"after resuming: {ran} ticks in {expected:.0f} ticks' worth of wall time")
    assert abs(ran - expected) <= 10  # not the paused second's 50 ticks in a burst
    assert rt.scheduler.stats.dropped == 0


def test_a_paused_run_never_fails_a_checks_deadline_by_waiting():
    """The checks are graded when simulated time reaches ``duration_s``; a pause that lasts
    longer than that in wall time grades nothing, and the run grades at the right tick after."""
    rt = scripted(duration_s=1.0)
    rt.start()
    try:
        wait_tick(rt, 10)
        rt.pause(True)
        held = rt.loaded.tick
        time.sleep(2.0)  # twice the deadline in wall time
        status = rt.check_status()
        assert (
            rt.graded is None
            and status["graded"] is False
            and status["t"] == pytest.approx(held * 0.02)
        )
        assert all(c["state"] == "pending" for c in status["checks"])
        rt.pause(False)
        end = time.monotonic() + 5
        while rt.graded is None and time.monotonic() < end:
            time.sleep(0.01)
        assert rt.graded is not None and rt.check_status()["graded"] is True
    finally:
        rt.stop()


def test_pause_and_step_are_write_endpoints_guarded_as_api_fault(tmp_path):
    rt = scripted()
    client = http(rt)
    assert (
        client.post(
            "/api/pause", json={"paused": True}, headers={"Origin": "http://x.example"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/pause", content=b"paused=1", headers={"Content-Type": "text/plain"}
        ).status_code
        == 415
    )
    assert client.post("/api/pause", json={"paused": True, "path": "x"}).status_code == 422
    assert client.post("/api/step", json={"steps": 0}).status_code == 422
    assert client.post("/api/step", json={"steps": 101}).status_code == 422
    # beyond loopback: the fault token, as /api/fault
    wide = http(rt, bind="0.0.0.0", fault_token="s3cret")
    assert wide.post("/api/pause", json={"paused": True}).status_code == 401
    assert (
        wide.post(
            "/api/pause", json={"paused": True}, headers={FAULT_TOKEN_HEADER: "s3cret"}
        ).status_code
        == 200
    )
    assert http(rt, bind="0.0.0.0").post("/api/pause", json={"paused": False}).status_code == 403
    # a replay pauses with its own controls
    replay = ReplayRuntime(read_recording(KIT), paused=True)
    assert http(replay).post("/api/pause", json={"paused": True}).status_code == 404
    assert http(replay).get("/api/pause").status_code == 404


def test_off_for_a_trainee_in_exam_mode():
    rt = scripted(exam=True)
    trainee = http(rt, fault_token="teach")
    assert trainee.post("/api/pause", json={"paused": True}).status_code == 403
    assert trainee.post("/api/step", json={}).status_code == 403
    assert not rt.paused
    assert (
        trainee.post(
            "/api/pause", json={"paused": True}, headers={FAULT_TOKEN_HEADER: "teach"}
        ).json()["paused"]
        is True
    )
    assert http(rt).post("/api/pause", json={"paused": True}).status_code == 403  # no token set


def test_frames_carry_sim_paused_and_the_snapshot_never_does(running):
    rt, client = running, http(running)
    wait_tick(rt, 5)

    def frame() -> dict:
        with client.websocket_connect("ws://127.0.0.1/ws/state") as ws:
            return json.loads(ws.receive_text())

    assert "sim_paused" not in frame()
    client.post("/api/pause", json={"paused": True})
    assert frame()["sim_paused"] is True
    assert "sim_paused" not in client.get("/api/state").json()
    assert client.get("/api/pause").json() == {
        "paused": True, "tick": rt.loaded.tick, "t": round(rt.loaded.time, 6),
        "plc_attached": False, "protocols": [],
    }  # fmt: skip
    assert client.get("/api/status").json()["paused"] is True
