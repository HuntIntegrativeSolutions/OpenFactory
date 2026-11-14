"""PLAN 4.1f Done-when, in the browser: Delete on a referenced component lists what goes with it
(and what is left to fix) before anything changes; Cancel changes nothing; confirming removes the
component and its references, and the saved scene loads."""

from __future__ import annotations

import contextlib
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from openfactory.core.schema import load_scene

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


def ask_delete(page, comp_id: str) -> dict:
    page.evaluate(f"() => window.__openfactoryEditorSelect('{comp_id}')")
    page.locator("#editor-delete").click()
    page.wait_for_function("() => window.__openfactory.editor.confirm !== null")
    return page.evaluate("() => window.__openfactory.editor.confirm")


def test_delete_lists_its_references_then_removes_them(tmp_path):
    for f in (REPO / "scenes").glob("sorting-by-height*"):
        shutil.copy(f, tmp_path / f.name)
    scene = tmp_path / "sorting-by-height.yaml"
    before = scene.read_text(encoding="utf-8")
    out = Path(os.environ.get("OPENFACTORY_EDITOR_OUT", tmp_path))
    with editor(scene) as page:
        confirm = ask_delete(page, "REM_02")
        assert confirm == {"id": "REM_02", "remove": ["check parts_reached: REM_02"], "fix": []}
        assert page.locator("#editor-confirm-remove li").all_inner_texts() == confirm["remove"]
        assert page.evaluate("() => window.__openfactory.editor.ops") == 0  # nothing yet
        page.screenshot(path=str(out / "editor-4.1f-confirm.png"))
        page.locator("#editor-confirm-cancel").click()
        page.wait_for_function("() => window.__openfactory.editor.confirm === null")
        assert page.evaluate("() => window.__openfactoryEditorData('REM_02')") is not None

        ask_delete(page, "REM_02")
        page.locator("#editor-confirm-delete").click()
        page.wait_for_function("() => window.__openfactory.editor.deletes === 1")
        assert page.evaluate("() => window.__openfactoryEditorData('REM_02')") is None
        assert page.evaluate("() => window.__openfactory.editor.errors") == 0
        page.locator("#editor-save").click()
        page.wait_for_function("() => window.__openfactory.editor.saves === 1")
    saved = scene.read_text(encoding="utf-8")
    assert saved != before
    data = yaml.safe_load(saved)
    assert "REM_02" not in str(data)
    assert list(data["checks"]["parts_reached"]) == ["REM_01"]
    load_scene(scene)


def test_references_left_to_fix_are_listed(tmp_path):
    for f in (REPO / "scenes").glob("batch-mix-heat*"):
        shutil.copy(f, tmp_path / f.name)
    scene = tmp_path / "batch-mix-heat.yaml"
    with editor(scene) as page:
        confirm = ask_delete(page, "TK_01")
        assert "check never_overflow: TK_01" in confirm["remove"]
        assert any(".tank names TK_01" in f for f in confirm["fix"]), confirm
        assert page.locator("#editor-confirm-fix li").count() == len(confirm["fix"])
        page.locator("#editor-confirm-delete").click()
        page.wait_for_function("() => window.__openfactory.editor.deletes === 1")
        # the preview's load error names a reference left to fix; Save stays refused
        findings = page.evaluate("() => window.__openfactory.editor.findings")
        assert any(f.startswith("E100") and "TK_01" in f for f in findings), findings
        assert page.locator("#editor-save").is_disabled()
