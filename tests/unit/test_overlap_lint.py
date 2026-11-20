"""PLAN 5.5 (ruled 2026-11-19, ruling 5): the overlap lint covers every component's body, not
only conveyors' (W205). W217 names two components whose bodies overlap and a run of a link that
passes through a body; what is meant to touch is not a finding. Every shipped scene is clean."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from openfactory.core import footprints as fp
from openfactory.lint import lint_scene

REPO = Path(__file__).resolve().parents[2]


def scene(tmp_path: Path, components: list[dict], name: str = "s.yaml") -> Path:
    data = {
        "openfactory": "0.1",
        "scene": {"name": "overlap", "description": "W217 test"},
        "components": components,
    }
    path = tmp_path / name
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def w217(path: Path) -> list[str]:
    found = lint_scene(path)
    assert not [f for f in found if f.level == "error"], found
    return [f.message for f in found if f.code == "W217"]


TANK = {"id": "TK_01", "type": "tank", "description": "Tank", "position": [0, 0, 0],
        "diameter": 1.2, "height": 1.8, "io": {"level": "TK_01_LVL_AI"}}  # fmt: skip
SUPPLY = {"id": "SUP_01", "type": "supply", "description": "Supply", "position": [-3, 0.3, 0]}
DRAIN = {"id": "DR_01", "type": "drain", "description": "Drain", "position": [3, 0.3, 0]}


def pump(at: list[float], **kw: object) -> dict:
    return {"id": "P_01", "type": "pump", "description": "Pump", "position": at,
            "from": "TK_01.bottom", "to": "PIPE_01", "design_flow_m3h": 3.0,
            "design_head_m": 6.0, "io": {"run": "P_01_RUN_DO"}, **kw}  # fmt: skip


def pipe(**kw: object) -> dict:
    return {"id": "PIPE_01", "type": "pipe", "description": "Line", "position": [2, 0.3, 0],
            "from": "P_01", "to": "DR_01", **kw}  # fmt: skip


def test_a_pump_inside_a_tank_is_a_finding(tmp_path):
    """Two process components in one place: W205 saw conveyors only, so this passed before. The
    pump draws from the supply, so it names nothing it overlaps."""
    inside = pump([0.2, 0.3, 0], **{"from": "SUP_01"})
    found = w217(scene(tmp_path, [TANK, SUPPLY, DRAIN, inside, pipe()]))
    print(found)
    assert any("TK_01 and P_01 overlap" in m for m in found)


def test_a_run_through_a_body_is_a_finding_and_a_route_round_it_is_not(tmp_path):
    other = {"id": "TK_02", "type": "tank", "description": "Other tank", "position": [2.4, 0, 0],
             "diameter": 0.6, "height": 1.0, "io": {"level": "TK_02_LVL_AI"}}  # fmt: skip
    straight = w217(scene(tmp_path, [TANK, SUPPLY, DRAIN, pump([1.2, 0.05, 0]), pipe(), other]))
    print(straight)
    assert "the run of PIPE_01 passes through TK_02" in straight
    around = pipe(path=[[2, 0.3, 1.0], [3, 0.3, 1.0]])
    assert w217(scene(tmp_path, [TANK, SUPPLY, DRAIN, pump([1.2, 0.05, 0]), around, other])) == []


def test_what_is_meant_to_touch_is_not_a_finding(tmp_path):
    """A link at its own ends, a transmitter on what it measures, a device on its belt."""
    lt = {"id": "LT_01", "type": "transmitter", "description": "Level", "position": [0.6, 0.9, 0],
          "measures": "TK_01.level", "range": [0, 1.8], "io": {"pv": "LT_01_PV_AI"}}  # fmt: skip
    belt = {"id": "CV_01", "type": "belt_conveyor", "description": "Belt",
            "position": [0, 0, 4], "length": 3.0, "width": 0.5, "speed_mps": 0.4,
            "io": {"run": "CV_01_RUN_DO"}}  # fmt: skip
    eye = {"id": "PE_01", "type": "photo_eye", "description": "Eye", "position": [1.5, 0.1, 4],
           "rotation_deg": 0, "beam_length": 0.56, "io": {"detect": "PE_01_DI"}}  # fmt: skip
    emitter = {"id": "EMT_01", "type": "box_emitter", "description": "Source",
               "position": [0.3, 0.155, 4], "interval_s": 4.0,
               "parts": [{"name": "carton", "size": [0.3, 0.3, 0.3], "weight": 1.0}]}  # fmt: skip
    parts = [TANK, SUPPLY, DRAIN, pump([1.2, 0.05, 0]), pipe(), lt, belt, eye, emitter]
    assert w217(scene(tmp_path, parts)) == []
    lt["measures"] = "P_01.flow"  # the same place, now measuring something else
    assert any("TK_01 and LT_01 overlap" in m for m in w217(scene(tmp_path, parts)))


def test_bodies_are_three_dimensional():
    """A supply above a tank's top shares its plan but not its space."""
    tank = fp.boxes(
        type(
            "C",
            (),
            {
                "position": [0, 0, 0],
                "rotation_deg": 0,
                "type": "tank",
                "shape": "cylinder",
                "diameter": 1.2,
                "height": 1.8,
            },
        )()
    )
    above = fp.boxes(type("C", (), {"position": [0, 2.4, 0], "rotation_deg": 0,
                                    "type": "supply"})())  # fmt: skip
    assert fp.overlap(tank[0], above[0]) is None
    beside = fp.boxes(type("C", (), {"position": [0, 1.0, 0], "rotation_deg": 0,
                                     "type": "supply"})())  # fmt: skip
    assert fp.overlap(tank[0], beside[0]) is not None


@pytest.mark.parametrize(
    "path",
    [
        p
        for p in (
            *sorted((REPO / "scenes").glob("*.yaml")),
            *sorted((REPO / "scenes" / "examples").glob("*.yaml")),
            *sorted((REPO / "scenes" / "plants").glob("*/*.yaml")),
        )
        if ".modbus." not in p.name
    ],
    ids=lambda p: p.relative_to(REPO).as_posix(),
)
def test_every_shipped_scene_has_no_overlap(path):
    assert w217(path) == []
