"""PLAN 4.1g1 Done-when (ruled 2026-11-14): a photo eye, a pusher, a stopper and a diverter,
each dragged by real mouse moves on the gizmo from beside a belt to its rail, land by the drop
rules (positions within 1 mm, headings within 0.1 degrees, the diverter's swing over the belt).
The editor chooses only the rail and the distance along it; the placement comes from the server,
so the saved file is compared here with core/mounts.py's place_on_rail directly: the editor and
the function agree. The preview has no W214, W206 or W207."""

from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml
from test_editor_fit import editor, out_dir

from openfactory.core.mounts import place_on_rail
from openfactory.core.schema import load_scene
from openfactory.core.sizing import tallest_part

REPO = Path(__file__).resolve().parents[2]

# a belt turned 90 degrees (flowing along -z, its left rail at x -0.25): each device stands at
# x -0.8, beside it, and is dragged along +x onto the rail
SCENE = """\
# PLAN 4.1g1 test scene (tests only). Training and demo use only. Not a safety-rated simulation.
openfactory: "0.1"
scene:
  name: mount-four
  description: Four devices beside one belt, to drop on its rail.
plc:
  protocol: modbus_tcp
  role: server
components:
  - id: CONV_01
    type: belt_conveyor
    description: Mount Test Belt Conveyor
    position: [0, 0, 0]
    rotation_deg: 90
    length: 4.0
    width: 0.5
    speed_mps: 0.5
    io: {run: CONV_01_RUN_DO}
  - id: EMT_01
    type: box_emitter
    description: Mount Test Box Source
    position: [0, 0.3, -0.2]
    interval_s: 3
    parts: [{name: tall, size: [0.3, 0.4, 0.3], weight: 1}]
  - id: PE_01
    type: photo_eye
    description: Mount Test Photo Eye
    position: [-0.8, 0.1, -0.5]
    beam_length: 0.6
    io: {detect: PE_01_DI}
  - id: CYL_01
    type: pusher
    description: Mount Test Pusher
    position: [-0.8, 0.3, -1.5]
    stroke: 0.6
    extend_time_s: 0.5
    io: {extend: CYL_01_EXTEND_DO}
  - id: STP_01
    type: stopper
    description: Mount Test Stopper
    position: [-0.8, 0, -2.5]
    io: {raise: STP_01_RAISE_DO}
  - id: DIV_01
    type: diverter_arm
    description: Mount Test Diverter Arm
    position: [-0.8, 0, -3.3]
    length: 0.78
    swing_deg: -45
    io: {extend: DIV_01_EXTEND_DO}
"""
DEVICES = {"PE_01": 0.5, "CYL_01": 1.5, "STP_01": 2.5, "DIV_01": 3.3}  # along the rail, m


@pytest.fixture(autouse=True)
def _viewer_built(prerequisite):
    prerequisite(
        (REPO / "web" / "dist" / "index.html").is_file(), "viewer not built (npm run build)"
    )


def drag_onto_rail(page, comp_id: str, shot: Path | None = None) -> dict:
    page.evaluate(f"() => window.__openfactoryEditorSelect('{comp_id}')")
    page.wait_for_function(f"() => window.__openfactory.editor.selected === '{comp_id}'")
    g = page.evaluate("() => window.__openfactoryGizmo()")
    unit = math.dist(g["origin"], g["xArrow"]) / 0.6  # screen pixels per metre along x
    dx, dy = g["xArrow"][0] - g["origin"][0], g["xArrow"][1] - g["origin"][1]
    norm = math.hypot(dx, dy)
    start = g["xArrow"]
    page.mouse.move(*start)
    page.mouse.down()
    for i in range(1, 21):  # +0.5 m along x: the origin to x -0.3, 5 cm outside the rail
        f = 0.5 * unit * i / 20 / norm
        page.mouse.move(start[0] + dx * f, start[1] + dy * f)
    page.wait_for_function("() => window.__openfactory.editor.mounting !== null")
    if shot:
        page.screenshot(path=str(shot))  # the rail marked while dragging
    before = page.evaluate("() => window.__openfactory.editor.previews")
    page.mouse.up()
    page.wait_for_function(
        f"() => window.__openfactory.editor.mount?.id === '{comp_id}'"
        f" && window.__openfactory.editor.previews > {before}"
    )
    return page.evaluate("() => window.__openfactory.editor.mount")


def test_four_devices_dropped_on_a_rail_land_by_the_python_rules(tmp_path):
    scene = tmp_path / "mount-four.yaml"
    scene.write_text(SCENE, encoding="utf-8")
    drops = {}
    with editor(scene) as page:
        page.locator("#editor-fit").click()
        page.wait_for_timeout(300)
        for comp_id in DEVICES:
            shot = out_dir(tmp_path) / "editor-4.1g1-marked.png" if comp_id == "CYL_01" else None
            drops[comp_id] = drag_onto_rail(page, comp_id, shot)
        findings = page.evaluate("() => window.__openfactory.editor.findings")
        assert not [f for f in findings if f[:4] in ("W214", "W206", "W207")], findings
        assert page.evaluate("() => window.__openfactory.editor.errors") == 0
        page.evaluate("() => window.__openfactoryEditorSelect(null)")
        page.screenshot(path=str(out_dir(tmp_path) / "editor-4.1g1-dropped.png"))
        page.locator("#editor-save").click()
        page.wait_for_function("() => window.__openfactory.editor.saves === 1")
    saved = {c["id"]: c for c in yaml.safe_load(scene.read_text(encoding="utf-8"))["components"]}
    original = {c["id"]: c for c in yaml.safe_load(SCENE)["components"]}
    loaded = load_scene(scene)
    belt = next(c for c in loaded.components if c.id == "CONV_01")
    for comp_id, along in DEVICES.items():
        drop = drops[comp_id]
        assert (drop["conveyor"], drop["rail"]) == ("CONV_01", "rail_left"), drop
        assert drop["along"] == pytest.approx(along, abs=0.05), drop  # on the 0.05 m grid
        raw = original[comp_id]
        want = place_on_rail(
            raw["type"], raw, belt, "rail_left", drop["along"], tallest_part(loaded)
        )
        got = saved[comp_id]
        assert got["position"] == pytest.approx(list(want.position), abs=0.001), comp_id
        assert got["rotation_deg"] == pytest.approx(want.rotation_deg, abs=0.1), comp_id
    # the rules by hand, as the proposal writes them (belt turned 90: local (x, y, z) is world
    # (z, y, -x)): the eye over the centre line, the pusher 0.05 m outside the rail with its
    # plate from 0.05 m to 3/4 of the 0.4 m box, the stopper on the centre line, the diverter's
    # pivot on the rail swinging over the belt (its -45 flipped to +45 on the left rail)
    a = {k: drops[k]["along"] for k in DEVICES}
    assert saved["PE_01"]["position"] == pytest.approx([0, 0.1, -a["PE_01"]], abs=0.001)
    assert saved["CYL_01"]["position"] == pytest.approx([-0.3, 0.175, -a["CYL_01"]], abs=0.001)
    assert saved["STP_01"]["position"] == pytest.approx([0, 0, -a["STP_01"]], abs=0.001)
    assert saved["DIV_01"]["position"] == pytest.approx([-0.25, 0, -a["DIV_01"]], abs=0.001)
    assert {saved[k]["rotation_deg"] for k in DEVICES} == {90}
    assert saved["DIV_01"]["swing_deg"] == 45
