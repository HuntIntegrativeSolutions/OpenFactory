"""pipe (PLAN 3.2, ADR-0005): the first link. Darcy-Weisbach resistance, the drawn length, link
ends checked at load, pipe-to-pipe joins, the check valve, open drains that never give back,
and the flow network lint (E102, E103, W211)."""

from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from openfactory.core import flownet
from openfactory.core.loader import build
from openfactory.core.schema import Scene
from openfactory.lint import lint_scene

SUP = {
    "id": "SUP_01",
    "type": "supply",
    "description": "Supply",
    "position": [0, 0, 0],
    "pressure_bar": 1.0,
}
TK = {
    "id": "TK_01",
    "type": "tank",
    "description": "Tank",
    "position": [3, 0, 0],
    "height": 2.0,
    "initial_level": 0.5,
}
DR = {"id": "DR_01", "type": "drain", "description": "Open Drain", "position": [6, -0.5, 0]}


def pipe(pid: str, frm: str, to: str, **extra: object) -> dict:
    return {
        "id": pid,
        "type": "pipe",
        "description": f"Pipe {pid}",
        "position": [1, 0, 0],
        "from": frm,
        "to": to,
        **extra,
    }


def scene(*components: dict) -> dict:
    return {"openfactory": "0.1", "scene": {"name": "pipe-test", "description": "Pipe test."},
            "plc": {"protocol": "none"}, "components": list(components)}  # fmt: skip


def sim(*components: dict):  # type: ignore[no-untyped-def]
    return build(Scene.model_validate(scene(*components)))


def test_the_drawn_length_sizes_it_and_its_flow_is_the_square_root_law():
    s = sim(SUP, TK, pipe("PIPE_01", "SUP_01", "TK_01.top", path=[[0, 2.5, 0]]))
    p = s.component("PIPE_01")
    # supply origin, up to the path point, across to the tank top's centre
    assert p.points == [[0.0, 0.0, 0.0], [0.0, 2.5, 0.0], [3.0, 2.0, 0.0]]
    length = 2.5 + math.hypot(3.0, 0.5)
    assert p.length == pytest.approx(length)
    r = flownet.pipe_resistance(length, 0.05, 0.02, 1.5)
    assert p.link.r == pytest.approx(r)
    s.step(0.02)
    head = 1.0 * flownet.BAR_HEAD - 2.0  # into the top: it falls freely from the nozzle
    assert p.state()["flow_m3h"] == pytest.approx(math.sqrt(head / r) * 3600, rel=1e-3)
    assert p.geometry()["points"] == p.points and p.geometry()["diameter"] == 0.05


def test_a_set_length_is_kept_and_a_bottom_nozzle_end_sits_on_the_wall_facing_the_pipe():
    s = sim(TK, DR, pipe("PIPE_01", "TK_01.bottom", "DR_01", length=4.0))
    p = s.component("PIPE_01")
    assert p.length == 4.0
    assert p.points[0] == pytest.approx([3.5, 0.05, 0.0])  # the 1 m tank's wall, toward the drain


def test_an_open_drain_never_gives_liquid_back():
    high = {**DR, "position": [6, 3.0, 0]}  # above the tank's surface
    s = sim(TK, high, pipe("PIPE_01", "TK_01.bottom", "DR_01"))
    for _ in range(10):
        s.step(0.02)
    assert s.component("PIPE_01").link.flow == 0.0
    assert s.component("TK_01").node.level == pytest.approx(0.5)


def test_a_check_valve_pipe_passes_one_way():
    low = {**TK, "id": "TK_02", "position": [6, 0, 0], "initial_level": 1.5}
    s = sim(TK, low, pipe("PIPE_01", "TK_01.bottom", "TK_02.bottom", check_valve=True))
    s.step(0.02)
    assert s.component("PIPE_01").link.flow == 0.0  # TK_02 is fuller: flow would run backward
    s2 = sim(TK, low, pipe("PIPE_01", "TK_02.bottom", "TK_01.bottom", check_valve=True))
    s2.step(0.02)
    assert s2.component("PIPE_01").link.flow > 0


def test_two_pipes_naming_each_other_join_through_an_implicit_junction():
    s = sim(SUP, TK, pipe("PIPE_01", "SUP_01", "PIPE_02", length=2.0),
            pipe("PIPE_02", "PIPE_01", "TK_01.top", length=2.0, position=[2, 1.0, 0]))  # fmt: skip
    s.step(0.02)
    a, b = s.component("PIPE_01").link, s.component("PIPE_02").link
    assert a.flow == pytest.approx(b.flow, rel=1e-9) and a.flow > 0
    joint = s.flownet.junctions[0]  # type: ignore[union-attr]
    assert joint.id == "PIPE_01>PIPE_02" and joint.elevation == 1.0
    assert s.component("PIPE_01").points[-1] == [2.0, 1.0, 0.0]  # drawn to the joint


BELT = {"id": "CONV_01", "type": "belt_conveyor", "description": "Belt", "position": [0, 0, 5],
        "length": 2, "width": 0.5, "speed_mps": 0.5, "io": {"run": "CONV_01_RUN_DO"}}  # fmt: skip


@pytest.mark.parametrize(
    ("components", "message"),
    [
        (
            [SUP, pipe("PIPE_01", "SUP_01", "TK_09.top")],
            "PIPE_01.to: 'TK_09.top' names no component",
        ),
        ([SUP, TK, pipe("PIPE_01", "SUP_01", "TK_01")], "TK_01 is a tank; name a nozzle"),
        ([SUP, TK, pipe("PIPE_01", "SUP_01", "TK_01.side")], "TK_01 has no nozzle 'side'"),
        ([SUP, TK, pipe("PIPE_01", "SUP_01.out", "TK_01.top")], "SUP_01 is a supply, not a tank"),
        (
            [SUP, BELT, pipe("PIPE_01", "SUP_01", "CONV_01")],
            "CONV_01 is a belt_conveyor, not part of the flow network",
        ),
        ([SUP, pipe("PIPE_01", "SUP_01", "PIPE_01")], "a link cannot end on itself"),
        (
            [SUP, TK, pipe("PIPE_01", "SUP_01", "PIPE_02"), pipe("PIPE_02", "SUP_01", "TK_01.top")],
            "PIPE_01.to names the link PIPE_02, so PIPE_02.from must name PIPE_01",
        ),
        ([SUP, TK, pipe("PIPE_01", "SUP_01", "TK_01.top", diameter=0)], "greater than 0"),
    ],
)
def test_bad_link_ends_fail_at_load_and_say_why(components, message):
    with pytest.raises(ValidationError, match=message):
        Scene.model_validate(scene(*components))


def lint(tmp_path: Path, *components: dict) -> list[str]:
    path = tmp_path / "flow.yaml"
    path.write_text(yaml.safe_dump(scene(*components), sort_keys=False), encoding="utf-8")
    return [f"{f.code} {f.message}" for f in lint_scene(path)]


def test_lint_flags_an_unused_declared_nozzle_but_not_the_defaults(tmp_path):
    side = {**TK, "nozzles": {"side": 1.0}}
    found = lint(tmp_path, SUP, side, pipe("PIPE_01", "SUP_01", "TK_01.top"))
    assert any(f.startswith("E102 TK_01: nozzle 'side'") for f in found)
    assert not [f for f in found if f.startswith("E102") and "'bottom'" in f]


def test_lint_flags_a_network_part_with_no_node_of_known_head(tmp_path):
    j1 = {"id": "J_01", "type": "junction", "description": "Tee", "position": [0, 0, 1]}
    j2 = {**j1, "id": "J_02", "position": [1, 0, 1]}
    found = lint(tmp_path, SUP, TK, pipe("PIPE_01", "SUP_01", "TK_01.top"), j1, j2,
                 pipe("PIPE_02", "J_01", "J_02"))  # fmt: skip
    assert [f for f in found if f.startswith("E103")] == [
        "E103 flow network part J_01, J_02: no tank, supply or open drain, so its heads are "
        "undefined and nothing flows"
    ]


def test_lint_warns_when_a_tank_is_too_small_for_its_connection(tmp_path):
    tiny = {**TK, "diameter": 0.05, "initial_level": 1.0}
    found = lint(
        tmp_path, tiny, DR, pipe("PIPE_01", "TK_01.bottom", "DR_01", diameter=0.1, minor_loss=0.5)
    )
    assert any(f.startswith("W211 TK_01: level time constant") for f in found)
    big = {**TK, "diameter": 1.5}
    found = lint(tmp_path, big, DR, pipe("PIPE_01", "TK_01.bottom", "DR_01"))
    assert not [f for f in found if f.startswith(("W211", "E102", "E103"))]
