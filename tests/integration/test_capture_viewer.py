"""PLAN 5.14e Done-when, in the browser: Capture is pressed during a sorter run, a note typed, and
the folder holds all six files; its recording replays and ends at the capture's tick; the note
is in ``report.md``; the picture is the viewer's (not blank). The C key opens the same note box,
and Esc closes it without writing anything. The folder is then a strip's input (PLAN 5.14f).
Pictures go to the test's own folder (or ``$OPENFACTORY_CAPTURE_OUT``), never the repository."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from openfactory.capture import FILES
from openfactory.record import ReplayRuntime, read_recording

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("screenshot_tool", REPO / "tools" / "screenshot.py")
screenshot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(screenshot)
SORTER = REPO / "scenes" / "sorting-by-height.yaml"
SCRIPT = REPO / "examples" / "pyplc" / "sorting-by-height.py"


@pytest.fixture(autouse=True)
def _viewer_built(prerequisite):
    prerequisite(
        (REPO / "web" / "dist" / "index.html").is_file(), "viewer not built (npm run build)"
    )


def test_capture_during_a_sorter_run_writes_the_six_files(tmp_path):
    base = tmp_path / "caps"
    with screenshot.running_viewer(
        SORTER, forces=[], size=(1280, 720), script=SCRIPT, view={"labels": "tags"},
        run_args=["--capture-dir", str(base)],
    ) as (page, port, console):  # fmt: skip
        screenshot.wait_for_viewer(page, console, seconds=8.0, timeout=90)
        # the C key opens the note box; Esc closes it and nothing is written
        page.locator("body").press("c")
        page.wait_for_function("() => window.__openfactory.capturing === true")
        page.locator("#capture-note").press("Escape")
        assert page.evaluate("() => window.__openfactory.capturing") is False
        assert not base.exists()
        # the button: the picture now, then the note, Enter saves
        page.locator("#capture").click()
        page.locator("#capture-note").fill("box jammed here <b>#1</b>")
        page.locator("#capture-note").press("Enter")
        page.wait_for_function("() => window.__openfactory.lastCapture", timeout=30_000)
        answer = page.evaluate("() => window.__openfactory.lastCapture")
        assert "folder" in answer, (answer, console)
        assert page.locator("#capture-status").inner_text().startswith("Captured to ")
    folder = base / "sorting-by-height" / str(answer["tick"])
    assert sorted(p.name for p in folder.iterdir()) == sorted(FILES)
    rec = read_recording(folder / "last-60s.ofrec")
    assert not rec.truncated and rec.frames[-1][0] == answer["tick"]
    replay = ReplayRuntime(rec, speed=100.0)
    replay.start()
    replay._thread.join(10)
    assert replay.finished and replay.snapshot["tick"] == answer["tick"]
    report = (folder / "report.md").read_text(encoding="utf-8")
    assert "> box jammed here &lt;b&gt;\\#1&lt;/b&gt;" in report
    width, height, _, _ = screenshot.read_png(folder / "view.png")
    assert (width, height) == (1280, 720)
    ok, summary = screenshot.check_image(folder / "view.png")
    assert ok, summary
    # "what I just saw" as a strip, in one command: the capture folder is the input (PLAN 5.14f)
    done = subprocess.run(
        [sys.executable, "-m", "openfactory", "strip", str(folder), "--frames", "3",
         "--out", str(tmp_path / "strip.png")],
        cwd=tmp_path, capture_output=True, text=True, timeout=240,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr
    last = rec.frames[-1][0]  # the capture's tick, off the 5-tick grid as a rule
    assert f"| 3 | {last * rec.dt:.2f} | {last} |" in done.stdout  # its last still: the capture
    assert (tmp_path / "strip.png").is_file() and (tmp_path / "strip.md").is_file()
    out = os.environ.get("OPENFACTORY_CAPTURE_OUT")
    if out:
        shutil.copy(folder / "view.png", Path(out) / "capture-view.png")
