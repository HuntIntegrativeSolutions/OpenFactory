"""PLAN 4.1e Done-when: Add places a new component clear of the plant. On the sorter, with the
view centred on CONV_01's belt, every palette type is added in turn: none gives an overlap
finding (W205) on the preview, and each one drawn is clear of every other component's drawn
footprint. A new conveyor dropped near an open end snaps to it with the 4.1d ports (owner,
2026-11-13): ends meeting, the connection added, no W205, W212 or W213."""

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

OVERLAP = ("W205", "W212", "W213")

LINE = """\
# PLAN 4.1e test scene (tests only): two belts joined end to end; CONV_02's discharge is open.
# Training and demo use only. Not a safety-rated simulation.
openfactory: "0.1"
scene:
  name: add-two-belts
  description: Two joined belts with an open end to drop a third on.
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
    position: [2.0, 0, 0]
    length: 2.0
    width: 0.5
    speed_mps: 0.5
    io: {run: CONV_02_RUN_DO}
connections:
  - {from: CONV_01, to: CONV_02}
"""


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


def add(page, type_name: str) -> dict:
    """Add one ``type_name`` from the palette; the editor's record of where it went."""
    before = page.evaluate("() => window.__openfactory.editor.placed?.id ?? null")
    page.locator("#editor-palette").select_option(type_name)
    page.locator("#editor-add").click()
    page.wait_for_function(
        "(b) => { const p = window.__openfactory.editor.placed; return p && p.id !== b; }",
        arg=before,
    )
    return page.evaluate("() => window.__openfactory.editor.placed")


def overlaps(a: dict, b: dict) -> bool:
    eps = 1e-6
    return (
        a["minX"] < b["maxX"] - eps
        and b["minX"] < a["maxX"] - eps
        and a["minZ"] < b["maxZ"] - eps
        and b["minZ"] < a["maxZ"] - eps
    )


def test_every_palette_type_lands_clear_of_the_plant(tmp_path):
    for f in (REPO / "scenes").glob("sorting-by-height*"):
        shutil.copy(f, tmp_path / f.name)
    shutil.copytree(REPO / "scenes" / "_library", tmp_path / "_library")
    scene = tmp_path / "sorting-by-height.yaml"
    out = Path(os.environ.get("OPENFACTORY_EDITOR_OUT", tmp_path))
    with editor(scene) as page:
        drop = page.evaluate("() => window.__openfactoryEditorLook(1.0, 0.0)")
        conv = page.evaluate("() => window.__openfactoryEditorFootprints()")["CONV_01"]
        # the drop point is on CONV_01's belt: anything left there would overlap it
        assert conv["minX"] < drop[0] < conv["maxX"] and conv["minZ"] < drop[2] < conv["maxZ"]
        types = page.evaluate("() => window.__openfactory.editor.palette")
        assert len(types) >= 25, types
        placed_any = 0
        unloadable = []
        for type_name in types:
            placed = add(page, type_name)
            codes = page.evaluate("() => window.__openfactory.editor.findings")
            assert not [c for c in codes if c.split(" ")[0] in OVERLAP], (type_name, codes)
            prints = page.evaluate("() => window.__openfactoryEditorFootprints()")
            if placed["id"] in prints:
                mine = prints.pop(placed["id"])
                hits = [i for i, r in prints.items() if overlaps(mine, r)]
                assert not hits, (type_name, placed, hits)
                assert placed["moved_m"] > 0 or not overlaps(mine, conv), placed
                placed_any += 1
                if type_name == "pusher":
                    page.screenshot(path=str(out / "editor-4.1e-added.png"))
            else:  # a type that needs a reference filled in first does not load: nothing drawn
                assert placed["waiting"], placed  # placed when it is first drawn
                assert page.evaluate("() => window.__openfactory.editor.errors") > 0, type_name
                unloadable.append(type_name)
            previews = page.evaluate("() => window.__openfactory.editor.previews")
            page.locator("#editor-revert").click()
            page.wait_for_function(f"() => window.__openfactory.editor.previews > {previews}")
        # the process types need a pipe, a tank or a junction the sorter does not have
        assert placed_any + len(unloadable) == len(types)
        assert placed_any >= 20, unloadable
        assert "pipe" in unloadable  # needs its from and to before it can be drawn


def test_a_conveyor_dropped_near_an_open_end_snaps_to_it(tmp_path):
    scene = tmp_path / "add-two-belts.yaml"
    scene.write_text(LINE, encoding="utf-8")
    out = Path(os.environ.get("OPENFACTORY_EDITOR_OUT", tmp_path))
    with editor(scene) as page:
        page.evaluate("() => window.__openfactoryEditorLook(4.6, 0.3)")  # 0.67 m past CONV_02
        placed = add(page, "belt_conveyor")
        assert placed["snapped"] == "CONV_03.infeed -> CONV_02.discharge", placed
        data = page.evaluate("() => window.__openfactoryEditorData('CONV_03')")
        assert data["position"] == pytest.approx([4.0, 0.0, 0.0], abs=1e-3)
        assert float(data.get("rotation_deg", 0)) == pytest.approx(0.0, abs=0.1)
        codes = page.evaluate("() => window.__openfactory.editor.findings")
        # no W212: the ends meet and the connection CONV_02 -> CONV_03 joins them
        assert not [c for c in codes if c.split(" ")[0] in OVERLAP], codes
        page.screenshot(path=str(out / "editor-4.1e-snapped.png"))
    assert "CONV_03" not in scene.read_text(encoding="utf-8")
