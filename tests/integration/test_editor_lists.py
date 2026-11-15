"""Two editor faults that lost what the user typed (PLAN 4.1j, fixed 2026-11-14), in a browser.

1. Renaming a mapping key to one another entry has, or to nothing, was taken: the mapping was
   then written with one entry fewer. Now the key box goes back to its name and says why, and
   nothing is sent.
2. A list item not yet sent (a field still empty) was dropped when another change redrew the
   form. Now it is kept, still marked "Not sent yet", until it is finished and sent."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from test_editor_fit import editor, out_dir, previews

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _viewer_built(prerequisite):
    prerequisite(
        (REPO / "web" / "dist" / "index.html").is_file(), "viewer not built (npm run build)"
    )


def sorter(tmp_path: Path) -> Path:
    scene = tmp_path / "sorting-by-height.yaml"
    shutil.copy(REPO / "scenes" / "sorting-by-height.yaml", scene)
    return scene


def leave(box, value: str) -> None:
    box.fill(value)
    box.press("Tab")


def test_a_mapping_key_rename_to_a_taken_or_empty_key_is_refused(tmp_path):
    scene = sorter(tmp_path)
    before_text = scene.read_text(encoding="utf-8")
    with editor(scene) as page:
        page.evaluate("() => window.__openfactoryEditorSelect(null)")
        reached = page.locator('[data-field="checks.parts_reached"]').first
        key = reached.locator(".editor-map-key").first
        assert key.input_value() == "REM_01"
        for bad, why in (("REM_02", "REM_02 is already a key here"), ("", "a key cannot be empty")):
            leave(reached.locator(".editor-map-key").first, bad)
            problem = reached.locator(".editor-map-problem")
            problem.wait_for(state="visible")
            assert why in problem.inner_text()
            keys = [k.input_value() for k in reached.locator(".editor-map-key").all()]
            assert keys == ["REM_01", "REM_02"], keys  # back to its name, both entries kept
            assert page.evaluate("() => window.__openfactory.editor.ops") == 0  # nothing sent
        page.screenshot(path=str(out_dir(tmp_path) / "editor-fix-rename-refused.png"))
        # a free name is still taken, in place
        before = previews(page)
        leave(reached.locator(".editor-map-key").first, "REM_09")
        page.wait_for_function(f"() => window.__openfactory.editor.previews > {before}")
        data = page.evaluate("() => window.__openfactory.editor.ops")
        assert data == 1
    assert scene.read_text(encoding="utf-8") == before_text  # never saved


def test_a_half_filled_list_item_survives_a_redraw(tmp_path):
    scene = sorter(tmp_path)
    with editor(scene) as page:
        page.evaluate("() => window.__openfactoryEditorSelect('EMT_01')")
        page.wait_for_function("() => window.__openfactory.editor.selected === 'EMT_01'")
        page.locator('[data-field="parts"] .editor-list-add').first.click()
        leave(page.locator('input[data-field="parts.2.name"]'), "medium")
        assert page.evaluate("() => window.__openfactory.editor.ops") == 0  # not sent: unfinished
        # another change, previewed: the form is redrawn from the file
        before = previews(page)
        leave(page.locator('[data-field="description"] input'), "Box Source — Three Heights")
        page.wait_for_function(f"() => window.__openfactory.editor.previews > {before}")
        name = page.locator('input[data-field="parts.2.name"]')
        assert name.count() == 1, "the half-filled item was dropped by the redraw"
        assert name.input_value() == "medium"
        error = page.locator('[data-field="parts"] .editor-error').first.inner_text()
        assert error.startswith("Not sent yet: item 3"), error
        page.screenshot(path=str(out_dir(tmp_path) / "editor-fix-draft-kept.png"))
        # finished, it is sent, and saved with the rest
        for n, v in enumerate(("0.3", "0.3", "0.3")):
            leave(page.locator('[data-field="parts.2.size"] input').nth(n), v)
        before = previews(page)
        leave(page.locator('[data-field="parts.2.weight"] input'), "0.2")
        page.wait_for_function(f"() => window.__openfactory.editor.previews > {before}")
        page.locator("#editor-save").click()
        page.wait_for_function("() => window.__openfactory.editor.saves === 1")
    saved = yaml.safe_load(scene.read_text(encoding="utf-8"))
    emitter = next(c for c in saved["components"] if c["id"] == "EMT_01")
    assert [p["name"] for p in emitter["parts"]] == ["tall", "short", "medium"]
    assert emitter["parts"][2] == {"name": "medium", "size": [0.3, 0.3, 0.3], "weight": 0.2}
    assert emitter["description"] == "Box Source — Three Heights"
