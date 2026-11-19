"""PLAN 5.14e: capture. A live run keeps the last 60 s (frames every 5 ticks; changes from the
change log), and ``POST /api/capture`` writes a folder of six files whose ``last-60s.ofrec`` is
the same run as a whole recording over that window and ends at the capture's tick. The endpoint's
security: loopback only, another Origin and a body that is not JSON refused, the picture capped
at 5 MB, no client-supplied path or file name, off in exam mode for a trainee. The note is data:
escaped in ``report.md``, never a path, a file name or a command."""

from __future__ import annotations

import base64
import json
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openfactory.capture import FILES, MAX_IMAGE_BYTES, clean_note, escape_markdown, scene_slug
from openfactory.core.loader import load
from openfactory.pyplc import ScriptPlc
from openfactory.record import Recorder, ReplayRuntime, read_recording, recording_header
from openfactory.server.app import create_app
from openfactory.server.runtime import Runtime

REPO = Path(__file__).resolve().parents[2]
SORTER = REPO / "scenes" / "sorting-by-height.yaml"
SCRIPT = REPO / "examples" / "pyplc" / "sorting-by-height.py"


def png(width: int = 4, height: int = 3) -> bytes:
    """A real (tiny, grey) PNG, made with the standard library."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    rows = b"".join(b"\x00" + b"\x80\x80\x80" * width for _ in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(rows))
            + chunk(b"IEND", b""))  # fmt: skip


def data_url(image: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(image).decode()


def body(**over) -> dict:  # noqa: ANN003
    out = {
        "image": data_url(png()),
        "camera": {"position": [1.0, 2.0, 3.0], "target": [0.0, 0.5, 0.0], "plan": False},
        "view": "view=1,2,3,0,0.5,0",
        "selected": "CYL_01",
        "note": "box jammed here",
        "viewer_tick": 0,
        "labels": "tags",
    }
    out.update(over)
    return out


def sorter(exam: bool = False) -> Runtime:
    loaded = load(SORTER)
    if exam:
        loaded.enable_exam()
    runtime = Runtime(loaded, realtime=False, controller=ScriptPlc(SCRIPT, loaded.io))
    runtime.recording_header = recording_header(runtime, SORTER.read_text("utf-8"), None)
    return runtime


def run(runtime: Runtime, ticks: int) -> None:
    for _ in range(ticks):
        runtime._step(runtime.scheduler.dt)


def client(runtime, tmp_path: Path, **kw) -> TestClient:  # noqa: ANN001, ANN003
    app = create_app(runtime, web_dist=None, capture_dir=tmp_path / "caps", **kw)
    return TestClient(app, base_url="http://127.0.0.1:8000")


@pytest.fixture(scope="module")
def captured(tmp_path_factory):  # noqa: ANN001, ANN201
    """The sorter run 75 s under its script and recorded whole, then captured at its last tick."""
    tmp = tmp_path_factory.mktemp("capture")
    runtime = sorter()
    runtime.recorder = Recorder(tmp / "whole.ofrec", runtime.recording_header, runtime.snapshot)
    run(runtime, 3750)
    runtime.recorder.close({})
    res = client(runtime, tmp).post("/api/capture", json=body(viewer_tick=3748))
    assert res.status_code == 200, res.text
    return runtime, tmp, res.json()


def test_a_capture_is_six_files_in_a_folder_named_by_the_server(captured):
    runtime, tmp, answer = captured
    tick = runtime.snapshot["tick"]
    folder = tmp / "caps" / "sorting-by-height" / str(tick)
    assert answer["tick"] == tick and Path(answer["folder"]) == folder
    assert sorted(answer["files"]) == sorted(FILES)
    assert sorted(p.name for p in folder.iterdir()) == sorted(FILES)
    assert (folder / "view.png").read_bytes() == png()
    view = json.loads((folder / "view.json").read_text())
    assert view["camera"]["position"] == [1.0, 2.0, 3.0] and view["view"] == "view=1,2,3,0,0.5,0"
    assert view["selected"] == "CYL_01" and view["viewer_tick"] == 3748
    assert json.loads((folder / "snapshot.json").read_text()) == json.loads(
        json.dumps(runtime.snapshot)
    )
    look = (folder / "look.md").read_text()
    assert look.startswith("# sorting-by-height at t = 75.00 s (tick 3750)")
    assert "CYL_01" in look.split("\n# ", 2)[-1]  # the selected component's report follows
    report = (folder / "report.md").read_text()
    assert "> box jammed here" in report and "Selected component: CYL\\_01" in report


def test_the_piece_is_the_whole_recording_over_the_last_60_s_and_ends_at_the_capture(captured):
    runtime, tmp, _ = captured
    tick = runtime.snapshot["tick"]
    piece = read_recording(tmp / "caps" / "sorting-by-height" / str(tick) / "last-60s.ofrec")
    whole = read_recording(tmp / "whole.ofrec")
    assert not piece.truncated and piece.frames[-1][0] == tick
    first = piece.frames[0][0]
    assert first == tick - 3000 + 5 and len(piece.frames) == 600  # 60 s at 10 Hz
    # its first line is the whole I/O table, as tick 0's is
    assert piece.io[0] == (
        first,
        dict(whole.frames[[k for k, _ in whole.frames].index(first)][1]["io"]),
    )
    # every frame and every change after the first frame, exactly as the whole recording has them
    assert piece.frames == [(k, f) for k, f in whole.frames if first <= k <= tick]
    assert piece.io[1:] == [(k, c) for k, c in whole.io if first < k <= tick]
    assert piece.header["scene"] == whole.header["scene"]
    assert piece.header["scene_yaml"] == whole.header["scene_yaml"]
    assert piece.end == {"capture": {"from_tick": first, "tick": tick}}


def test_the_piece_replays_and_ends_at_the_capture(captured):
    runtime, tmp, _ = captured
    tick = runtime.snapshot["tick"]
    piece = read_recording(tmp / "caps" / "sorting-by-height" / str(tick) / "last-60s.ofrec")
    replay = ReplayRuntime(piece, speed=200.0)
    replay.start()
    replay._thread.join(10)  # 60 s at 200x: 0.3 s, not the 75 s before the piece's start
    assert replay.finished and replay.snapshot["tick"] == tick
    # and `look` on it does not list the first line's whole table as changes
    text = replay.look(since_s=600)
    assert "older changes are no longer held" in text
    done = subprocess.run(
        [sys.executable, "-m", "openfactory", "look",
         str(tmp / "caps" / "sorting-by-height" / str(tick) / "last-60s.ofrec")],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    assert done.stdout.startswith("# sorting-by-height at t = 75.00 s (tick 3750)")


@pytest.mark.parametrize(
    "note",
    [
        "```\n# Heading\n<script>alert(1)</script>",
        "../../../etc/passwd",
        "$(touch pwned) `rm -rf /` ; echo hi > x",
        "[link](http://example.invalid) ![img](x.png) | a | b |",
        "line\r\nwith a CR and a \x00 NUL and \x1b[31m an escape",
    ],
)
def test_the_note_is_data_escaped_in_the_report_and_never_a_path(tmp_path, note):
    runtime = sorter()
    run(runtime, 50)
    http = client(runtime, tmp_path)
    before = set(tmp_path.rglob("*"))
    res = http.post("/api/capture", json=body(note=note, selected="../../x"))
    assert res.status_code == 200, res.text
    folder = tmp_path / "caps" / "sorting-by-height" / "50"
    written = {folder.parent.parent, folder.parent, folder} | {folder / f for f in FILES}
    assert set(tmp_path.rglob("*")) - before == written
    report = (folder / "report.md").read_text()
    section = report.split("## Note\n\n", 1)[1].split("\n\n## ", 1)[0]
    for line in section.split("\n"):
        assert line.startswith(">"), line  # every line of the note is quoted
    assert "<script>" not in report and "```" not in report and "\n#" not in section
    assert "](" not in section and "\x00" not in report and "\x1b" not in report
    assert "Selected component: none" in report  # not a component: left out
    assert not (tmp_path / "pwned").exists() and not Path("pwned").exists()


def test_escaping_and_cleaning_rules():
    assert escape_markdown("a_b *c* `d` <e> & #f") == r"a\_b \*c\* \`d\` &lt;e&gt; &amp; \#f"
    assert clean_note("a\r\nb\x00c\x07") == "a\nbc"
    assert len(clean_note("x" * 10_000)) == 2000
    assert scene_slug("../../evil name") == "evil-name" and scene_slug("///") == "scene"


def test_two_captures_of_one_tick_get_two_folders(tmp_path):
    runtime = sorter()
    run(runtime, 10)
    http = client(runtime, tmp_path)
    names = [Path(http.post("/api/capture", json=body()).json()["folder"]).name for _ in range(2)]
    assert names == ["10", "10-2"]


def test_another_origin_a_form_and_a_request_off_loopback_are_refused(tmp_path):
    runtime = sorter()
    run(runtime, 10)
    http = client(runtime, tmp_path)
    res = http.post("/api/capture", json=body(), headers={"Origin": "http://evil.example"})
    assert res.status_code == 403
    res = http.post("/api/capture", content=json.dumps(body()),
                    headers={"Content-Type": "text/plain"})  # fmt: skip
    assert res.status_code == 415
    ok = http.post("/api/capture", json=body(), headers={"Origin": "http://127.0.0.1:8000"})
    assert ok.status_code == 200  # this server's own page
    app = create_app(runtime, web_dist=None, bind="0.0.0.0", capture_dir=tmp_path / "wide")
    wide = TestClient(app, base_url="http://127.0.0.1:8000")
    res = wide.post("/api/capture", json=body())
    assert res.status_code == 403 and "loopback" in res.json()["detail"]
    assert not (tmp_path / "wide").exists()


def test_a_picture_over_5_mb_is_refused_before_and_after_decoding(tmp_path):
    runtime = sorter()
    run(runtime, 10)
    http = client(runtime, tmp_path)
    over = png() + b"\x00" * (MAX_IMAGE_BYTES + 1 - len(png()))  # 5 MB + 1 byte, decoded
    res = http.post("/api/capture", json=body(image=data_url(over)))
    assert res.status_code == 413
    huge = "data:image/png;base64," + "A" * (8 * 1024 * 1024)  # past the body cap
    res = http.post("/api/capture", json=body(image=huge))
    assert res.status_code == 413
    at_cap = png() + b"\x00" * (MAX_IMAGE_BYTES - len(png()))  # exactly 5 MB is taken
    assert http.post("/api/capture", json=body(image=data_url(at_cap))).status_code == 200
    assert not (tmp_path / "caps" / "sorting-by-height" / "10-2").exists()


def test_no_client_path_or_file_name_and_only_a_png(tmp_path):
    runtime = sorter()
    run(runtime, 10)
    http = client(runtime, tmp_path)
    for extra in ({"path": "/tmp/x"}, {"folder": "../x"}, {"filename": "a.png"}):
        assert http.post("/api/capture", json=body(**extra)).status_code == 422
    gif = "data:image/gif;base64," + base64.b64encode(b"GIF89a").decode()
    assert http.post("/api/capture", json=body(image=gif)).status_code == 400
    fake = data_url(b"not a png at all")
    assert http.post("/api/capture", json=body(image=fake)).status_code == 400
    bad = body(camera={"position": [1, 2], "target": [0, 0, 0]})
    assert http.post("/api/capture", json=bad).status_code == 422
    assert not (tmp_path / "caps").exists()


def test_off_in_exam_mode_for_a_trainee(tmp_path):
    runtime = sorter(exam=True)
    run(runtime, 10)
    res = client(runtime, tmp_path).post("/api/capture", json=body())
    assert res.status_code == 403 and "exam" in res.json()["detail"]
    teacher = client(runtime, tmp_path, fault_token="s3cret")
    res = teacher.post("/api/capture", json=body(), headers={"X-OpenFactory-Fault-Token": "nope"})
    assert res.status_code == 403
    res = teacher.post("/api/capture", json=body(),
                       headers={"X-OpenFactory-Fault-Token": "s3cret"})  # fmt: skip
    assert res.status_code == 200  # the instructor's token opens it
    assert (tmp_path / "caps" / "sorting-by-height" / "10" / "last-60s.ofrec").is_file()


def test_a_replay_has_nothing_to_capture(tmp_path):
    rec = read_recording(REPO / "kits" / "sorting-by-height" / "sorting-by-height.ofrec")
    http = client(ReplayRuntime(rec), tmp_path)
    assert http.post("/api/capture", json=body()).status_code == 404


def test_the_capture_folder_inside_the_repository_is_git_ignored():
    """``openfactory-captures/`` beside where ``run`` started (ruling 2) is ignored: a capture
    inside the repository leaves ``git status`` as it was."""
    runtime = sorter()
    run(runtime, 12)
    base = REPO / "openfactory-captures"
    existed = base.exists()

    def status() -> str:
        command = ["git", "status", "--short", "--untracked-files=all"]
        return subprocess.run(command, cwd=REPO, capture_output=True, text=True, check=True).stdout

    before = status()
    app = create_app(runtime, web_dist=None, capture_dir=base)
    res = TestClient(app, base_url="http://127.0.0.1:8000").post("/api/capture", json=body())
    folder = Path(res.json()["folder"])
    folder = folder if folder.is_absolute() else Path.cwd() / folder
    try:
        assert res.status_code == 200 and folder.is_dir()
        assert status() == before
    finally:
        for f in folder.iterdir():
            f.unlink()
        folder.rmdir()
        if not existed:
            import shutil

            shutil.rmtree(base)


def test_openfactory_captures_lists_newest_first_and_prints_the_newest(tmp_path):
    runtime = sorter()
    run(runtime, 10)
    http = client(runtime, tmp_path)
    http.post("/api/capture", json=body(note="first"))
    run(runtime, 10)
    http.post("/api/capture", json=body(note="second"))
    done = subprocess.run(
        [sys.executable, "-m", "openfactory", "captures", "--capture-dir", str(tmp_path / "caps")],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    lines = done.stdout.strip().split("\n")
    newest = tmp_path / "caps" / "sorting-by-height" / "20"
    assert lines[0].startswith(str(newest)) and "second" in lines[0]
    assert lines[1].startswith(str(newest.parent / "10")) and "first" in lines[1]
    assert lines[-1] == str(newest)
    empty = subprocess.run(
        [sys.executable, "-m", "openfactory", "captures", "--capture-dir", str(tmp_path / "no")],
        capture_output=True, text=True,
    )  # fmt: skip
    assert empty.returncode == 1 and "no captures" in empty.stdout
