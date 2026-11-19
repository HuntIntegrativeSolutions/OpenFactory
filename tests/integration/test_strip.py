"""PLAN 5.14f Done-when, in the browser. Seeking a paused replay of the sorter's kit recording to
tick 300 draws the frame the recording holds at 300 (every part where it has it); the replay bar
steps and plays. The strip around the first tall box's push shows the plate retracted in the
first still and extended in a later one (pixel sample on the plate), and its table lists
``CYL_01_EXT_DO`` going true between the right two stills. A strip of the wash plant is made from
a recording written in the test's folder. Images go to ``$OPENFACTORY_STRIP_OUT`` (else the test's
own folder, never the repository) for a person to look at."""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

from openfactory.record import read_recording
from openfactory.strip import make_strip

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("screenshot_tool", REPO / "tools" / "screenshot.py")
screenshot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(screenshot)
KIT = REPO / "kits" / "sorting-by-height" / "sorting-by-height.ofrec"
WASH = REPO / "scenes" / "plants" / "wash-plant" / "wash-plant.yaml"
WASH_SCRIPT = REPO / "scenes" / "plants" / "wash-plant" / "wash-plant.py"
STOPPED = (0x9C, 0xA3, 0xAF)  # ISA-101 stopped: the pusher's plate when not extending
RUNNING = (0x22, 0xC5, 0x5E)  # ISA-101 running: the plate while extending (and a running belt)


@pytest.fixture(autouse=True)
def _viewer_built(prerequisite):
    prerequisite(
        (REPO / "web" / "dist" / "index.html").is_file(), "viewer not built (npm run build)"
    )


def out_dir(tmp_path: Path) -> Path:
    return Path(os.environ.get("OPENFACTORY_STRIP_OUT", tmp_path))


def post(port: int, path: str, body: dict) -> dict:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )  # fmt: skip
    with urllib.request.urlopen(req, timeout=5) as res:
        return json.load(res)


def test_seek_draws_the_recorded_frame_and_the_bar_steps(tmp_path):
    from playwright.sync_api import sync_playwright

    frames = dict(read_recording(KIT).frames)
    port = screenshot.free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "openfactory", "replay", str(KIT), "-q", "--paused",
         "--web-port", str(port)],
        cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )  # fmt: skip
    try:
        screenshot.wait_for_api(port, proc)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=screenshot.CHROMIUM_ARGS)
            page = browser.new_page(viewport={"width": 1280, "height": 720})
            page.goto(f"http://127.0.0.1:{port}/?quality=low")
            page.wait_for_function("() => window.__openfactory && window.__openfactory.ready")
            assert page.locator("#replay-bar").is_visible()
            assert post(port, "/api/replay/seek", {"tick": 300})["tick"] == 300
            page.wait_for_function(
                "() => window.__openfactory.tick === 300 && window.__openfactoryPartsSettled()"
            )
            assert screenshot.get_json(port, "/api/state") == frames[300]
            drawn = page.evaluate("() => window.__openfactoryPartTargets()")
            want = {p["id"]: p["position"] for p in frames[300]["parts"]}
            assert set(drawn) == set(want)
            for pid, pos in want.items():  # every part drawn where the recording has it
                assert drawn[pid] == pytest.approx(pos, abs=1e-6), pid
            assert page.locator("#replay-time").inner_text() == "6.00 s / 60.00 s"
            page.locator("#replay-forward").click()
            page.wait_for_function("() => window.__openfactory.tick === 305")
            page.keyboard.press("ArrowLeft")
            page.keyboard.press("ArrowLeft")
            page.wait_for_function("() => window.__openfactory.tick === 295")
            assert screenshot.get_json(port, "/api/replay")["paused"] is True
            page.locator("#replay-play").click()  # play on from 295
            page.wait_for_function("() => window.__openfactory.tick > 320", timeout=20_000)
            page.keyboard.press(" ")  # and pause
            page.wait_for_function("() => window.__openfactory.replay.paused === true")
            browser.close()
    finally:
        proc.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(10)


def colour(rgb: bytes, width: int, channels: int, x: float, y: float) -> tuple[int, ...]:
    i = (int(y) * width + int(x)) * channels
    return tuple(rgb[i : i + 3])


def test_the_strip_of_the_first_tall_box_push(tmp_path):
    rec = read_recording(KIT)
    push = next(k for k, c in rec.io if c.get("CYL_01_EXT_DO") is True)  # tick 430, 8.60 s
    out = out_dir(tmp_path) / "sorter-strip-push.png"
    sample = (0.0, 0.0, 0.2)  # on the plate once it is 0.2 m out, over the belt before that
    result = make_strip(KIT, out=out, at_s=push * rec.dt, span_s=2.0, frames=6,
                        look_at="CYL_01", labels="off", probes=[("CYL_01", sample)])  # fmt: skip
    ticks = result["ticks"]
    assert len(ticks) == 6 and ticks[0] < push < ticks[-1]
    width, height, channels, rgb = screenshot.read_png(out)
    assert width == 6 * 480 + 5 * 4 and height == 270 + 22
    frames = dict(rec.frames)
    stroke = [frames[k]["components"]["CYL_01"]["position_m"] for k in ticks]
    seen = []
    for still, (point,) in zip(result["stills"], result["probes"], strict=True):
        seen.append(colour(rgb, width, channels, still["x"] + point[0], still["y"] + point[1]))
    print(f"push at tick {push}; stills {ticks}; stroke {stroke}; plate sample {seen}")
    assert stroke[0] == 0.0 and seen[0] not in (STOPPED,)  # retracted: the belt shows there
    later = [i for i in range(1, 6) if stroke[i] >= 0.2 and seen[i] in (STOPPED, RUNNING)]
    assert later, (stroke, seen)  # extended: the plate's own colour at that point
    assert any(seen[i] != seen[0] for i in later)
    # the table: the command went true between the two stills around the push
    sections = result["table"].split("\n## ")[1:]
    with_push = [i for i, s in enumerate(sections) if "`CYL_01_EXT_DO` false → true" in s]
    assert with_push == [next(i for i in range(5) if ticks[i] < push <= ticks[i + 1])]
    assert out.with_suffix(".md").read_text(encoding="utf-8") == result["table"]


def test_a_strip_of_the_wash_plant_from_the_command_line(tmp_path):
    recording = tmp_path / "wash.ofrec"
    subprocess.run(
        [sys.executable, "-m", "openfactory", "run", str(WASH), "--script", str(WASH_SCRIPT),
         "--as-fast-as-possible", "--duration", "20", "--record", str(recording), "-q"],
        cwd=tmp_path, check=True, capture_output=True,
    )  # fmt: skip
    out = tmp_path / "wash-strip.png"
    done = subprocess.run(
        [sys.executable, "-m", "openfactory", "strip", str(recording), "--at", "12",
         "--labels", "tags", "--out", str(out)],
        cwd=tmp_path, capture_output=True, text=True, timeout=240,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr
    assert done.stdout.startswith("# Strip of wash-plant: 6 stills, t = 11.00 to 13.00 s")
    assert "## Still 5 to 6" in done.stdout and f"wrote {out}" in done.stdout
    width, height, _, _ = screenshot.read_png(out)
    assert (width, height) == (6 * 480 + 5 * 4, 270 + 22)
    if os.environ.get("OPENFACTORY_STRIP_OUT"):
        shutil.copy(out, out_dir(tmp_path) / "wash-plant-strip.png")
        shutil.copy(out.with_suffix(".md"), out_dir(tmp_path) / "wash-plant-strip.md")
