"""PLAN 4.1g1 (ruled 2026-11-14): a belt's rails, the drop rules (one copy, in
core/mounts.py, ruling 6) and W214."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openfactory.core.loader import build
from openfactory.core.mounts import (
    MOUNT_TYPES,
    place_on_rail,
    pusher_height_above,
    rails,
    short_of_belt,
)
from openfactory.core.schema import load_scene, parse_scene
from openfactory.core.sizing import tallest_part
from openfactory.lint import lint_scene

REPO = Path(__file__).resolve().parents[2]


def by_id(scene) -> dict:
    return {c.id: c for c in scene.components}


def test_a_belt_has_two_rails_facing_in_and_a_deck_has_none():
    sorter = by_id(load_scene(REPO / "scenes" / "sorting-by-height.yaml"))
    left, right = rails(sorter["CONV_01"])
    assert (left.name, left.kind, left.role) == ("rail_left", "mount", "both")
    assert left.position == pytest.approx((0.0, 0.0, -0.25))
    assert right.position == pytest.approx((0.0, 0.0, 0.25))
    assert (left.heading_deg, right.heading_deg) == (-90.0, 90.0)  # each faces across the belt
    assert left.length == right.length == sorter["CONV_01"].length
    merge = by_id(load_scene(REPO / "scenes" / "two-lane-merge.yaml"))
    assert rails(merge["TT_01"]) == []  # ruling 5: no rails on a turntable or lift deck
    assert rails(sorter["PE_01"]) == []


@pytest.mark.parametrize(
    ("scene_name", "pusher", "belt", "along"),
    [("sorting-by-height", "CYL_01", "CONV_01", 2.5), ("inline-reject", "CYL_01", "CONV_02", 1.5)],
)
def test_both_shipped_pushers_are_placed_where_they_are_committed(scene_name, pusher, belt, along):
    """The Done-when's addition (2026-11-14): the Python function places both shipped pushers at
    their committed positions."""
    scene = load_scene(REPO / "scenes" / f"{scene_name}.yaml")
    comps = by_id(scene)
    raw = {"type": "pusher", "position": list(comps[pusher].position)}  # plate_height unset
    placed = place_on_rail("pusher", raw, comps[belt], "rail_left", along, tallest_part(scene))
    assert placed.position == pytest.approx(tuple(comps[pusher].position), abs=1e-9)
    assert placed.position == pytest.approx((comps[pusher].position[0], 0.175, -0.3))
    assert placed.rotation_deg == comps[pusher].rotation_deg == 0


def test_a_pushers_plate_is_sized_by_the_loader_exactly_as_the_rule_expects():
    """Placed at the rule's height, the loader's plate runs from 0.05 m above the belt to 3/4 of
    the tallest part, or is the 0.15 m minimum from 0.05 m with short parts."""
    for tallest, top in ((0.4, 0.3), (0.6, 0.45), (0.2, 0.2), (None, 0.2)):
        y = pusher_height_above(None, tallest)
        parts = (
            f"parts: [{{name: p, size: [0.3, {tallest}, 0.3], weight: 1}}]"
            if tallest
            else "parts: []"
        )
        text = f"""openfactory: "0.1"
scene: {{name: t, description: t}}
plc: {{protocol: modbus_tcp, role: server}}
components:
  - {{id: CONV_01, type: belt_conveyor, description: B, position: [0, 0, 0], length: 3,
      width: 0.5, speed_mps: 0.5, io: {{run: CONV_01_RUN_DO}}}}
  - {{id: CYL_01, type: pusher, description: P, position: [1.5, {y}, -0.3], stroke: 0.6,
      extend_time_s: 0.4, io: {{extend: CYL_01_EXTEND_DO}}}}
"""
        if tallest:
            text += f"""  - {{id: EMT_01, type: box_emitter, description: E,
      position: [0.2, 0.3, 0],
      interval_s: 3, {parts}}}
"""
        loaded = build(parse_scene(text, REPO))
        plate = next(c.config for c in loaded.components if c.id == "CYL_01").plate_height
        assert y - plate / 2 == pytest.approx(0.05), tallest
        assert y + plate / 2 == pytest.approx(top), tallest


def test_each_type_lands_by_its_rule_on_a_turned_belt():
    """A belt turned 90 degrees (flowing along -z, left rail at -x), 0.75 m up."""
    scene = parse_scene(
        """openfactory: "0.1"
scene: {name: t, description: t}
plc: {protocol: modbus_tcp, role: server}
components:
  - {id: CONV_01, type: belt_conveyor, description: B, position: [1, 0.75, 2], rotation_deg: 90,
     length: 4, width: 0.6, speed_mps: 0.5, io: {run: CONV_01_RUN_DO}}
""",
        REPO,
    )
    belt = scene.components[0]
    eye = place_on_rail("photo_eye", {"position": [9, 0.95, 9]}, belt, "rail_left", 1.0)
    assert eye.position == pytest.approx((1.0, 0.95, 1.0))  # over the centre line, 0.2 kept
    assert eye.rotation_deg == 90
    low = place_on_rail("photo_eye", {"position": [9, 0.0, 9]}, belt, "rail_left", 1.0)
    assert low.position[1] == pytest.approx(0.85)  # below the belt: 0.1 m above it
    push = place_on_rail("pusher", {"plate_height": 0.2}, belt, "rail_right", 2.0)
    assert push.position == pytest.approx((1.35, 0.75 + 0.15, 0.0))  # 0.05 outside +x rail
    assert push.rotation_deg == -90  # 90 + 180: extends toward -x, across the belt
    stop = place_on_rail("stopper", {}, belt, "rail_right", 4.5)  # clamped to the end
    assert stop.position == pytest.approx((1.0, 0.75, -2.0)) and stop.rotation_deg == 90
    div = place_on_rail("diverter_arm", {"swing_deg": 30}, belt, "rail_right", 0.5)
    assert div.position == pytest.approx((1.3, 0.75, 1.5)) and div.swing_sign == -1
    assert place_on_rail("diverter_arm", {}, belt, "rail_left", 0.5).swing_sign == 1
    ds = place_on_rail("distance_sensor", {"position": [0, 1.05, 0]}, belt, "rail_left", 1.0)
    assert ds.position == pytest.approx((0.65, 1.05, 1.0)) and ds.rotation_deg == 90
    down = place_on_rail(
        "distance_sensor", {"axis": "-y", "position": [0, 1.35, 0]}, belt, "rail_left", 1.0
    )
    assert down.position == pytest.approx((1.0, 1.35, 1.0))
    assert set(MOUNT_TYPES) == {
        "photo_eye", "distance_sensor", "color_sensor", "pusher", "stopper", "diverter_arm"
    }  # fmt: skip
    with pytest.raises(ValueError, match="not placed on a rail"):
        place_on_rail("tank", {}, belt, "rail_left", 1.0)
    with pytest.raises(ValueError, match="no rail"):
        place_on_rail("stopper", {}, belt, "rail_top", 1.0)


# --- W214 -----------------------------------------------------------------------------------

BELT = """openfactory: "0.1"
scene: {{name: w214, description: W214 case}}
plc: {{protocol: modbus_tcp, role: server}}
components:
  - {{id: CONV_01, type: {belt}, description: Belt, position: [0, 0, 0], length: 3, width: 0.5,
      speed_mps: 0.5, {extra} io: {{run: CONV_01_RUN_DO}}}}
  - {device}
"""


def w214(tmp_path, device: str, belt: str = "belt_conveyor", extra: str = "") -> list[str]:
    path = tmp_path / "w214.yaml"
    path.write_text(BELT.format(belt=belt, device=device, extra=extra), encoding="utf-8")
    return [f.message for f in lint_scene(path) if f.code == "W214"]


def test_w214_an_eye_beam_2_mm_short_of_a_rail(tmp_path):
    eye = (
        "{{id: PE_01, type: photo_eye, description: E, position: [1, 0.1, {z}], "
        "beam_length: {b}, io: {{detect: PE_01_DI}}}}"
    )
    found = w214(tmp_path, eye.format(z=0.0, b=0.496))
    assert (
        len(found) == 1
        and "2 mm short of the left rail and 2 mm short of the right rail" in found[0]
    )
    assert w214(tmp_path, eye.format(z=0.0, b=0.5)) == []  # equal to the belt: on the boundary
    assert w214(tmp_path, eye.format(z=0.0, b=0.499)) == []  # within 1 mm (0.5 each side)
    off = w214(tmp_path, eye.format(z=0.06, b=0.6))  # off centre: the left end 10 mm short
    assert len(off) == 1 and "10 mm short of the left rail" in off[0] and "right" not in off[0]


def test_w214_a_stopper_narrower_than_its_belt(tmp_path):
    stop = (
        "{id: STP_01, type: stopper, description: S, position: [1, 0, 0], width: 0.4, "
        "io: {raise: STP_01_RAISE_DO}}"
    )
    found = w214(tmp_path, stop)
    assert len(found) == 1 and "blade (0.4 m)" in found[0] and "50 mm short" in found[0]
    unset = stop.replace(" width: 0.4,", "")
    assert w214(tmp_path, unset) == []  # the belt's width


def test_w214_a_diverter_swinging_off_its_belt(tmp_path):
    arm = (
        "{{id: DIV_01, type: diverter_arm, description: D, position: [1, 0, -0.25], "
        "length: 0.78, swing_deg: {s}, io: {{extend: DIV_01_EXTEND_DO}}}}"
    )
    off = w214(tmp_path, arm.format(s=-45))
    assert len(off) == 1 and "right rail" in off[0] and "sign of swing_deg" in off[0]
    assert w214(tmp_path, arm.format(s=45)) == []  # tip at z 0.30, past the right rail at 0.25
    assert len(w214(tmp_path, arm.format(s=20))) == 1  # over the belt but not across it


DECK = """openfactory: "0.1"
scene: {name: deck, description: A deck eye}
plc: {protocol: modbus_tcp, role: server}
components:
  - id: TT_01
    type: turntable
    description: Deck
    position: [0, 0, 0]
    length: 0.8
    width: 0.5
    speed_mps: 0.5
    turn_deg: 90
    turn_time_s: 1
    io: {run: TT_01_RUN_DO, turn: TT_01_TURN_DO}
  - id: PE_01
    type: photo_eye
    description: Deck Eye
    position: [0.1, 0.1, 0]
    beam_length: 0.4
    io: {detect: PE_01_DI}
"""


def test_w214_leaves_a_turntable_deck_alone(tmp_path):
    """Ruling 5: decks are not checked (two-lane-merge's deck eyes are short on purpose)."""
    path = tmp_path / "deck.yaml"
    path.write_text(DECK, encoding="utf-8")
    assert [f for f in lint_scene(path) if f.code == "W214"] == []
    # the same eye on a belt of the same width is flagged: only the deck spares it
    belt = DECK.replace("type: turntable", "type: belt_conveyor").replace(
        "    turn_deg: 90\n    turn_time_s: 1\n    io: {run: TT_01_RUN_DO, turn: TT_01_TURN_DO}",
        "    io: {run: TT_01_RUN_DO}",
    )
    path.write_text(belt, encoding="utf-8")
    assert len([f for f in lint_scene(path) if f.code == "W214"]) == 1


def test_no_shipped_example_or_test_scene_raises_w214():
    """The proposal's measurement (33 scenes, composites expanded): none."""
    paths = [
        p
        for folder in ("scenes", "scenes/examples", "tests/scenes")
        for p in sorted((REPO / folder).glob("*.yaml"))
        if not p.name.endswith(".modbus.yaml")
    ]
    assert len(paths) >= 33
    found = []
    for path in paths:
        try:
            loaded = build(load_scene(path), base_dir=path.parent)
        except Exception:  # a test scene built to fail to load is not this test's
            continue
        found += [
            f"{path.name}: {m}" for _, m in short_of_belt([c.config for c in loaded.components])
        ]
    assert found == []


# --- the editor's endpoint ------------------------------------------------------------------


def test_the_mount_endpoint_places_by_the_same_function(tmp_path):
    from openfactory.server.app import create_app
    from openfactory.server.editor import EditSession
    from openfactory.server.runtime import Runtime

    scene = tmp_path / "sorting-by-color.yaml"
    scene.write_text(
        (REPO / "scenes" / "sorting-by-color.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    session = EditSession(scene)
    runtime = Runtime(build(load_scene(scene), base_dir=tmp_path), realtime=False)
    client = TestClient(
        create_app(runtime, web_dist=None, edit=session), base_url="http://127.0.0.1"
    )
    body = {
        "base": session.base,
        "ops": [],
        "id": "DIV_01",
        "conveyor": "CONV_01",
        "rail": "rail_right",
        "along": 2.6,
    }
    got = client.post("/api/edit/mount", json=body).json()
    comps = by_id(load_scene(scene))
    want = place_on_rail("diverter_arm", {"swing_deg": 45}, comps["CONV_01"], "rail_right", 2.6)
    assert got["position"] == pytest.approx(list(want.position))
    assert got["rotation_deg"] == want.rotation_deg
    assert got["swing_deg"] == -45  # the right rail swings toward -z
    left = client.post("/api/edit/mount", json={**body, "rail": "rail_left"}).json()
    assert (
        left["position"] == pytest.approx([2.6, 0, -0.25]) and "swing_deg" not in left
    )  # as committed
    bad = client.post("/api/edit/mount", json={**body, "id": "TANK_99"})
    assert bad.status_code == 400
