"""A shipped scene built only with the editor (owner, 2026-11-13): `openfactory new`, then the
editor in a browser, without the file ever being opened. This test does what a user does, click
by click and field by field, and checks the saved file holds exactly what the shipped
scenes/inline-reject.yaml holds (the same values; the shipped file is this session's output).

PLAN 4.1j: no value is typed as YAML. The emitter's parts and the parts_reached check are built
with the list and mapping controls (Add item, a key and Add), and the test fails if any YAML
text box is used; at the end Fit view frames the whole plant.

Set OPENFACTORY_BUILD_SCENE=<path> to write the saved file there (how the shipped one was made).
"""

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

SHIPPED = REPO / "scenes" / "inline-reject.yaml"


@pytest.fixture(autouse=True)
def _viewer_built(prerequisite):
    prerequisite(
        (REPO / "web" / "dist" / "index.html").is_file(), "viewer not built (npm run build)"
    )


class User:
    """What a user does in the editor panel, waiting for each preview like a person would."""

    def __init__(self, page):
        self.page = page

    def previews(self) -> int:
        return self.page.evaluate("() => window.__openfactory.editor.previews")

    def _after(self, before: int) -> None:
        self.page.wait_for_function(f"() => window.__openfactory.editor.previews > {before}")

    def select(self, comp_id: str | None) -> None:
        arg = "null" if comp_id is None else f"'{comp_id}'"
        self.page.evaluate(f"() => window.__openfactoryEditorSelect({arg})")
        want = "null" if comp_id is None else f"'{comp_id}'"
        self.page.wait_for_function(f"() => window.__openfactory.editor.selected === {want}")

    def box(self, field: str, nth: int = 0):
        """The input of a field: in its row, or the input itself (an item in a list)."""
        sel = f'[data-field="{field}"] input, input[data-field="{field}"]'
        return self.page.locator(sel).nth(nth)

    def type(self, field: str, value: str, nth: int = 0, preview: bool = True) -> None:
        """Type a value and leave the field; wait for the preview it sends unless the value is
        not complete yet (an item in a list whose other fields are still empty)."""
        box = self.box(field, nth)
        current = box.input_value()
        with contextlib.suppress(ValueError):
            if current == value or float(current) == float(value):
                return  # already so: the panel says it was kept, and sends nothing
        before = self.previews()
        box.fill(value)
        box.press("Tab")  # a field commits when it loses focus
        if preview:
            self._after(before)

    def add_item(self, field: str) -> None:
        """Press a list's Add item: a new empty item, sent once every field of it is filled."""
        self.page.locator(f'[data-field="{field}"] .editor-list-add').first.click()

    def add_entry(self, field: str, key: str) -> None:
        """Type a mapping's new key and press its Add: a new entry with its value empty."""
        row = self.page.locator(f'[data-field="{field}"]').first
        row.locator(".editor-map-new").last.fill(key)
        row.locator(".editor-map-add").last.click()

    def position(self, xyz: tuple[float, float, float]) -> None:
        for i, v in enumerate(xyz):
            self.type("position", repr(v), nth=i)

    def use_tag(self, field: str, expect: str) -> None:
        """Bind an optional port to the tag the editor offers (its "Use TAG" button)."""
        button = self.page.locator(f'[data-field="{field}"] .editor-use-tag')
        assert button.inner_text() == f"Use {expect}"
        before = self.previews()
        button.click()
        self._after(before)

    def tick(self, field: str) -> None:
        before = self.previews()
        self.page.locator(f'[data-field="{field}"] input').check()
        self._after(before)

    def add(self, type_name: str, at: tuple[float, float]) -> str:
        """Centre the view on a floor point, pick the type in the palette, press Add."""
        self.page.evaluate(f"() => window.__openfactoryEditorLook({at[0]}, {at[1]})")
        before = self.page.evaluate("() => window.__openfactory.editor.placed?.id ?? null")
        self.page.locator("#editor-palette").select_option(type_name)
        self.page.locator("#editor-add").click()
        self.page.wait_for_function(
            "(b) => { const p = window.__openfactory.editor.placed; return p && p.id !== b; }",
            arg=before,
        )
        return self.page.evaluate("() => window.__openfactory.editor.placed.id")


def build(user: User) -> None:
    """The session: two belts snapped end to end, a box source, an eye, a pusher, two bins."""
    # the starter belt from `openfactory new`
    user.select("CONV_01")
    user.type("description", "Infeed Belt Conveyor — Inspection Line")
    user.type("length", "3")
    user.use_tag("io.running", "CONV_01_RUNNING_DI")
    # a second belt dropped past CONV_01's discharge snaps on and is connected
    assert user.add("belt_conveyor", (3.4, 0.2)) == "CONV_02"
    placed = user.page.evaluate("() => window.__openfactory.editor.placed")
    assert placed["snapped"] == "CONV_02.infeed -> CONV_01.discharge", placed
    user.type("description", "Outfeed Belt Conveyor — Reject Station")
    user.type("length", "3")
    user.use_tag("io.running", "CONV_02_RUNNING_DI")
    # the box source at the infeed
    assert user.add("box_emitter", (0.2, 0.0)) == "EMT_01"
    user.type("description", "Infeed Box Source — Good and Oversize")
    user.position((0.2, 0.3, 0.0))
    user.use_tag("io.enable", "EMT_01_DO")
    # its parts list: the starter box becomes the oversize one, and a good one is added
    user.type("parts.0.name", "oversize")
    user.type("parts.0.size", "0.4", nth=1)
    user.type("parts.0.weight", "0.3")
    user.add_item("parts")
    user.type("parts.1.name", "good", preview=False)
    for n, v in enumerate(("0.3", "0.2", "0.3")):
        user.type("parts.1.size", v, nth=n, preview=False)
    user.type("parts.1.weight", "0.7")  # the item is complete: now it is sent
    # the eye across CONV_02, above good boxes and below oversize ones
    assert user.add("photo_eye", (3.5, 0.0)) == "PE_01"
    user.type("description", "Oversize Box Photo Eye")
    user.position((3.5, 0.3, 0.0))
    # the pusher 1.0 m on, beside the belt, pushing across it
    assert user.add("pusher", (4.5, -0.3)) == "CYL_01"
    user.type("description", "Oversize Box Reject Pusher")
    user.position((4.5, 0.175, -0.3))
    user.type("extend_time_s", "0.4")
    user.use_tag("io.extended", "CYL_01_EXTENDED_DI")
    # the reject bin across the belt from the pusher, and the good-box bin at the end
    assert user.add("part_remover", (4.5, 0.6)) == "REM_01"
    user.type("description", "Reject Bin Part Remover")
    user.position((4.5, 0.0, 0.55))
    user.type("size", "0.6", nth=0)
    user.type("size", "1", nth=1)
    assert user.add("part_remover", (6.2, 0.0)) == "REM_02"
    user.type("description", "Good Box Discharge Part Remover")
    user.position((6.15, 0.0, 0.0))
    user.type("size", "0.3", nth=0)
    user.type("size", "1", nth=1)
    user.type("size", "1", nth=2)
    # the scene's own settings and its checks
    user.select(None)
    about = "Inline inspection: oversize boxes pushed off into a reject bin."
    user.type("scene.description", about)
    user.type("scene.author", "HIS Controls")
    user.type("scene.seed", "7")
    user.add_entry("checks.parts_reached", "REM_01")
    user.type("checks.parts_reached.REM_01.0", "oversize")
    user.add_entry("checks.parts_reached", "REM_02")
    user.type("checks.parts_reached.REM_02.0", "good")
    user.tick("checks.none_lost")
    user.type("checks.max_cycle_s", "15")


def test_the_inline_reject_scene_is_built_in_the_editor(tmp_path):
    from playwright.sync_api import sync_playwright

    scene = tmp_path / "inline-reject.yaml"
    subprocess.run(
        [sys.executable, "-m", "openfactory", "new", str(scene), "--no-edit"],
        cwd=REPO, check=True, capture_output=True,
    )  # fmt: skip
    web = screenshot.free_port()
    cmd = [sys.executable, "-m", "openfactory", "edit", str(scene), "-q", "--web-port", str(web)]
    proc = subprocess.Popen(cmd, cwd=REPO, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    out = Path(os.environ.get("OPENFACTORY_EDITOR_OUT", tmp_path))
    try:
        screenshot.wait_for_api(web, proc, timeout=60)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=screenshot.CHROMIUM_ARGS)
            page = browser.new_page(viewport={"width": 1280, "height": 720})
            page.goto(f"http://127.0.0.1:{web}/")
            page.wait_for_function("() => window.__openfactory && window.__openfactory.ready")
            page.wait_for_function("() => (window.__openfactory.editor?.palette ?? []).length > 0")
            assert page.locator("#editor-save").is_disabled()  # placeholders: scene and CONV_01
            user = User(page)
            # PLAN 4.1j: count every YAML text box the user types in; there must be none
            page.evaluate(
                "() => { window.__yamlTyped = 0; document.addEventListener('input', (e) => {"
                " if (e.target instanceof HTMLTextAreaElement) window.__yamlTyped += 1; }, true); }"
            )
            build(user)
            assert page.evaluate("() => window.__yamlTyped") == 0
            assert page.evaluate("() => window.__openfactory.editor.undescribed") == []
            assert page.evaluate("() => window.__openfactory.editor.errors") == 0
            page.locator("#editor-save").click()
            page.wait_for_function("() => window.__openfactory.editor.saves === 1")
            # the lists as the form shows them, then the whole plant framed by Fit view
            user.select("EMT_01")
            page.locator('[data-field="parts"]').first.scroll_into_view_if_needed()
            page.screenshot(path=str(out / "editor-4.1j-list.png"))
            user.select(None)
            page.locator('[data-field="checks.parts_reached"]').first.scroll_into_view_if_needed()
            page.screenshot(path=str(out / "editor-4.1j-mapping.png"))
            page.locator("#editor-fit").click()
            page.wait_for_timeout(300)
            assert page.evaluate("() => window.__openfactoryEditorOutOfView()") == []
            page.screenshot(path=str(out / "editor-build-inline-reject.png"))
            browser.close()
    finally:
        proc.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(10)
        if proc.poll() is None:
            proc.kill()
    load_scene(scene)
    if os.environ.get("OPENFACTORY_BUILD_SCENE"):
        shutil.copy(scene, os.environ["OPENFACTORY_BUILD_SCENE"])
    built = yaml.safe_load(scene.read_text(encoding="utf-8"))
    shipped = yaml.safe_load(SHIPPED.read_text(encoding="utf-8"))
    assert built == shipped
