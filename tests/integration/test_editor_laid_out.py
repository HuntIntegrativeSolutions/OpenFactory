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


@contextlib.contextmanager
def editing(scene: Path):  # noqa: ANN201
    """The editor on ``scene``, and a page on it."""
    from playwright.sync_api import sync_playwright

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
            page.wait_for_function(f"() => window.__openfactory.editor?.file === '{scene.name}'")
            yield page
            browser.close()
    finally:
        proc.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(10)
        if proc.poll() is None:
            proc.kill()


def select(page, cid: str) -> None:  # noqa: ANN001
    page.evaluate(f"() => window.__openfactoryEditorSelect('{cid}')")
    page.wait_for_function(f"() => window.__openfactory.editor.selected === '{cid}'")


def test_dragging_a_belt_carries_its_laid_out_devices_with_their_marks(tmp_path):
    """Ruled 2026-11-20, ruling 2: a laid-out device mounted on a belt moves with its belt in
    the editor, as it does in `openfactory layout`: the same rail, the same distance from the
    infeed, its mark kept; the belt the person moved loses its own mark."""
    import math

    scene = laid_out_scene(tmp_path)
    before = scene.read_text(encoding="utf-8")
    cv0 = yaml.safe_load(before)
    comps0 = {c["id"]: c for c in cv0["components"]}
    with editing(scene) as page:
        select(page, "CV_201")
        g = page.evaluate("() => window.__openfactoryGizmo()")
        unit = math.dist(g["origin"], g["xArrow"]) / 0.6  # pixels per metre along x
        dx, dy = g["xArrow"][0] - g["origin"][0], g["xArrow"][1] - g["origin"][1]
        norm = math.hypot(dx, dy)
        start = g["xArrow"]
        page.mouse.move(*start)
        page.mouse.down()
        for i in range(1, 21):  # +1.0 m along x
            f = 1.0 * unit * i / 20 / norm
            page.mouse.move(start[0] + dx * f, start[1] + dy * f)
        page.mouse.up()
        page.wait_for_function(
            "() => (window.__openfactory.editor.carried?.ids ?? []).includes('PE_201')"
        )
        page.screenshot(path=str(tmp_path / "editor-carried.png"))
        page.locator("#editor-save").click()
        page.wait_for_function("() => window.__openfactory.editor.saves === 1")
    after = scene.read_text(encoding="utf-8")
    comps = {c["id"]: c for c in yaml.safe_load(after)["components"]}
    shift = comps["CV_201"]["position"][0] - comps0["CV_201"]["position"][0]
    assert shift > 0.5 and not comps["CV_201"].get("laid_out")  # the person moved the belt
    for cid in ("PE_201", "CYL_201"):
        old, new = comps0[cid]["position"], comps[cid]["position"]
        assert new[0] == pytest.approx(old[0] + shift, abs=1e-3), cid  # same distance along
        assert new[1:] == pytest.approx(old[1:], abs=1e-6), cid  # same rail, same height
        assert comps[cid]["laid_out"] is True, cid
    assert "LAID OUT" in block(after, "PE_201")


def test_a_move_undone_before_saving_keeps_the_mark(tmp_path):
    """Review of 2026-11-20, gap 2d: a marked item moved and moved back (typed back to its own
    value) before Save keeps its mark; nothing of it changes."""
    scene = laid_out_scene(tmp_path)
    before = scene.read_text(encoding="utf-8")
    with editing(scene) as page:
        select(page, "TK_101")
        x = page.locator('[data-field="position"] input').nth(0)
        was = x.input_value()
        x.fill("20")
        x.press("Tab")
        page.wait_for_function("() => window.__openfactory.editor.ops === 2")  # + the clear
        x = page.locator('[data-field="position"] input').nth(0)
        x.fill(was)
        x.press("Tab")
        page.wait_for_function("() => window.__openfactory.editor.ops === 0")
        assert page.locator('[data-field="laid_out"] input').is_checked()
        select(page, "LT_101")  # another change, so there is something to save
        d = page.locator('[data-field="description"] input').first
        d.fill("Wash Water Tank Level Transmitter, Checked")
        d.press("Tab")
        page.wait_for_function("() => window.__openfactory.editor.ops === 1")
        page.locator("#editor-save").click()
        page.wait_for_function("() => window.__openfactory.editor.saves === 1")
    after = scene.read_text(encoding="utf-8")
    assert block(after, "TK_101") == block(before, "TK_101")  # the mark and its comment stay
    assert "laid_out: true" in block(after, "TK_101")


def test_retyping_a_laid_out_position_clears_the_mark(tmp_path):
    from playwright.sync_api import sync_playwright

    scene = laid_out_scene(tmp_path)
    before = scene.read_text(encoding="utf-8")
    assert "laid_out: true" in block(before, "TK_101")
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
    text = scene.read_text(encoding="utf-8")
    after = block(text, "TK_101")
    assert "position: [20" in after and "laid_out" not in after
    # ruled 2026-11-20, ruling 3: the LAID OUT comment went with the mark, and no other byte
    assert "LAID OUT" not in after and "LAID OUT" in block(before, "TK_101")
    import difflib

    lines = [ln for ln in difflib.ndiff(before.splitlines(), text.splitlines())
             if ln[:2] in ("- ", "+ ")]  # fmt: skip
    print("\n".join(lines))
    assert len(lines) == 5  # the id line (its comment), the position, the mark: 3 out, 2 in
    assert all("TK_101" in ln or "position:" in ln or "laid_out" in ln for ln in lines)
