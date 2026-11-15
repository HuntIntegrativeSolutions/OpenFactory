"""PLAN 4.1j Done-when, the view and the acknowledgement (items 5 and 8 of 4.1h's awkward list).

Fit view: on a copy of the sorter, zoomed in close on CONV_01, a belt added from the palette
lands outside the view and the view refits by itself so every component is on screen (in the
part of the window the panel does not cover); a component then typed 12 m away is off screen
(a preview never reframes, 4.1c), and pressing Fit view brings every component back on screen.

Acknowledgement: typing the value a field already has fires no change event, so nothing used to
answer. Now the field's row says it was kept, and no operation is sent."""

from __future__ import annotations

import contextlib
import importlib.util
import os
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


@contextlib.contextmanager
def editor(scene: Path):
    from playwright.sync_api import sync_playwright

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
            page.wait_for_function("() => (window.__openfactory.editor?.palette ?? []).length > 0")
            yield page
            browser.close()
    finally:
        proc.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(10)
        if proc.poll() is None:
            proc.kill()


def sorter(tmp_path: Path) -> Path:
    scene = tmp_path / "sorting-by-height.yaml"
    shutil.copy(REPO / "scenes" / "sorting-by-height.yaml", scene)
    return scene


def out_dir(tmp_path: Path) -> Path:
    return Path(os.environ.get("OPENFACTORY_EDITOR_OUT", tmp_path))


def previews(page) -> int:
    return page.evaluate("() => window.__openfactory.editor.previews")


def test_add_outside_the_view_refits_and_fit_view_frames_every_component(tmp_path):
    with editor(sorter(tmp_path)) as page:
        assert page.evaluate("() => window.__openfactoryEditorOutOfView()") == []
        # zoom in close on CONV_01's infeed: most of the plant leaves the view
        page.evaluate("() => window.__openfactoryEditorLook(0.5, 0.0)")
        page.mouse.move(400, 360)
        for _ in range(8):
            page.mouse.wheel(0, -400)
            page.wait_for_timeout(80)
        page.wait_for_timeout(300)
        away = page.evaluate("() => window.__openfactoryEditorOutOfView()")
        assert away, "zooming in should leave some of the plant out of view"
        # a belt added here is placed clear of the plant (4.1e), outside this close view
        page.locator("#editor-palette").select_option("belt_conveyor")
        page.locator("#editor-add").click()
        page.wait_for_function(
            "() => { const p = window.__openfactory.editor.placed; return p && !p.waiting; }"
        )
        status = page.evaluate("() => window.__openfactory.editor")
        assert status["fit"] == "add" and status["fits"] == 1, status
        page.wait_for_timeout(300)
        assert page.evaluate("() => window.__openfactoryEditorOutOfView()") == []
        page.screenshot(path=str(out_dir(tmp_path) / "editor-4.1j-add-refit.png"))
        # a component typed 12 m away: the preview redraws it there and keeps the camera
        new_id = status["placed"]["id"]
        before = previews(page)
        box = page.locator('[data-field="position"] input').nth(0)
        box.fill("12")
        box.press("Tab")
        page.wait_for_function(f"() => window.__openfactory.editor.previews > {before}")
        page.wait_for_timeout(300)
        assert new_id in page.evaluate("() => window.__openfactoryEditorOutOfView()")
        assert page.evaluate("() => window.__openfactory.editor.fits") == 1
        page.locator("#editor-fit").click()
        page.wait_for_timeout(300)
        assert page.evaluate("() => window.__openfactory.editor.fit") == "button"
        assert page.evaluate("() => window.__openfactoryEditorOutOfView()") == []
        page.screenshot(path=str(out_dir(tmp_path) / "editor-4.1j-fit.png"))


def test_typing_the_value_a_field_has_says_it_was_kept(tmp_path):
    with editor(sorter(tmp_path)) as page:
        page.evaluate("() => window.__openfactoryEditorSelect('CONV_01')")
        page.wait_for_function("() => window.__openfactory.editor.selected === 'CONV_01'")
        speed = page.locator('[data-field="speed_mps"] input')
        same = speed.input_value()
        before = previews(page)
        speed.fill("")
        speed.type(same)  # typed again, key by key: no change event fires
        speed.press("Tab")
        ack = page.locator('[data-field="speed_mps"] .editor-ack')
        ack.wait_for(state="visible")
        assert "Kept" in ack.inner_text()
        status = page.evaluate("() => window.__openfactory.editor")
        assert status["acks"] == 1 and status["ack"] == "speed_mps", status
        assert status["ops"] == 0 and previews(page) == before  # nothing sent
        page.screenshot(path=str(out_dir(tmp_path) / "editor-4.1j-kept.png"))
        # the same number written another way ("0.50" for 0.5) changes nothing either
        speed.fill(f"{float(same):.2f}0")
        speed.press("Tab")
        page.wait_for_function("() => window.__openfactory.editor.acks === 2")
        assert page.evaluate("() => window.__openfactory.editor.ops") == 0
        # and a real change still goes through
        speed.fill("0.6")
        speed.press("Tab")
        page.wait_for_function(f"() => window.__openfactory.editor.previews > {before}")
        assert page.evaluate("() => window.__openfactory.editor.ops") == 1
