"""PLAN 2.12 Done-when, record and replay: the sorter is recorded for 60 s under its script with a
fault at 10 s, the scene file is deleted, and `openfactory replay` serves the recording alone to
the viewer. The viewer draws every part where the recording has it at the tick it shows, with no
physics, marks REPLAY, and shows the fault on CYL_01's label from its tick."""

from __future__ import annotations

import contextlib
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from openfactory.record import read_recording

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


def test_replay_from_the_file_alone_draws_what_was_recorded(tmp_path):
    from playwright.sync_api import sync_playwright

    out = Path(os.environ.get("OPENFACTORY_REPLAY_OUT", tmp_path))
    work = tmp_path / "scene"
    work.mkdir()
    scene = work / SORTER.name
    scene.write_text(SORTER.read_text(encoding="utf-8"), encoding="utf-8")
    recording = tmp_path / "sorter.ofrec"
    subprocess.run(
        [
            sys.executable, "-m", "openfactory", "run", str(scene), "--script", str(SCRIPT),
            "--as-fast-as-possible", "--duration", "60", "--fault", "CYL_01=jammed",
            "--fault-at", "10", "--record", str(recording), "-q",
        ],
        cwd=REPO, check=True, capture_output=True,
    )  # fmt: skip
    shutil.rmtree(work)  # no scene, no lockfiles: the recording must carry everything
    rec = read_recording(recording)
    frames = dict(rec.frames)

    web_port = screenshot.free_port()
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "openfactory", "replay", str(recording), "-q",
            "--web-port", str(web_port), "--speed", "2",
        ],
        cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )  # fmt: skip
    try:
        screenshot.wait_for_api(web_port, proc)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=screenshot.CHROMIUM_ARGS)
            page = browser.new_page(viewport={"width": 1280, "height": 720})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"http://127.0.0.1:{web_port}/")
            page.wait_for_function("() => window.__openfactory?.ready")
            assert page.evaluate("() => window.__openfactory.physics") == "off: replay"

            # The viewer's part targets are the recorded positions for the tick it is showing.
            compared = 0
            for at in (4.0, 9.0, 14.0, 20.0):
                page.wait_for_function(f"() => window.__openfactory.simTime >= {at}")
                tick, targets = page.evaluate(
                    "() => [window.__openfactory.tick, window.__openfactoryPartTargets()]"
                )
                expected = {p["id"]: p["position"] for p in frames[tick]["parts"]}
                assert targets.keys() == expected.keys(), tick
                for pid, pos in expected.items():
                    assert targets[pid] == pytest.approx(pos, abs=1e-9), (tick, pid)
                compared += len(expected)
                print(f"tick {tick}: {len(expected)} parts drawn where the recording has them")
            assert compared > 0

            # The fault shows from its tick (500, t = 10 s; already past at t = 20 s).
            labels = page.eval_on_selector_all("div", "els => els.map(e => e.textContent)")
            assert "CYL_01 ⚠ jammed" in labels
            hud = [t for t in labels if t and t.startswith("OpenFactory · sorting-by-height")]
            assert hud and "REPLAY" in hud[0]
            page.screenshot(path=str(out / "replay.png"))
            browser.close()
        assert errors == []
    finally:
        proc.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(10)
        if proc.poll() is None:
            proc.kill()
    ok, verdict = screenshot.check_image(out / "replay.png")
    assert ok, verdict
