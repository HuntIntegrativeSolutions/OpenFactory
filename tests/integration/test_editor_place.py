"""PLAN 4.1c: placing components in the scene editor. `openfactory edit` on a copy of the sorter
with the library composite and a mesh beside it: add a photo eye and a library composite
instance from the palette, describe them, move and turn each with the gizmo (real mouse drags),
set a mesh, save; the file validates and the lint shows no new error. Also: a new component
cannot be saved before it has its own description, deleting one that is not saved takes it back,
and Download YAML gives the saved text."""

from __future__ import annotations

import contextlib
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from openfactory.lint import lint_scene

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


def drag(page, start, direction, pixels):
    """Press at ``start`` and move ``pixels`` along the screen ``direction``, like a user."""
    dx, dy = direction
    norm = (dx * dx + dy * dy) ** 0.5
    page.mouse.move(*start)
    page.mouse.down()
    for i in range(1, 11):
        f = pixels * i / 10 / norm
        page.mouse.move(start[0] + dx * f, start[1] + dy * f)
    page.mouse.up()


def add_and_describe(page, type_name, comp_id, description):
    before = page.evaluate("() => window.__openfactory.editor.previews")
    page.locator("#editor-palette").select_option(type_name)
    page.locator("#editor-add").click()
    page.wait_for_function(f"() => window.__openfactory.editor.selected === '{comp_id}'")
    page.wait_for_function(f"() => window.__openfactory.editor.previews > {before}")
    assert comp_id in page.evaluate("() => window.__openfactory.editor.undescribed")
    assert page.locator("#editor-save").is_disabled()  # not without its own description
    assert page.locator("#editor-describe").is_visible()
    desc = page.locator('[data-field="description"] input')
    desc.fill(description)
    desc.press("Tab")
    page.wait_for_function(f"() => !window.__openfactory.editor.undescribed.includes('{comp_id}')")


def move_and_turn(page, comp_id):
    data = f"() => window.__openfactoryEditorData('{comp_id}')"
    start = page.evaluate(data)
    page.locator("#editor-mode-move").click()
    g = page.evaluate("() => window.__openfactoryGizmo()")
    previews = page.evaluate("() => window.__openfactory.editor.previews")
    direction = (g["xArrow"][0] - g["origin"][0], g["xArrow"][1] - g["origin"][1])
    drag(page, g["xArrow"], direction, 90)
    page.wait_for_function(f"() => window.__openfactory.editor.previews > {previews}")
    moved = page.evaluate(data)
    assert moved["position"][0] > start["position"][0] + 0.1, (start, moved)
    assert moved["position"][1] == start["position"][1]  # on the floor plane only
    assert round(moved["position"][0] / 0.05, 6) % 1 == 0  # on the 0.05 m grid
    assert page.evaluate("() => window.__openfactory.editor.selected") == comp_id

    page.locator("#editor-mode-turn").click()
    g = page.evaluate("() => window.__openfactoryGizmo()")
    previews = page.evaluate("() => window.__openfactory.editor.previews")
    # grab the vertical-axis ring and drag across it, along the x arrow's screen direction
    tangent = (g["xArrow"][0] - g["origin"][0], g["xArrow"][1] - g["origin"][1])
    drag(page, g["ring"], tangent, 120)
    page.wait_for_function(f"() => window.__openfactory.editor.previews > {previews}")
    turned = page.evaluate(data)
    rot = turned["rotation_deg"]
    assert rot != 0 and abs(rot / 15 - round(rot / 15)) < 1e-6, turned  # the 15 degree grid
    return turned


def test_add_move_turn_mesh_and_save_a_photo_eye_and_a_library_composite(tmp_path):
    from playwright.sync_api import sync_playwright

    for f in (REPO / "scenes").glob("sorting-by-height*"):
        shutil.copy(f, tmp_path / f.name)
    (tmp_path / "_library").mkdir()
    shutil.copy(REPO / "scenes" / "_library" / "accumulation_conveyor.yaml", tmp_path / "_library")
    (tmp_path / "models").mkdir()
    shutil.copy(REPO / "tests" / "scenes" / "models" / "pusher-housing.glb", tmp_path / "models")
    scene = tmp_path / "sorting-by-height.yaml"
    errors_before = [f for f in lint_scene(scene) if f.level == "error"]
    locks = {p: p.read_bytes() for p in tmp_path.glob("*.modbus.yaml")}
    web = screenshot.free_port()
    cmd = [sys.executable, "-m", "openfactory", "edit", str(scene), "-q", "--web-port", str(web)]
    proc = subprocess.Popen(cmd, cwd=REPO, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        screenshot.wait_for_api(web, proc, timeout=60)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=screenshot.CHROMIUM_ARGS)
            page = browser.new_page(viewport={"width": 1280, "height": 720}, accept_downloads=True)
            page.goto(f"http://127.0.0.1:{web}/")
            page.wait_for_function("() => window.__openfactory && window.__openfactory.ready")
            page.wait_for_function("() => window.__openfactory.editor?.palette.length > 0")
            palette = page.evaluate("() => window.__openfactory.editor.palette")
            assert "photo_eye" in palette and "accumulation_conveyor" in palette

            # a new pushbutton, then deleted before saving: nothing of it is left to send
            page.locator("#editor-palette").select_option("pushbutton")
            page.locator("#editor-add").click()
            page.wait_for_function("() => window.__openfactory.editor.selected === 'PB_01'")
            page.locator("#editor-delete").click()
            page.wait_for_function("() => window.__openfactory.editor.selected === null")
            assert page.evaluate("() => window.__openfactory.editor.ops") == 0

            add_and_describe(page, "photo_eye", "PE_02", "Reject Lane Photo Eye")
            eye = move_and_turn(page, "PE_02")

            add_and_describe(page, "accumulation_conveyor", "ACC_01", "Reject Lane Buffer Conveyor")
            acc = move_and_turn(page, "ACC_01")
            # the pusher gets the housing beside the scene
            page.evaluate("() => window.__openfactoryEditorSelect('CYL_01')")
            page.wait_for_function("() => window.__openfactory.editor.selected === 'CYL_01'")
            page.locator("#editor-mesh").select_option("models/pusher-housing.glb")
            page.wait_for_function("() => window.__openfactory.meshes?.loaded >= 1")
            assert page.evaluate("() => window.__openfactory.meshes.failed") == 0
            assert page.evaluate("() => window.__openfactory.editor.errors") == 0
            page.screenshot(path=str(REPO / "tools" / "screenshots" / "editor-4.1c.png"))

            # a binding written as a mapping is edited as YAML (read-only in 4.1b)
            page.evaluate("() => window.__openfactoryEditorSelect('CONV_01')")
            binding = page.locator('[data-field="io.speed"] textarea')
            assert binding.input_value() == "{tag: CONV_01_SPD_AO, initial: 100}"
            previews = page.evaluate("() => window.__openfactory.editor.previews")
            binding.fill("{tag: CONV_01_SPD_AO, initial: 80}")
            binding.press("Tab")
            page.wait_for_function(f"() => window.__openfactory.editor.previews > {previews}")
            assert page.evaluate("() => window.__openfactory.editor.errors") == 0

            page.locator("#editor-save").click()
            page.wait_for_function("() => window.__openfactory.editor.saves === 1")
            with page.expect_download() as download:
                page.locator("#editor-download").click()
            downloaded = Path(download.value.path()).read_text(encoding="utf-8")
            browser.close()
    finally:
        proc.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(10)
        if proc.poll() is None:
            proc.kill()

    text = scene.read_text(encoding="utf-8")
    assert downloaded == text
    data = yaml.safe_load(text)
    comps = {c["id"]: c for c in data["components"]}
    assert {"library": "accumulation_conveyor"} in data["composites"]
    assert comps["PE_02"]["description"] == "Reject Lane Photo Eye"
    assert comps["PE_02"]["position"] == eye["position"]
    assert comps["PE_02"]["rotation_deg"] == eye["rotation_deg"]
    assert comps["ACC_01"]["position"] == acc["position"]
    assert comps["ACC_01"]["io"]["release"] == "ACC_01_RELEASE_DO"
    assert comps["CYL_01"]["mesh"] == {"file": "models/pusher-housing.glb"}
    assert "PB_01" not in comps
    assert comps["CONV_01"]["io"]["speed"] == {"tag": "CONV_01_SPD_AO", "initial": 80}
    assert "speed: {tag: CONV_01_SPD_AO, initial: 80}  # optional" in text  # comment kept
    valid = subprocess.run(
        [sys.executable, "-m", "openfactory", "validate", str(scene)], cwd=REPO, check=False
    )
    assert valid.returncode == 0
    errors_after = [f for f in lint_scene(scene) if f.level == "error"]
    assert errors_after == errors_before == []
    assert {p: p.read_bytes() for p in locks} == locks  # the lockfiles are never written
