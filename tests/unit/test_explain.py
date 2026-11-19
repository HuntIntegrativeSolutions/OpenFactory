"""PLAN 5.14g: why it happened. On a sorter run whose program fires the pusher late, ``explain``
for the failed ``parts_reached`` check names the part, the tick of the late command, and how far
past the pusher the part was, within 2 cm of where the run itself had it. Every line names the
tick and the data it rests on; a live run and a replay explain through the same recording path;
``POST /api/explain`` is read-only and guarded as the read endpoints are (plus the write
middleware a POST gets); exam mode leaves fault events out.

The PLAN's Done-when says 0.5 s late. Measured 2026-11-19: at 0.5 s late the plate still catches
every tall box (all four checks pass), so the failing run is 0.6 s late, the first tenth that
fails; ``test_half_a_second_late_still_passes`` holds that fact."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openfactory.core.geometry import to_local
from openfactory.core.loader import load
from openfactory.explain import Unknown, explain, where_at
from openfactory.observe import carrier_frame
from openfactory.pyplc import ScriptPlc
from openfactory.record import (
    Recorder,
    ReplayRuntime,
    read_recording,
    recording_from_lines,
    recording_header,
)
from openfactory.server.app import create_app
from openfactory.server.runtime import Runtime

REPO = Path(__file__).resolve().parents[2]
SORTER = REPO / "scenes" / "sorting-by-height.yaml"
SCRIPT = REPO / "examples" / "pyplc" / "sorting-by-height.py"
KIT = REPO / "kits" / "sorting-by-height" / "sorting-by-height.ofrec"
LATE_S = 0.6


def late_script(folder: Path, late_s: float) -> Path:
    text = SCRIPT.read_text(encoding="utf-8")
    text, n = re.subn(
        r"^DIVERT_DELAY_S = TRAVEL_S.*$", f"DIVERT_DELAY_S = TRAVEL_S + {late_s}", text, flags=re.M
    )
    assert n == 1
    path = folder / f"late-{late_s}.py"
    path.write_text(text, encoding="utf-8")
    return path


def runtime(script: Path, exam: bool = False) -> Runtime:
    loaded = load(SORTER)
    if exam:
        loaded.enable_exam()
    rt = Runtime(loaded, realtime=False, controller=ScriptPlc(script, loaded.io))
    rt.recording_header = recording_header(rt, SORTER.read_text("utf-8"), None)
    return rt


def record(rt: Runtime, path: Path, ticks: int, truth: dict | None = None) -> None:
    """Run ``ticks`` recorded; ``truth`` gains every part's position and the pusher command at
    every tick, as the run itself had them."""
    rt.recorder = Recorder(path, rt.recording_header, rt.snapshot)
    for _ in range(ticks):
        rt._step(rt.scheduler.dt)
        if truth is not None:
            tick = rt.loaded.tick
            truth[tick] = (
                {p.id: tuple(p.position) for p in rt.loaded.world.parts.values()},
                bool(rt.loaded.io.get("CYL_01_EXT_DO")),
            )
    rt.recorder.close({})


@pytest.fixture(scope="module")
def late(tmp_path_factory):  # noqa: ANN001, ANN201
    tmp = tmp_path_factory.mktemp("explain")
    rt = runtime(late_script(tmp, LATE_S))
    truth: dict = {}
    record(rt, tmp / "late.ofrec", 3000, truth)
    return rt, read_recording(tmp / "late.ofrec"), truth


def along_conv_01(rt: Runtime, point) -> float:  # noqa: ANN001
    doc = {c["id"]: c for c in rt.scene_document(meshes=False, snaps=False)["components"]}
    origin, rot = carrier_frame(doc["CONV_01"], {})
    return to_local(origin, rot, tuple(point))[0]


def test_the_failed_check_names_the_part_the_late_command_and_how_far_past_it_was(late):
    rt, rec, truth = late
    graded = {r.name: r for r in rt.graded}
    failed = graded["parts_reached REM_02: tall"]
    assert not failed.passed and failed.subject and failed.at_tick  # CheckResult's new fields
    answer = explain(rec, check="parts_reached REM_02: tall")
    print(answer.text)
    # the part: the run's own removal log and the recording agree on which part it was
    assert answer.subject == failed.subject == "P000002"
    end = answer.steps[-1]
    assert end.tick < failed.at_tick <= end.until and "left the scene at REM_01" in end.text
    # the late command: the first CYL_01_EXT_DO rise after the part tripped PE_01
    trip = next(s for s in answer.steps if s.text.startswith("PE_01_DI went true"))
    command = next(
        s
        for s in answer.steps
        if s.text.startswith("CYL_01_EXT_DO went true") and s.tick > trip.tick
    )
    rises = [k for k in sorted(truth) if truth[k][1] and not truth.get(k - 1, ({}, False))[1]]
    assert command.tick == next(k for k in rises if k > trip.tick)
    assert f"(tick {command.tick})" in answer.text and "CYL_01_EXT_DO" in command.basis
    # 0.6 s late: 30 ticks after the on-time program's command for the same box (the kit's run)
    kit = read_recording(KIT)
    on_time = next(k for k, c in kit.io if c.get("CYL_01_EXT_DO") is True)
    assert command.tick - on_time == round(LATE_S / rec.dt)
    # how far past the pusher, against where the run had the part at that very tick
    said = float(re.search(r"its centre ([0-9.]+) m past CYL_01's centre", command.text)[1])
    real = along_conv_01(rt, truth[command.tick][0]["P000002"]) - along_conv_01(
        rt, rt.loaded.component("CYL_01").config.position
    )
    print(f"past the pusher: explain {said} m, the run {real:.4f} m")
    assert abs(said - real) <= 0.02
    # every line of the story names its tick and what it rests on
    for line in answer.text.split("\n"):
        if line.startswith("- ") and "listed by nearness" not in line:
            assert re.search(r"\(ticks? \d+", line) and "*[" in line, line


def test_positions_between_frames_are_within_2_cm_of_the_run(late):
    """The command tick above may fall between two frames; every tick of the part's ride on
    CONV_01, off the 5-tick grid included, is placed within 2 cm of where the run had it."""
    _, rec, truth = late
    from openfactory.explain import _Data

    d = _Data.of(rec)
    track = d.tracks["P000002"]
    worst = 0.0
    for tick in range(track[0][0], track[-1][0] + 1):
        found = where_at(d, track, tick)
        real = truth[tick][0]["P000002"]
        worst = max(worst, max(abs(a - b) for a, b in zip(found[0], real, strict=True)))
    print(f"worst position error over ticks {track[0][0]} to {track[-1][0]}: {worst:.5f} m")
    assert worst <= 0.02


def test_half_a_second_late_still_passes(tmp_path):
    rt = runtime(late_script(tmp_path, 0.5))
    record(rt, tmp_path / "half.ofrec", 3000)
    assert all(r.passed for r in rt.graded), [r for r in rt.graded if not r.passed]
    text = explain(read_recording(tmp_path / "half.ofrec")).text
    assert "No check failed in this recording." in text


def test_a_part_a_check_by_its_start_and_what_is_not_there(late):
    _, rec, _ = late
    story = explain(rec, part="P000001")
    assert story.subject == "P000001" and story.trail[0][0] == 55
    assert "REM_01's count of short rose from 0 to 1" in story.text
    assert explain(rec, check="parts_reached").subject == "P000002"  # the failed one first
    assert "passed" in explain(rec, check="none_lost").text
    with pytest.raises(Unknown):
        explain(rec, part="P999999")
    with pytest.raises(Unknown, match="its checks: parts_reached REM_01"):
        explain(rec, check="no_such_check")


def test_live_and_replay_explain_through_the_same_recording_path(late, tmp_path):
    """The live run's last 60 s, read as a capture's piece, tell the same story of P000002 as
    the whole recording, from where the piece starts; a replay explains up to the frame shown."""
    rt, rec, _ = late
    rt.recorder = None
    _, lines = rt.piece()
    piece = recording_from_lines(lines)
    live = rt.explain(part="P000017")
    whole = explain(rec, part="P000017").document()
    assert live["steps"][1:] == whole["steps"][1:]  # step 0: made before the piece? same tick
    assert explain(piece, part="P000017").document() == live
    replay = ReplayRuntime(rec, paused=True)
    replay.seek(600)
    shown = replay.explain(check="parts_reached REM_02: tall")
    assert shown["subject"] == "P000002" and shown["to_tick"] == 600
    with pytest.raises(Unknown):
        replay.explain(part="P000019")  # made after tick 600


def test_post_api_explain_is_read_only_and_guarded_as_a_read_is(late, tmp_path):
    rt, _, _ = late
    http = TestClient(create_app(rt, web_dist=None), base_url="http://127.0.0.1:8000")
    tick = rt.loaded.tick
    res = http.post("/api/explain", json={"part": "P000017"})
    assert res.status_code == 200 and res.json()["subject"] == "P000017"
    assert res.json()["trail"][0][0] >= res.json()["from_tick"]
    assert http.post("/api/explain", json={}).json()["subject"] == "P000002"
    assert http.post("/api/explain", json={"part": "NOPE"}).status_code == 404
    assert http.post("/api/explain", json={"check": "x", "part": "y"}).status_code == 422
    assert http.post("/api/explain", json={"part": "P1", "path": "/etc"}).status_code == 422
    other = http.post(
        "/api/explain", json={"part": "P000017"}, headers={"Origin": "http://evil.example"}
    )
    assert other.status_code == 403
    form = http.post(
        "/api/explain", content=b"part=P000017", headers={"Content-Type": "text/plain"}
    )
    assert form.status_code == 415
    rebound = TestClient(create_app(rt, web_dist=None), base_url="http://evil.example:8000")
    assert rebound.post("/api/explain", json={}).status_code == 421  # DNS rebinding
    assert rt.loaded.tick == tick  # read-only: the run did not move


def test_exam_mode_leaves_fault_events_out(tmp_path):
    for exam in (False, True):
        rt = runtime(SCRIPT, exam=exam)
        rt.scheduled_faults.append((5.0, "CONV_01", "stuck_off", {}))
        path = tmp_path / f"exam-{exam}.ofrec"
        record(rt, path, 400)
        text = explain(read_recording(path), part="P000002").text
        if exam:
            assert "event fault" not in text and "Faults are hidden in exam mode" in text
        else:
            assert "event fault on CONV_01 (stuck_off)" in text
