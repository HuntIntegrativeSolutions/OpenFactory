"""PLAN 4.1g2 (ruled 2026-11-14): a nozzle may be fixed on the tank's wall with a bearing (an
optional form: the format stays 0.1), and the process types publish pipe ports."""

from __future__ import annotations

import math
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from openfactory.core.loader import build
from openfactory.core.migrate import migrate_text
from openfactory.core.schema import SCHEMA_VERSION, load_scene, parse_scene
from openfactory.core.snaps import PIPE_PORT_NAMES, nozzle_wall, pipe_ports
from openfactory.server.app import create_app
from openfactory.server.runtime import Runtime

REPO = Path(__file__).resolve().parents[2]
PID = (REPO / "scenes" / "tank-level-pid.yaml").read_text(encoding="utf-8")
SHIPPED = [
    p
    for p in [*(REPO / "scenes").glob("*.yaml"), *(REPO / "scenes" / "examples").glob("*.yaml")]
    if not p.name.endswith(".modbus.yaml")
]


def with_nozzles(text: str, block: str) -> str:
    """tank-level-pid with TK_01 given a `nozzles:` block."""
    return text.replace("    type: tank\n", f"    type: tank\n    nozzles:\n{block}", 1)


def by_id(loaded) -> dict:
    return {c.id: c for c in loaded.components}


def test_the_format_stays_0_1_and_every_shipped_file_migrates_byte_for_byte():
    """Ruling 2: additive, no migration."""
    assert SCHEMA_VERSION == "0.1"
    for path in [*SHIPPED, *(REPO / "scenes" / "_library").glob("*.yaml")]:
        text = path.read_text(encoding="utf-8")
        assert migrate_text(text).text == text, path.name


def test_a_bottom_nozzle_with_a_bearing_draws_its_pipe_on_that_wall_and_sizes_the_pipe():
    plain = by_id(build(parse_scene(PID, REPO / "scenes")))
    fixed = by_id(
        build(
            parse_scene(
                with_nozzles(PID, "      bottom: {height: 0, bearing_deg: 90}\n"), REPO / "scenes"
            )
        )
    )
    tank = fixed["TK_01"].config  # a 0.6 m cylinder at (0, 1.5, 0)
    # bearing 90 turns local +x toward -z, as rotation_deg: the wall point is (0, 1.55, -0.3)
    end = fixed["PIPE_02"].points[0]
    assert math.dist(end, (0.0, 1.55, -0.3)) < 0.001
    assert nozzle_wall(tank, "bottom") == pytest.approx((0.0, 1.55, -0.3))
    # without a bearing it faces its link, as before: (0.3, 1.55, 0) toward the drain at +x
    assert math.dist(plain["PIPE_02"].points[0], (0.3, 1.55, 0.0)) < 1e-9
    # the pipe's default length is its drawn length, so it follows the nozzle
    for comp in (plain["PIPE_02"], fixed["PIPE_02"]):
        pts = comp.points
        drawn = sum(math.dist(a, b) for a, b in zip(pts, pts[1:], strict=False))
        assert comp.length == pytest.approx(max(drawn, 0.1))
    assert fixed["PIPE_02"].length > plain["PIPE_02"].length


def test_a_box_tanks_bearing_meets_the_wall_its_ray_crosses():
    box = PID.replace("    diameter: 0.6\n", "    shape: box\n    length: 1.0\n    width: 0.4\n", 1)
    assert "shape: box" in box
    text = with_nozzles(box, "      side: {height: 0.8, bearing_deg: 45}\n")
    tank = next(c for c in parse_scene(text, REPO / "scenes").components if c.id == "TK_01")
    # local direction (cos 45, -sin 45): the +x half-length 0.5 is past the -z half-width 0.2
    x, y, z = nozzle_wall(tank, "side")
    assert z == pytest.approx(-0.2) and x == pytest.approx(0.2) and y == pytest.approx(1.5 + 0.8)


def test_top_takes_no_bearing_and_bottom_stays_at_height_0():
    with pytest.raises(ValidationError, match="nozzle top is the centre of the top"):
        parse_scene(with_nozzles(PID, "      top: {height: 2, bearing_deg: 0}\n"), REPO / "scenes")
    with pytest.raises(ValidationError, match="nozzle bottom is at height 0"):
        parse_scene(
            with_nozzles(PID, "      bottom: {height: 0.2, bearing_deg: 0}\n"), REPO / "scenes"
        )
    with pytest.raises(ValidationError, match="bearing_deg"):
        parse_scene(with_nozzles(PID, "      side: {height: 0.2}\n"), REPO / "scenes")
    ok = parse_scene(
        with_nozzles(PID, "      side: 0.4\n      low: {height: 0.2, bearing_deg: 180}\n"),
        REPO / "scenes",
    )
    tank = next(c for c in ok.components if c.id == "TK_01")
    assert tank.all_nozzles() == {"bottom": 0.0, "top": 2.0, "side": 0.4, "low": 0.2}
    assert tank.bearings() == {"low": 180.0}


def test_each_process_type_has_its_pipe_ports():
    scene = load_scene(REPO / "scenes" / "plantpax-tank-level.yaml")
    found = {c.type: [(p.name, p.role) for p in pipe_ports(c)] for c in scene.components}
    assert found["tank"] == [("bottom", "both"), ("top", "both")]
    assert found["supply"] == [("outlet", "out")]
    assert found["drain"] == [("inlet", "in")]
    assert found["pump"] == [("suction", "in"), ("discharge", "out")]
    assert found["pipe"] == [("inlet", "in"), ("outlet", "out")]
    assert found["valve_control"] == [("inlet", "in"), ("outlet", "out")]
    tank = next(c for c in scene.components if c.type == "tank")
    bottom, top = pipe_ports(tank)
    assert bottom.heading_deg is None and bottom.radius == pytest.approx(
        tank.diameter / 2
    )  # a ring
    assert top.position == pytest.approx(
        (tank.position[0], tank.position[1] + tank.height, tank.position[2])
    )
    pump = next(c for c in scene.components if c.type == "pump")
    assert {p.position for p in pipe_ports(pump)} == {tuple(pump.position)}  # ruling 3: the origin
    assert set(PIPE_PORT_NAMES) == {
        "supply",
        "drain",
        "junction",
        "pipe",
        "valve_onoff",
        "valve_control",
        "pump",
    }
    fixed = parse_scene(
        with_nozzles(PID, "      bottom: {height: 0, bearing_deg: 90}\n"), REPO / "scenes"
    )
    ports = {p.name: p for p in pipe_ports(next(c for c in fixed.components if c.id == "TK_01"))}
    assert ports["bottom"].heading_deg == 90 and ports["bottom"].radius == 0
    assert ports["bottom"].position == pytest.approx((0.0, 1.55, -0.3))


def test_api_scene_publishes_pipe_ports_and_a_recording_does_not():
    path = REPO / "scenes" / "two-tank-cascade.yaml"
    runtime = Runtime(build(load_scene(path), base_dir=path.parent), realtime=False)
    client = TestClient(create_app(runtime, web_dist=None), base_url="http://127.0.0.1")
    doc = client.get("/api/scene").json()
    tank = next(c for c in doc["components"] if c["id"] == "TK_01")
    assert [(p["name"], p["kind"]) for p in tank["snaps"]] == [("bottom", "pipe"), ("top", "pipe")]
    assert tank["snaps"][0]["heading_deg"] is None and tank["snaps"][0]["radius"] > 0
    pipe = next(c for c in doc["components"] if c["type"] == "pipe")
    assert [p["name"] for p in pipe["snaps"]] == ["inlet", "outlet"]
    recorded = runtime.scene_document(meshes=False, snaps=False)
    assert not [c for c in recorded["components"] if "snaps" in c]
