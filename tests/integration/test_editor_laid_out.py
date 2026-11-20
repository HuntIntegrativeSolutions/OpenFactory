"""PLAN 5.5, ruled 2026-11-19 (second set), ruling 2: moving or re-typing the position of a
laid-out component in the editor clears its `laid_out` mark (a person placed it); the mark stays
the scene author's to set by hand, a checkbox in the form. On a copy of the wash-water sample's
takeoff with every position removed, scaffolded and laid out (in the test's folder)."""

from __future__ import annotations

import contextlib
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from openfactory import takeoff as tk
from openfactory.scaffold import scaffold

REPO = Path(__file__).resolve().parents[2]
SAMPLE = REPO / "docs" / "samples" / "wash-skid" / "wash-skid.takeoff.yaml"
TOOL = REPO / "tools" / "screenshot.py"
spec = importlib.util.spec_from_file_location("screenshot_tool", TOOL)
screenshot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(screenshot)


@pytest.fixture(autouse=True)
def _viewer_built(prerequisite):
    prerequisite(
        (REPO / "web" / "dist" / "index.html").is_file(), "viewer not built (npm run build)"
    )


def laid_out_scene(tmp_path: Path) -> Path:
    data = yaml.safe_load(SAMPLE.read_text(encoding="utf-8"))
    for e in data["equipment"]:
        for key in ("layout", "mount", "at"):
            e.pop(key, None)
    data.pop("checked", None)
    takeoff = tmp_path / "bare.takeoff.yaml"
    takeoff.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    scene = tmp_path / "laid.yaml"
    scene.write_text(scaffold(tk.load(takeoff)).text, encoding="utf-8")
    return scene


def block(text: str, cid: str) -> str:
    return text.split(f"  - id: {cid}")[1].split("\n  - id: ")[0]


def test_retyping_a_laid_out_position_clears_the_mark(tmp_path):
    from playwright.sync_api import sync_playwright

    scene = laid_out_scene(tmp_path)
    assert "laid_out: true" in block(scene.read_text(encoding="utf-8"), "TK_101")
    out = Path(os.environ.get("OPENFACTORY_EDITOR_OUT", tmp_path))  # never the repository
    web = screenshot.free_port()
    cmd = [sys.executable, "-m", "openfactory", "edit", str(scene), "-q", "--web-port", str(web)]
    proc = subprocess.Popen(cmd, cwd=REPO, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        screenshot.wait_for_api(web, proc, timeout=60)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=screenshot.CHROMIUM_ARGS)
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(f"http://127.0.0.1:{web}/")
            page.wait_for_function("() => window.__openfactory && window.__openfactory.ready")
            page.wait_for_function("() => window.__openfactory.editor?.file === 'laid.yaml'")
            point = page.evaluate(
                """() => { const [x0, y0] = window.__openfactoryScreenOf('TK_101');
                    for (let r = 0; r < 160; r += 4) for (let a = 0; a < 360; a += 30) {
                      const t = (a * Math.PI) / 180;
                      const x = x0 + r * Math.cos(t), y = y0 + r * Math.sin(t);
                      if (window.__openfactoryPick(x, y) === 'TK_101') return [x, y]; }
                    return null; }"""
            )
            assert point, "no screen point picks TK_101"
            page.mouse.click(*point)
            page.wait_for_function("() => window.__openfactory.editor.selected === 'TK_101'")
            mark = page.locator('[data-field="laid_out"] input')
            assert mark.is_checked()
            mark.scroll_into_view_if_needed()
            page.screenshot(path=str(out / "editor-5.5-laid-out-checked.png"))

            x = page.locator('[data-field="position"] input').nth(0)
            x.fill("20")
            x.press("Tab")
            page.wait_for_function("() => window.__openfactory.editor.previews >= 1")
            page.wait_for_function("() => window.__openfactory.editor.ops === 2")  # + the clear
            assert not page.locator('[data-field="laid_out"] input').is_checked()
            page.locator('[data-field="laid_out"] input').scroll_into_view_if_needed()
            page.screenshot(path=str(out / "editor-5.5-laid-out-cleared.png"))
            page.locator("#editor-save").click()
            page.wait_for_function("() => window.__openfactory.editor.saves === 1")
            browser.close()
    finally:
        proc.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(10)
        if proc.poll() is None:
            proc.kill()
    after = block(scene.read_text(encoding="utf-8"), "TK_101")
    assert "position: [20" in after and "laid_out" not in after.replace("LAID OUT", "")
