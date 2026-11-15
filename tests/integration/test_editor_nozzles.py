"""PLAN 4.1g2 Done-when, the form: a tank's `nozzles` entry is a number or a {height,
bearing_deg} group, picked in the form; it never falls back to a line of YAML. On a copy of
tank-level-pid, TK_01's bottom nozzle is given a bearing of 90 degrees in the editor, saved, and
the saved scene draws PIPE_02's end on the tank's -z wall."""

from __future__ import annotations

import math
import shutil
from pathlib import Path

import pytest
import yaml
from test_editor_fit import editor, out_dir, previews

from openfactory.core.loader import build
from openfactory.core.schema import load_scene

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _viewer_built(prerequisite):
    prerequisite(
        (REPO / "web" / "dist" / "index.html").is_file(), "viewer not built (npm run build)"
    )


def leave(box, value: str) -> None:
    box.fill(value)
    box.press("Tab")


def test_a_nozzle_gets_a_bearing_in_the_form_and_its_pipe_follows(tmp_path):
    scene = tmp_path / "tank-level-pid.yaml"
    for f in (REPO / "scenes").glob("tank-level-pid*"):
        shutil.copy(f, tmp_path / f.name)
    with editor(scene) as page:
        page.evaluate(
            "() => { window.__yamlTyped = 0; document.addEventListener('input', (e) => {"
            " if (e.target instanceof HTMLTextAreaElement) window.__yamlTyped += 1; }, true); }"
        )
        page.evaluate("() => window.__openfactoryEditorSelect('TK_01')")
        page.wait_for_function("() => window.__openfactory.editor.selected === 'TK_01'")
        nozzles = page.locator('[data-field="nozzles"]').first
        assert nozzles.locator("textarea").count() == 0  # a mapping control, not YAML
        nozzles.locator(".editor-map-new").fill("bottom")
        nozzles.locator(".editor-map-add").click()
        pick = page.locator('[data-field="nozzles.bottom"] .editor-either').first
        options = pick.locator("option").all_inner_texts()
        assert options == ["a number", "height and bearing_deg"], options
        pick.select_option(label="height and bearing_deg")
        leave(page.locator('input[data-field="nozzles.bottom.height"]'), "0")
        assert page.evaluate("() => window.__openfactory.editor.ops") == 0  # bearing still empty
        before = previews(page)
        leave(page.locator('input[data-field="nozzles.bottom.bearing_deg"]'), "90")
        page.wait_for_function(f"() => window.__openfactory.editor.previews > {before}")
        assert page.evaluate("() => window.__openfactory.editor.errors") == 0
        page.locator('[data-field="nozzles"]').first.scroll_into_view_if_needed()
        page.screenshot(path=str(out_dir(tmp_path) / "editor-4.1g2-nozzle.png"))
        assert page.evaluate("() => window.__yamlTyped") == 0
        page.locator("#editor-save").click()
        page.wait_for_function("() => window.__openfactory.editor.saves === 1")
    tank = next(
        c
        for c in yaml.safe_load(scene.read_text(encoding="utf-8"))["components"]
        if c["id"] == "TK_01"
    )
    assert tank["nozzles"] == {"bottom": {"height": 0, "bearing_deg": 90}}
    pipe = next(
        c for c in build(load_scene(scene), base_dir=tmp_path).components if c.id == "PIPE_02"
    )
    assert math.dist(pipe.points[0], (0.0, 1.55, -0.3)) < 0.001
