"""PLAN 4.1d: snap ports declared in Python (core/snaps.py) and published in /api/scene, and the
two lint checks on them: W212 (two ends that meet with no connection; it replaces W203) and W213
(a connection whose ends do not meet). Each check has a test here that fails without it."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from openfactory.core.loader import build
from openfactory.core.schema import load_scene, parse_scene
from openfactory.core.snaps import best_gap, snap_ports
from openfactory.lint import lint_scene
from openfactory.server.app import create_app
from openfactory.server.runtime import Runtime

REPO = Path(__file__).resolve().parents[2]


def belt(cid: str, x: float, z: float = 0.0, rot: float = 0.0, y: float = 0.0, **extra) -> dict:
    return {
        "id": cid, "type": "belt_conveyor", "description": f"Belt {cid}",
        "position": [x, y, z], "rotation_deg": rot, "length": 2.0, "width": 0.5,
        "speed_mps": 0.5, "io": {"run": f"{cid}_RUN_DO"}, **extra,
    }  # fmt: skip


def scene_of(*components: dict, connections: list | None = None) -> dict:
    return {
        "openfactory": "0.1",
        "scene": {"name": "snaps", "description": "x", "author": "x"},
        "plc": {"protocol": "none"},
        "components": list(components),
        "connections": [{"from": a, "to": b} for a, b in connections or []],
    }


def parsed(data: dict):
    return parse_scene(yaml.safe_dump(data, sort_keys=False))


def lint_codes(tmp_path: Path, data: dict) -> list[str]:
    path = tmp_path / "snaps.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return [f.code for f in lint_scene(path) if f.code not in ("W201", "W202")]


# --- the ports ------------------------------------------------------------------------------------


def test_a_belts_ports_are_its_two_ends_at_belt_top_facing_out():
    (comp,) = parsed(scene_of(belt("CONV_01", 1.0, 2.0, rot=90, y=0.8))).components
    infeed, discharge = snap_ports(comp)
    assert (infeed.name, infeed.role, infeed.kind) == ("infeed", "in", "load")
    assert infeed.position == pytest.approx((1.0, 0.8, 2.0))
    assert infeed.heading_deg == pytest.approx(270.0)  # back against the flow
    assert (discharge.name, discharge.role) == ("discharge", "out")
    assert discharge.position == pytest.approx((1.0, 0.8, 0.0))  # 90 degrees: flow toward -z
    assert discharge.heading_deg == pytest.approx(90.0)
    assert infeed.width == discharge.width == 0.5


def test_a_reversible_belt_s_ends_take_both_roles():
    data = belt("CONV_01", 0, bidirectional=True)
    data["io"]["reverse"] = "CONV_01_REV_DO"
    (comp,) = parsed(scene_of(data)).components
    assert [p.role for p in snap_ports(comp)] == ["both", "both"]


def test_a_lift_has_its_ends_at_the_bottom_and_the_top_and_a_turntable_at_rest_and_turned():
    data = yaml.safe_load((REPO / "tests" / "scenes" / "demo-lift.yaml").read_text())
    lift = next(c for c in parsed(data).components if c.type == "lift")
    names = [p.name for p in snap_ports(lift)]
    assert names == ["infeed", "discharge", "infeed_top", "discharge_top"]
    top = snap_ports(lift)[2]
    assert top.position[1] == pytest.approx(lift.position[1] + lift.lift_height)
    merge = load_scene(REPO / "scenes" / "two-lane-merge.yaml")
    tt = next(c for c in merge.components if c.type == "turntable")
    ports = {p.name: p for p in snap_ports(tt)}
    assert set(ports) == {"infeed", "discharge", "infeed_turned", "discharge_turned"}
    assert ports["infeed_turned"].heading_deg - ports["infeed"].heading_deg == pytest.approx(
        tt.turn_deg
    )


def test_types_that_are_not_conveyors_have_no_ports():
    sorter = load_scene(REPO / "scenes" / "sorting-by-height.yaml")
    assert {c.type for c in sorter.components if snap_ports(c)} == {"belt_conveyor"}


def test_api_scene_publishes_the_ports_and_a_recording_does_not():
    path = REPO / "scenes" / "two-lane-merge.yaml"
    runtime = Runtime(build(load_scene(path), base_dir=path.parent), realtime=False)
    doc = (
        TestClient(create_app(runtime, web_dist=None), base_url="http://127.0.0.1")
        .get("/api/scene")
        .json()
    )
    tt = next(c for c in doc["components"] if c["type"] == "turntable")
    assert [p["name"] for p in tt["snaps"]][:2] == ["infeed", "discharge"]
    assert set(tt["snaps"][0]) == {"name", "kind", "position", "heading_deg", "width", "role"}
    assert not [c for c in doc["components"] if c["type"] == "photo_eye" and "snaps" in c]
    recorded = runtime.scene_document(meshes=False, snaps=False)
    assert not [c for c in recorded["components"] if "snaps" in c]


def test_every_shipped_connection_meets_and_every_meeting_pair_is_connected():
    """The shipped scenes are clean under both checks (none bridges a gap)."""
    for path in sorted((REPO / "scenes").glob("*.yaml")):
        if ".modbus" in path.name:
            continue
        scene = load_scene(path)
        by_id = {c.id: c for c in scene.components}
        for conn in scene.connections:
            g = best_gap(by_id[conn.from_], by_id[conn.to])
            assert g is not None and g.within(0.01, 1.0), (path.name, conn, g)


# --- W212: two ends that meet with no connection (replaces W203) ----------------------------------


@pytest.mark.parametrize(
    ("second", "warns"),
    [
        (belt("CONV_02", 2.0), True),  # in line, touching
        (belt("CONV_02", 2.015), True),  # 15 mm gap: within 2 cm
        (belt("CONV_02", 2.004, rot=90, z=1.0), False),  # beside, its infeed far away
        (belt("CONV_02", 2.03), False),  # 3 cm: not meeting
        (belt("CONV_02", 2.0, y=-0.1), False),  # 0.1 m lower: a drop
    ],
)
def test_w212_two_ends_that_meet_with_no_connection(tmp_path, second, warns):
    codes = lint_codes(tmp_path, scene_of(belt("CONV_01", 0), second))
    assert ("W212" in codes) is warns, codes
    joined = scene_of(belt("CONV_01", 0), second, connections=[("CONV_01", "CONV_02")])
    assert "W212" not in lint_codes(tmp_path, joined)


def test_w212_counts_a_handoff_through_a_turn_as_w203_did(tmp_path):
    # CONV_02 turned 90 degrees with its infeed on CONV_01's discharge
    codes = lint_codes(tmp_path, scene_of(belt("CONV_01", 0), belt("CONV_02", 2.0, rot=90)))
    assert "W212" in codes


def test_w203_is_retired(tmp_path):
    codes = lint_codes(tmp_path, scene_of(belt("CONV_01", 0), belt("CONV_02", 2.0)))
    assert "W203" not in codes


# --- W213: a connection whose ends do not meet ----------------------------------------------------


@pytest.mark.parametrize(
    ("second", "warns"),
    [
        (belt("CONV_02", 2.0), False),  # meets
        (belt("CONV_02", 2.005), False),  # 5 mm: within 1 cm
        (belt("CONV_02", 2.05), True),  # 5 cm gap
        (belt("CONV_02", 2.0, y=0.02), True),  # 2 cm step
        (belt("CONV_02", 2.0, rot=5), True),  # 5 degrees off
        (belt("CONV_02", 2.0, rot=90), True),  # through a turn: a transfer, not a join
    ],
)
def test_w213_a_connection_whose_ends_do_not_meet(tmp_path, second, warns):
    data = scene_of(belt("CONV_01", 0), second, connections=[("CONV_01", "CONV_02")])
    codes = lint_codes(tmp_path, data)
    assert ("W213" in codes) is warns, codes


def test_w213_message_names_the_ends_and_the_gap(tmp_path):
    data = scene_of(belt("CONV_01", 0), belt("CONV_02", 2.05), connections=[("CONV_01", "CONV_02")])
    path = tmp_path / "s.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    (f,) = [f for f in lint_scene(path) if f.code == "W213"]
    assert "CONV_01's discharge end and CONV_02's infeed end do not meet (50 mm apart" in f.message


def test_a_connected_turntable_counts_in_either_position(tmp_path):
    merge = yaml.safe_load((REPO / "scenes" / "two-lane-merge.yaml").read_text())
    assert not {"W212", "W213"} & set(lint_codes(tmp_path, copy.deepcopy(merge)))


def test_port_names_match_what_snap_ports_gives():
    from openfactory.core.snaps import PORT_NAMES

    merge = load_scene(REPO / "scenes" / "two-lane-merge.yaml")
    lift = yaml.safe_load((REPO / "tests" / "scenes" / "demo-lift.yaml").read_text())
    comps = [*merge.components, *parsed(lift).components]
    seen = {}
    for comp in comps:
        if snap_ports(comp):
            seen[comp.type] = tuple(p.name for p in snap_ports(comp))
    roller = parsed(scene_of({**belt("RC_01", 0), "type": "roller_conveyor"})).components[0]
    seen["roller_conveyor"] = tuple(p.name for p in snap_ports(roller))
    assert seen == PORT_NAMES
