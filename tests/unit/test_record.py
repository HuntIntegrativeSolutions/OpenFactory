"""PLAN 2.12 record and replay: the recording is deterministic, self-contained and free of
machine details, survives being cut short, and replays through the web app with no simulation."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from openfactory.cli import app
from openfactory.core.loader import load
from openfactory.pyplc import ScriptPlc
from openfactory.record import FORMAT, ReplayRuntime, io_table, read_recording
from openfactory.server.app import create_app

REPO = Path(__file__).resolve().parents[2]
SORTER = REPO / "scenes" / "sorting-by-height.yaml"
SCRIPT = REPO / "examples" / "pyplc" / "sorting-by-height.py"
runner = CliRunner()


def record(tmp_path: Path, name: str, seconds: int = 60) -> Path:
    scene = tmp_path / name / SORTER.name  # its own folder: run writes lockfiles beside it
    scene.parent.mkdir()
    scene.write_text(SORTER.read_text(encoding="utf-8"), encoding="utf-8")
    out = tmp_path / f"{name}.ofrec"
    args = [
        "run", str(scene), "--script", str(SCRIPT), "--as-fast-as-possible",
        "--duration", str(seconds), "--fault", "CYL_01=jammed", "--fault-at", "10",
        "--record", str(out), "-q",
    ]  # fmt: skip
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    return out


def test_same_run_same_bytes_and_nothing_machine_specific(tmp_path):
    a, b = record(tmp_path, "a"), record(tmp_path, "b")
    print(f"60 s sorter recording: {a.stat().st_size} bytes")
    assert a.read_bytes() == b.read_bytes()  # different folders, same file
    text = gzip.decompress(a.read_bytes()).decode()
    header = json.loads(text.splitlines()[0])["header"]
    scene_text = json.dumps(header["scene_yaml"])[1:-1]  # the scene's own words, comments and all
    recorded = text.replace(scene_text, "")
    for leak in (str(tmp_path), str(REPO), "hiscontrols", "127.0.0.1", "token"):
        assert leak not in recorded, leak
    assert header["format"] == FORMAT and header["dt"] == 0.02
    assert header["scene_yaml"] == SORTER.read_text(encoding="utf-8")  # replays with no scene file
    assert a.stat().st_size < 1_000_000  # the repo's binary rule, if one were ever committed


def test_reader_gets_frames_io_events_and_the_end(tmp_path):
    rec = read_recording(record(tmp_path, "a"))
    assert not rec.truncated and rec.end["removed"] == {"short": 9, "tall": 9}
    assert rec.frames[0][0] == 0 and rec.frames[-1][0] == 3000
    assert [t for t, _ in rec.frames[:3]] == [0, 5, 10]
    [(tick, fault)] = rec.events
    assert tick == 500 and (fault["type"], fault["id"], fault["kind"]) == (
        "fault",
        "CYL_01",
        "jammed",
    )
    assert "fault" not in dict(rec.frames)[495]["components"]["CYL_01"]
    assert dict(rec.frames)[505]["components"]["CYL_01"]["fault"] == "jammed"
    assert "statistics" in rec.end and rec.end["error"] is None


def test_io_log_matches_the_core_tick_for_tick(tmp_path):
    rec = read_recording(record(tmp_path, "a"))
    recorded = [(t, c["PE_01_DI"]) for t, c in rec.io if "PE_01_DI" in c and t > 0]
    loaded = load(SORTER)
    plc, last, live = ScriptPlc(SCRIPT, loaded.io), False, []
    for _ in range(3000):
        if loaded.tick == 500:
            loaded.component("CYL_01").fault_inject("jammed")
        plc.scan(loaded.time)
        loaded.step(0.02)
        if loaded.io.get("PE_01_DI") != last:
            last = loaded.io.get("PE_01_DI")
            live.append((loaded.tick, last))
    print("PE_01_DI edges (tick, value):", recorded[:6], "...")
    assert recorded == live and len(recorded) == 2 * 10
    assert "6.50     325  PE_01_DI = True" in io_table(rec)


def test_a_recording_cut_short_still_replays(tmp_path):
    whole = record(tmp_path, "a", seconds=20)
    data = whole.read_bytes()
    cut = tmp_path / "cut.ofrec"
    cut.write_bytes(data[: len(data) * 2 // 3])
    rec = read_recording(cut)
    assert rec.truncated and rec.end is None
    assert 0 < rec.frames[-1][0] < 1000  # part of the run
    with pytest.raises(ValueError, match="not an OpenFactory recording"):
        bad = tmp_path / "bad.ofrec"
        bad.write_bytes(gzip.compress(b'{"hello": 1}\n'))
        read_recording(bad)


def test_replay_serves_frames_and_refuses_to_simulate(tmp_path):
    rec = read_recording(record(tmp_path, "a", seconds=4))
    runtime = ReplayRuntime(rec, speed=50.0)
    http = TestClient(create_app(runtime, web_dist=None))
    assert http.get("/api/scene").json()["mode"] == "replay"
    runtime.start()
    runtime._thread.join(5)
    status = http.get("/api/status").json()
    assert status["finished"] and status["tick"] == 200 and status["replay"]
    assert http.get("/api/state").json() == rec.frames[-1][1]
    refused = http.post("/api/fault", json={"id": "CYL_01", "kind": "jammed"})
    assert refused.status_code == 400 and "replay: no simulation" in refused.text
    assert http.post("/api/exam", json={"tag": "PE_01_DI", "on": True}).status_code == 404
    assert http.get("/api/fault").json() == {"active": [], "supported": {}}
    assert http.get("/api/stats").json()["sim_time_s"] == 4.0
