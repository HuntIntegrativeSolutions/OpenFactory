"""PLAN 5.4e: the editor on a plant, in the browser. `openfactory edit wash-plant.yaml --area
WASH` on a copy of the sample: the LOAD area is drawn dimmed; selecting its belt says which file
to open and offers no form; a tag LOAD already has, typed in WASH, is refused before save naming
LOAD's file; an edit of the wash belt is saved and only areas/wash.yaml changes. A screenshot at
quality low is written for a person to look at."""

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
SAMPLE = REPO / "scenes" / "plants" / "wash-plant"


@pytest.fixture(autouse=True)
def _viewer_built(prerequisite):
    prerequisite(
        (REPO / "web" / "dist" / "index.html").is_file(), "viewer not built (npm run build)"
    )


def sample_copy(tmp_path: Path) -> Path:
    folder = tmp_path / "wash-plant"
    shutil.copytree(SAMPLE, folder, ignore=shutil.ignore_patterns("__pycache__"))
    (tmp_path / "_library").mkdir()
    shutil.copyfile(SAMPLE.parents[1] / "_library" / "driven_item.yaml",
                    tmp_path / "_library" / "driven_item.yaml")  # fmt: skip
    (tmp_path / "openfactory.toml").write_text("", encoding="utf-8")  # a project root
    return folder / "wash-plant.yaml"


def click_on(page, comp_id: str) -> None:
    """Click a screen point over ``comp_id`` as a user would."""
    point = page.evaluate(
        """(id) => { const [x0, y0] = window.__openfactoryScreenOf(id);
            for (let r = 0; r < 200; r += 6) for (let a = 0; a < 360; a += 20) {
              const t = (a * Math.PI) / 180;
              const x = x0 + r * Math.cos(t), y = y0 + r * Math.sin(t);
              if (x < 0 || y < 0 || x > window.innerWidth - 380) continue;
              if (window.__openfactoryPick(x, y) === id) return [x, y]; }
            return null; }""",
        comp_id,
    )
    assert point, f"no screen point picks {comp_id}"
    page.mouse.click(*point)
    page.wait_for_function(f"() => window.__openfactory.editor.selected === '{comp_id}'")


def test_edit_one_area_of_a_plant_with_the_other_dimmed(tmp_path):
    from playwright.sync_api import sync_playwright

    plant = sample_copy(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    web = screenshot.free_port()
    cmd = [sys.executable, "-m", "openfactory", "edit", str(plant), "--area", "WASH", "-q",
           "--web-port", str(web)]  # fmt: skip
    proc = subprocess.Popen(cmd, cwd=REPO, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        screenshot.wait_for_api(web, proc, timeout=60)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=screenshot.CHROMIUM_ARGS)
            page = browser.new_page(viewport={"width": 1280, "height": 720})
            page.goto(f"http://127.0.0.1:{web}/?quality=low")
            page.wait_for_function("() => window.__openfactory && window.__openfactory.ready")
            page.wait_for_function("() => window.__openfactory.editor?.file === 'areas/wash.yaml'")
            assert page.evaluate("() => window.__openfactory.editor.plant") == "wash-plant.yaml"
            head = page.locator("#editor-head").inner_text()
            assert head == "EDIT MODE · areas/wash.yaml (plant wash-plant.yaml; the rest dimmed)"
            dimmed = set(page.evaluate("() => window.__openfactory.dimmed"))
            assert {"CV_101", "PE_101", "EMT_101"} <= dimmed  # LOAD's components
            assert not dimmed & {"CV_201", "TK_201", "P_201"}  # WASH's are not
            # out of the test's folder only with $OPENFACTORY_EDITOR_OUT (never the repository)
            out = Path(os.environ.get("OPENFACTORY_EDITOR_OUT", tmp_path))
            page.locator("#editor-fit").click()
            page.wait_for_timeout(300)
            page.screenshot(path=str(out / "editor-5.4e.png"))

            # another file's component: which file to open, no form, no gizmo, no operation
            click_on(page, "CV_101")
            assert page.locator("#editor-readonly").inner_text() == (
                "in areas/load.yaml: edit it with `openfactory edit wash-plant.yaml --area LOAD`"
            )
            assert page.evaluate("() => window.__openfactory.editor.readonly") == "areas/load.yaml"
            assert page.locator("#editor-form input").count() == 0
            page.keyboard.press("Delete")
            assert page.evaluate("() => window.__openfactory.editor.ops") == 0

            # a tag LOAD has, typed in WASH: refused before save, naming LOAD's file
            page.evaluate("() => window.__openfactoryEditorSelect('PE_201')")
            page.wait_for_function("() => window.__openfactory.editor.selected === 'PE_201'")
            tag = page.locator('[data-field="io.detect"] input')
            previews = page.evaluate("() => window.__openfactory.editor.previews")
            tag.fill("PE_101_DI")
            tag.press("Tab")
            page.wait_for_function(f"() => window.__openfactory.editor.previews > {previews}")
            assert page.evaluate("() => window.__openfactory.editor.errors") == 1
            findings = page.locator("#editor-findings").inner_text()
            assert "tag 'PE_101_DI' is in both areas/load.yaml and areas/wash.yaml" in findings
            assert page.locator("#editor-save").is_disabled()
            page.locator("#editor-revert").click()
            page.wait_for_function("() => window.__openfactory.editor.ops === 0")

            # an edit of WASH's own belt is saved
            click_on(page, "CV_201")
            assert page.locator("#editor-selected").inner_text() == "CV_201 (belt_conveyor)"
            speed = page.locator('[data-field="speed_mps"] input')
            previews = page.evaluate("() => window.__openfactory.editor.previews")
            speed.fill("0.35")
            speed.press("Tab")
            page.wait_for_function(f"() => window.__openfactory.editor.previews > {previews}")
            assert page.evaluate("() => window.__openfactory.editor.errors") == 0
            assert page.evaluate("() => window.__openfactory.dimmed").count("CV_101") == 1
            page.locator("#editor-save").click()
            page.wait_for_function("() => window.__openfactory.editor.saves === 1")
            assert "Saved areas/wash.yaml" in page.locator("#editor-findings").inner_text()
            browser.close()
    finally:
        proc.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(10)
        if proc.poll() is None:
            proc.kill()

    after = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    changed = sorted(p.relative_to(tmp_path).as_posix() for p in after
                     if p in before and after[p] != before[p])  # fmt: skip
    assert changed == ["wash-plant/areas/wash.yaml"]
    assert "speed_mps: 0.35" in (plant.parent / "areas" / "wash.yaml").read_text(encoding="utf-8")
