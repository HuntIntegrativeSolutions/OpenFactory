"""PLAN 5.14f: pause, seek and step in a replay, and the strip's times and table (no browser).
Seeking to tick 300 of the sorter's kit recording shows the frame the recording holds at 300."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openfactory.core.loader import load
from openfactory.record import ReplayRuntime, read_recording
from openfactory.server.app import create_app
from openfactory.server.runtime import Runtime
from openfactory.strip import change_table, recording_of, strip_ticks

REPO = Path(__file__).resolve().parents[2]
SORTER = REPO / "scenes" / "sorting-by-height.yaml"
KIT = REPO / "kits" / "sorting-by-height" / "sorting-by-height.ofrec"


@pytest.fixture(scope="module")
def rec():  # noqa: ANN201
    return read_recording(KIT)


def test_seek_to_tick_300_shows_the_recorded_frame(rec):
    frames = dict(rec.frames)
    runtime = ReplayRuntime(rec, paused=True)
    http = TestClient(create_app(runtime, web_dist=None), base_url="http://127.0.0.1")
    answer = http.post("/api/replay/seek", json={"tick": 300}).json()
    assert answer["tick"] == 300 and answer["paused"] and answer["t"] == 6.0
    assert http.get("/api/state").json() == frames[300]  # state compared, whole
    assert runtime.snapshot is frames[300]
    # between frames: the one at or before it; past either end: the end
    assert runtime.seek(302)["tick"] == 300 and runtime.snapshot is frames[300]
    assert runtime.seek(-50)["tick"] == 0
    assert runtime.seek(10**6)["tick"] == rec.frames[-1][0]
    assert http.get("/api/look").text.startswith(
        f"# sorting-by-height at t = {rec.frames[-1][0] * rec.dt:.2f} s"
    )


def test_step_pauses_and_moves_by_whole_frames(rec):
    runtime = ReplayRuntime(rec, paused=False)
    http = TestClient(create_app(runtime, web_dist=None), base_url="http://127.0.0.1")
    runtime.seek(300)
    s = http.post("/api/replay/step", json={"frames": 1}).json()
    assert s["tick"] == 305 and s["paused"]
    assert http.post("/api/replay/step", json={"frames": -3}).json()["tick"] == 290
    runtime.seek(0)
    assert runtime.step(-1)["tick"] == 0  # stops at the start
    assert http.get("/api/replay").json()["index"] == 0
    assert http.post("/api/replay/step", json={"frames": 0.5}).status_code == 422
    assert http.post("/api/replay/seek", json={"tick": 1, "path": "x"}).status_code == 422


def test_the_event_log_follows_a_seek_back(tmp_path):
    out = tmp_path / "fault.ofrec"
    subprocess.run(
        [sys.executable, "-m", "openfactory", "run", str(SORTER),
         "--as-fast-as-possible", "--duration", "6", "--fault", "CYL_01=jammed", "--fault-at", "3",
         "--record", str(out), "-q"],
        cwd=tmp_path, check=True, capture_output=True,
    )  # fmt: skip
    rec = read_recording(out)
    (fault_tick, _), *_ = rec.events
    runtime = ReplayRuntime(rec, paused=True)
    runtime.seek(rec.frames[-1][0])
    assert [e["type"] for e in runtime.events] == ["fault"]
    assert runtime.status()["events"] == {"fault:CYL_01": 1}
    runtime.seek(fault_tick - 10)  # before the fault: the log as it was then
    assert list(runtime.events) == [] and runtime.status()["events"] == {}
    runtime.step(5)
    assert len(runtime.events) == (1 if runtime.snapshot["tick"] >= fault_tick else 0)


def test_pause_and_play_from_a_seek_and_after_the_end(rec):
    runtime = ReplayRuntime(rec, speed=100.0, paused=True)
    runtime.start()
    try:
        time.sleep(0.2)
        assert runtime.snapshot["tick"] == 0  # paused: nothing moves
        runtime.seek(2900)
        runtime.pause(False)
        deadline = time.monotonic() + 10
        while not runtime.finished and time.monotonic() < deadline:
            time.sleep(0.02)
        assert runtime.finished and runtime.snapshot["tick"] == rec.frames[-1][0]
        assert runtime.replay_status()["finished"]
        # play after the end starts again from the first frame
        runtime.pause(True)
        assert runtime.pause(False)["tick"] == 0
        time.sleep(0.2)
        assert runtime.snapshot["tick"] > 0
    finally:
        runtime.stop()


def test_a_live_run_has_no_replay_controls():
    runtime = Runtime(load(REPO / "scenes" / "sorting-by-height.yaml"), realtime=False)
    http = TestClient(create_app(runtime, web_dist=None), base_url="http://127.0.0.1")
    assert http.get("/api/replay").status_code == 404
    res = http.post("/api/replay/seek", json={"tick": 1})
    assert res.status_code == 404 and "replay only" in res.text


def test_replay_controls_are_refused_from_another_origin(rec):
    http = TestClient(create_app(ReplayRuntime(rec), web_dist=None), base_url="http://127.0.0.1")
    res = http.post(
        "/api/replay/pause", json={"paused": True}, headers={"Origin": "http://x.example"}
    )
    assert res.status_code == 403


def test_strip_times_are_frames_centred_on_t_and_kept_inside_the_recording(rec):
    assert strip_ticks(rec, 8.6, 2.0, 6) == [380, 400, 420, 440, 460, 480]
    assert strip_ticks(rec, 0.2, 2.0, 3) == [0, 50, 100]  # moved inside at the start
    last = rec.frames[-1][0]
    assert strip_ticks(rec, None, 2.0, 3) == [last - 100, last - 50, last]  # the end
    with pytest.raises(ValueError):
        strip_ticks(rec, 1.0, 2.0, 1)


def test_the_table_lists_the_push_between_the_right_two_stills(rec):
    ticks = strip_ticks(rec, 8.6, 2.0, 6)
    table = change_table(rec, ticks)
    sections = table.split("\n## ")[1:]
    with_push = [s.split("\n")[0] for s in sections if "`CYL_01_EXT_DO` false → true" in s]
    assert with_push == ["Still 3 to 4 (t = 8.40 to 8.80 s)"]  # the command went true at 8.60 s
    assert "P000002 (tall) CONV_01 → CONV_02" in table  # the pushed box changed belts
    assert "P000001 (short) removed, was on CONV_01" in table


def test_a_capture_folder_is_a_strips_input(tmp_path):
    with pytest.raises(ValueError, match="not a capture"):
        recording_of(tmp_path)
    (tmp_path / "last-60s.ofrec").write_bytes(KIT.read_bytes())
    assert recording_of(tmp_path) == tmp_path / "last-60s.ofrec"
    assert recording_of(KIT) == KIT
