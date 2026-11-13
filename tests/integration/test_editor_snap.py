"""PLAN 4.1d Done-when (ruled 2026-11-10 decision 7): in the editor, a conveyor dragged near
another's discharge snaps to it: the ends within 1 mm, the headings equal, the connection added,
and after Save the lint is clean (no W212, no W213, nothing else)."""

from __future__ import annotations

import contextlib
import importlib.util
import math
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from openfactory.core.schema import load_scene
from openfactory.core.snaps import best_gap
from openfactory.lint import lint_scene

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "tools" / "screenshot.py"
spec = importlib.util.spec_from_file_location("screenshot_tool", TOOL)
screenshot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(screenshot)

SCENE = """\
# PLAN 4.1d test scene (tests only): two belts in line with a 0.3 m gap and no connection.
# Training and demo use only. Not a safety-rated simulation.
openfactory: "0.1"
scene:
  name: snap-two-belts
  description: Two belts to snap end to end in the editor.
  author: HIS Controls
plc:
  protocol: modbus_tcp
  role: server
components:
  - id: CONV_01
    type: belt_conveyor
    description: Infeed Belt Conveyor
    position: [0, 0, 0]
    length: 2.0
    width: 0.5
    speed_mps: 0.5
    io: {run: CONV_01_RUN_DO}
  - id: CONV_02
    type: belt_conveyor
    description: Outfeed Belt Conveyor
    position: [2.3, 0, 0.0]
    rotation_deg: 0
    length: 2.0
    width: 0.5
    speed_mps: 0.5
    io: {run: CONV_02_RUN_DO}
"""


@pytest.fixture(autouse=True)
def _viewer_built(prerequisite):
    prerequisite(
        (REPO / "web" / "dist" / "index.html").is_file(), "viewer not built (npm run build)"
    )


def test_a_conveyor_dragged_near_anothers_discharge_snaps_and_connects(tmp_path):
    from playwright.sync_api import sync_playwright

    scene = tmp_path / "snap-two-belts.yaml"
    scene.write_text(SCENE, encoding="utf-8")
    for enc in ("float32", "int16_scaled"):  # the lockfiles, so the lint starts clean
        subprocess.run(
            [sys.executable, "-m", "openfactory", "export", str(scene), "--analog-encoding", enc,
             "-o", str(tmp_path / f"io-{enc}.csv")],
            cwd=REPO, check=True, capture_output=True,
        )  # fmt: skip
    assert lint_scene(scene) == []  # clean before: the 0.3 m gap is no W212
    web = screenshot.free_port()
    cmd = [sys.executable, "-m", "openfactory", "edit", str(scene), "-q", "--web-port", str(web)]
    proc = subprocess.Popen(cmd, cwd=REPO, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        screenshot.wait_for_api(web, proc, timeout=60)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=screenshot.CHROMIUM_ARGS)
            page = browser.new_page(viewport={"width": 1280, "height": 720})
            page.goto(f"http://127.0.0.1:{web}/")
            page.wait_for_function("() => window.__openfactory && window.__openfactory.ready")
            page.wait_for_function("() => window.__openfactory.editor?.file !== undefined")
            doc = page.evaluate("() => fetch('/api/scene').then((r) => r.json())")
            ports = {c["id"]: c.get("snaps") for c in doc["components"]}
            assert [p["name"] for p in ports["CONV_01"]] == ["infeed", "discharge"]

            page.evaluate("() => window.__openfactoryEditorSelect('CONV_02')")
            page.wait_for_function("() => window.__openfactory.editor.selected === 'CONV_02'")
            page.locator("#editor-mode-move").click()
            g = page.evaluate("() => window.__openfactoryGizmo()")
            # drag back along the x arrow, toward CONV_01's discharge: about 0.25 m, inside the
            # 0.15 m capture but not onto it (the 0.05 m grid stops short or long of 2.0)
            unit = math.dist(g["origin"], g["xArrow"]) / 0.6  # screen pixels per metre along x
            back = (g["origin"][0] - g["xArrow"][0], g["origin"][1] - g["xArrow"][1])
            start = g["xArrow"]
            dx, dy = back
            norm = math.hypot(dx, dy)
            page.mouse.move(*start)
            page.mouse.down()
            for i in range(1, 21):
                f = 0.26 * unit * i / 20 / norm
                page.mouse.move(start[0] + dx * f, start[1] + dy * f)
            page.wait_for_function("() => window.__openfactory.editor.snapping !== null")
            out = Path(os.environ.get("OPENFACTORY_EDITOR_OUT", tmp_path))
            page.screenshot(path=str(out / "editor-4.1d-snapping.png"))  # the markers
            previews = page.evaluate("() => window.__openfactory.editor.previews")
            page.mouse.up()
            page.wait_for_function(f"() => window.__openfactory.editor.previews > {previews}")
            snap = page.evaluate("() => window.__openfactory.editor.snap")
            assert snap["connection"] == {"from": "CONV_01", "to": "CONV_02"}, snap
            assert (snap["mine"], snap["port"]) == ("infeed", "discharge")
            assert page.evaluate("() => window.__openfactory.editor.errors") == 0
            page.screenshot(path=str(out / "editor-4.1d-snapped.png"))
            page.locator("#editor-save").click()
            page.wait_for_function("() => window.__openfactory.editor.saves === 1")
            browser.close()
    finally:
        proc.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(10)
        if proc.poll() is None:
            proc.kill()

    saved = yaml.safe_load(scene.read_text(encoding="utf-8"))
    assert {"from": "CONV_01", "to": "CONV_02"} in saved["connections"]
    loaded = load_scene(scene)
    by_id = {c.id: c for c in loaded.components}
    g = best_gap(by_id["CONV_01"], by_id["CONV_02"])
    assert g is not None
    assert g.distance_m <= 0.001 and g.rise_m <= 0.001, g  # ends within 1 mm
    assert g.turn_deg <= 0.1, g  # facing, so headings equal
    assert by_id["CONV_02"].rotation_deg == pytest.approx(by_id["CONV_01"].rotation_deg, abs=0.1)
    assert lint_scene(scene) == []  # clean after: joined, no W212 or W213
