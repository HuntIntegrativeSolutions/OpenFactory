"""PLAN 4.1b: the scene editor in the browser. `openfactory edit` on a copy of the sorter: click
the first conveyor, change its speed in the schema-driven form, see the preview, save; the file's
diff is that one line. Done when (ruled 2026-11-10): this test and the form vitest pass."""

from __future__ import annotations

import contextlib
import difflib
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "tools" / "screenshot.py"
spec = importlib.util.spec_from_file_location("screenshot_tool", TOOL)
screenshot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(screenshot)


@pytest.fixture(autouse=True)
def _viewer_built(prerequisite):
    prerequisite(
        (REPO / "web" / "dist" / "index.html").is_file(), "viewer not built (npm run build)"
    )


def test_select_a_conveyor_change_its_speed_and_save_one_line(tmp_path):
    from playwright.sync_api import sync_playwright

    for f in (REPO / "scenes").glob("sorting-by-height*"):
        shutil.copy(f, tmp_path / f.name)  # the scene and its lockfiles
    scene = tmp_path / "sorting-by-height.yaml"
    before = scene.read_text(encoding="utf-8")
    locks = {p: p.read_bytes() for p in tmp_path.glob("*.modbus.yaml")}
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
            page.wait_for_function(
                "() => window.__openfactory.editor?.file === 'sorting-by-height.yaml'"
            )
            assert page.evaluate("() => window.__openfactory.physics") == "off: edit"
            assert "Click a component" in page.locator("#editor-panel").inner_text()

            # find a point on screen over CONV_01, near its origin, and click it like a user
            point = page.evaluate(
                """() => { const [x0, y0] = window.__openfactoryScreenOf('CONV_01');
                    for (let r = 0; r < 160; r += 8) for (let a = 0; a < 360; a += 30) {
                      const t = (a * Math.PI) / 180;
                      const x = x0 + r * Math.cos(t), y = y0 + r * Math.sin(t);
                      if (window.__openfactoryPick(x, y) === 'CONV_01') return [x, y]; }
                    return null; }"""
            )
            assert point, "no screen point picks CONV_01"
            page.mouse.click(*point)
            page.wait_for_function("() => window.__openfactory.editor.selected === 'CONV_01'")
            assert page.locator("#editor-selected").inner_text() == "CONV_01 (belt_conveyor)"
            speed = page.locator('[data-field="speed_mps"] input')
            assert speed.input_value() == "0.5"

            # a bad value is caught in the browser and never sent
            speed.fill("-1")
            speed.press("Tab")
            err = page.locator('[data-field="speed_mps"] .editor-error')
            assert "must be" in err.inner_text()
            assert page.evaluate("() => window.__openfactory.editor.ops") == 0

            # a good one is previewed: findings come back, Save is enabled
            speed.fill("0.6")
            speed.press("Tab")
            page.wait_for_function("() => window.__openfactory.editor.previews >= 1")
            assert page.evaluate("() => window.__openfactory.editor.ops") == 1
            assert page.evaluate("() => window.__openfactory.editor.errors") == 0
            assert "1 unsaved change" in page.locator("#editor-findings").inner_text()
            assert scene.read_text(encoding="utf-8") == before  # a preview writes nothing
            page.screenshot(path=str(tmp_path / "editor.png"))
            page.screenshot(path=str(REPO / "tools" / "screenshots" / "editor-4.1b.png"))

            page.locator("#editor-save").click()
            page.wait_for_function("() => window.__openfactory.editor.saves === 1")
            assert "Saved sorting-by-height.yaml" in page.locator("#editor-findings").inner_text()
            assert page.locator('[data-field="speed_mps"] input').input_value() == "0.6"
            browser.close()
    finally:
        proc.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(10)
        if proc.poll() is None:
            proc.kill()

    after = scene.read_text(encoding="utf-8")
    diff = list(difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm=""))
    changed = [d for d in diff if d[:1] in "+-" and not d.startswith(("+++", "---"))]
    assert len(changed) == 2, changed  # one line out, one line in
    old, new = changed[0][1:], changed[1][1:]
    assert "speed_mps: 0.5" in old and new == old.replace("speed_mps: 0.5", "speed_mps: 0.6")
    assert {p: p.read_bytes() for p in locks} == locks  # the lockfiles are never written


@pytest.mark.parametrize("width", [1280, 640])
def test_the_status_lines_stay_clear_of_the_editor_panel(tmp_path, width):
    """PLAN 4.1c fix 3: with the panel open, the HUD's status lines wrap within the part of the
    window the panel does not cover, and the quality control (PLAN 4.14) does not meet it."""
    from playwright.sync_api import sync_playwright

    for f in (REPO / "scenes").glob("sorting-by-height*"):
        shutil.copy(f, tmp_path / f.name)
    web = screenshot.free_port()
    scene = tmp_path / "sorting-by-height.yaml"
    cmd = [sys.executable, "-m", "openfactory", "edit", str(scene), "-q", "--web-port", str(web)]
    proc = subprocess.Popen(cmd, cwd=REPO, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        screenshot.wait_for_api(web, proc, timeout=60)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=screenshot.CHROMIUM_ARGS)
            page = browser.new_page(viewport={"width": width, "height": 720})
            page.goto(f"http://127.0.0.1:{web}/")
            page.wait_for_function("() => window.__openfactory && window.__openfactory.ready")
            page.wait_for_function("() => window.__openfactory.editor?.file !== undefined")
            page.wait_for_function(
                "() => document.getElementById('hud').textContent.includes('frame')"
            )
            rect = (
                "(id) => { const r = document.getElementById(id).getBoundingClientRect();"
                " return [r.left, r.top, r.right, r.bottom]; }"
            )
            hud = page.evaluate(rect, "hud")
            panel = page.evaluate(rect, "editor-panel")
            quality = page.evaluate(rect, "quality")
            page.screenshot(path=str(tmp_path / f"editor-{width}.png"))
            browser.close()
    finally:
        proc.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(10)
    assert hud[2] <= panel[0], f"status lines {hud} run under the panel {panel}"
    assert quality[2] <= panel[0], f"quality control {quality} meets the panel {panel}"
