"""PLAN 4.1c: the editor's palette and new components. Every built-in type's new component loads
in a scene (those that name pipe ends excepted: the user fills them in); ids and tags are
proposed as ruled (decision 5); a library composite is added to `composites:` with the
`use_library` operation, whose inverse gives the original bytes back."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from openfactory.core.loader import build
from openfactory.core.sceneedit import apply_ops, undo
from openfactory.core.schema import COMPONENT_MODELS, parse_scene
from openfactory.core.templates import (
    PREFIX,
    mesh_files,
    new_component,
    next_id,
    palette,
    placeholder,
    propose_tag,
)
from openfactory.lint import lint_scene

REPO = Path(__file__).resolve().parents[2]
SCENES = REPO / "scenes"
SORTER = SCENES / "sorting-by-height.yaml"
BASE = """\
openfactory: "0.1"
scene:
  name: palette-test
  description: A scene the palette's new components are added to.
components:
  - id: TK_01
    type: tank
    description: Test Tank
    position: [-3.0, 0.0, -3.0]
    diameter: 1.0
    height: 1.5
  - id: CONV_01
    type: belt_conveyor
    description: Test Belt Conveyor
    position: [0.0, 0.0, 0.0]
    length: 2.0
    width: 0.5
    speed_mps: 0.5
    io:
      run: CONV_01_RUN_DO
"""


def loads(text: str, folder: Path) -> None:
    build(parse_scene(text, folder), base_dir=folder)


def test_every_type_has_a_prefix_and_appears_in_the_palette():
    assert set(PREFIX) == set(COMPONENT_MODELS)
    assert len(set(PREFIX.values())) == len(PREFIX)  # no two types share a prefix
    items = palette(yaml.safe_load(BASE), SCENES)
    assert [i["type"] for i in items if i["kind"] == "primitive"] == list(COMPONENT_MODELS)
    lib = next(i for i in items if i["type"] == "accumulation_conveyor")
    assert lib["kind"] == "library" and lib["in_scene"] is False


@pytest.mark.parametrize("type_name", sorted(COMPONENT_MODELS))
def test_a_new_component_of_every_type_loads(type_name, tmp_path):
    data = yaml.safe_load(BASE)
    new = new_component(type_name, data, tmp_path, (2.0, 0.0, 1.5))
    comp = new["component"]
    assert new["library"] is None
    assert comp["id"] == next_id(PREFIX[type_name], data)
    assert comp["description"] == placeholder(type_name)
    text, inverses = apply_ops(BASE, [{"op": "add", "component": comp}])
    entry = next(i for i in palette(data, tmp_path) if i["type"] == type_name)
    if entry["needs"] and any(comp[f] == "" for f in entry["needs"]):
        assert type_name in {"pipe", "pump", "valve_control", "valve_onoff", "vfd_motor"}
        empty = "|".join(f for f in entry["needs"] if comp[f] == "")
        with pytest.raises(Exception, match=empty):
            loads(text, tmp_path)  # the preview's E100 names the field to fill in
    elif entry["note"]:
        assert type_name == "junction"
        with pytest.raises(Exception, match="no link connects"):
            loads(text, tmp_path)
    else:
        loads(text, tmp_path)
    assert undo(text, inverses) == BASE


def test_ids_and_tags_are_proposed_from_the_prefix_and_ports():
    data = yaml.safe_load(SORTER.read_text(encoding="utf-8"))
    eye = new_component("photo_eye", data, SCENES)["component"]
    assert eye["id"] == "PE_02"  # the sorter has PE_01
    assert eye["io"] == {"detect": "PE_02_DI"}
    assert eye["position"][1] == 0.1  # above the belt
    conv = new_component("belt_conveyor", data, SCENES)["component"]
    assert conv["id"] == "CONV_03" and conv["io"] == {"run": "CONV_03_RUN_DO"}  # required only
    vfd = new_component("vfd_motor", data, SCENES)["component"]
    assert vfd["drives"] == ""  # both conveyors bind run, so neither can be driven by a VFD
    free = new_component("belt_conveyor", data, SCENES)["component"]
    del free["io"]
    data["components"].append(free)
    assert new_component("vfd_motor", data, SCENES)["component"]["drives"] == free["id"]
    assert propose_tag("A" * 30, "position_fb", "AI", False).endswith("_AI")
    assert len(propose_tag("A" * 30, "position_fb", "AI", False)) == 40


def test_a_library_composite_is_added_with_use_library_and_loads(tmp_path):
    for f in SCENES.glob("sorting-by-height*"):
        shutil.copy(f, tmp_path / f.name)
    (tmp_path / "_library").mkdir()
    shutil.copy(SCENES / "_library" / "accumulation_conveyor.yaml", tmp_path / "_library")
    scene = tmp_path / "sorting-by-height.yaml"
    text = scene.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    new = new_component("accumulation_conveyor", data, tmp_path, (0.0, 0.0, 4.0))
    comp = new["component"]
    assert new["library"] == "accumulation_conveyor"
    assert comp["id"] == "ACC_01"
    assert comp["io"] == {
        "release": "ACC_01_RELEASE_DO",
        "zone1_eye": "ACC_01_ZONE1_EYE_DI",
        "zone2_eye": "ACC_01_ZONE2_EYE_DI",
        "zone3_eye": "ACC_01_ZONE3_EYE_DI",
        "zone4_eye": "ACC_01_ZONE4_EYE_DI",
    }
    ops = [{"op": "use_library", "name": "accumulation_conveyor"}, {"op": "add", "component": comp}]
    edited, inverses = apply_ops(text, ops)
    assert "composites:\n  - {library: accumulation_conveyor}\ncomponents:\n" in edited
    loads(edited, tmp_path)
    errors = [f for f in lint_scene(scene, text=edited) if f.level == "error"]
    assert errors == []
    assert undo(edited, inverses) == text
    # once used, the palette says so and a second instance needs no use_library
    again = new_component("accumulation_conveyor", yaml.safe_load(edited), tmp_path)
    assert again["library"] is None and again["component"]["id"] == "ACC_02"
    same, inv = apply_ops(edited, [{"op": "use_library", "name": "accumulation_conveyor"}])
    assert same == edited and inv == [{"op": "batch", "ops": []}]


@pytest.mark.parametrize(
    ("block", "expected"),
    [
        ("composites: []\n", "composites: [{library: lib_a}]\n"),
        ("composites: [{library: x_y}]\n", "composites: [{library: x_y}, {library: lib_a}]\n"),
        (
            "composites:\n  - {library: x_y}  # kept\n",
            "composites:\n  - {library: x_y}  # kept\n  - {library: lib_a}\n",
        ),
    ],
)
def test_use_library_appends_to_flow_and_block_lists(block, expected):
    text = 'openfactory: "0.1"\n' + block + "components: []\n"
    edited, inverses = apply_ops(text, [{"op": "use_library", "name": "lib_a"}])
    assert edited == 'openfactory: "0.1"\n' + expected + "components: []\n"
    assert undo(edited, inverses) == text
    with pytest.raises(ValueError, match="not a library composite name"):
        apply_ops(text, [{"op": "use_library", "name": "../x"}])


def test_mesh_files_lists_glb_files_beside_the_scene():
    assert mesh_files(REPO / "tests" / "scenes") == ["models/pusher-housing.glb"]


def test_new_scene_loads_and_holds_placeholders(tmp_path):
    """`openfactory new` (2026-11-13): one belt conveyor at the origin, placeholder descriptions
    the editor holds Save on, a Modbus server block; it loads and lints with no errors."""
    from openfactory.core.schema import parse_scene
    from openfactory.core.templates import new_scene

    text = new_scene(tmp_path / "my-line.yaml")
    assert text.startswith("# my-line: built in the OpenFactory scene editor.\n")
    assert "Not a safety-rated simulation" in text
    scene = parse_scene(text, tmp_path)
    assert scene.scene.name == "my-line" and scene.scene.description == "New scene"
    (conv,) = scene.components
    assert (conv.id, conv.type) == ("CONV_01", "belt_conveyor")
    assert conv.description == "New belt conveyor"
    assert scene.plc.protocol == "modbus_tcp"
    assert "photo_eye" in new_scene(tmp_path / "eye.yaml", "photo_eye")
    with pytest.raises(ValueError, match="lower case"):
        new_scene(tmp_path / "My Line.yaml")
    with pytest.raises(ValueError, match="needs another component"):
        new_scene(tmp_path / "heat.yaml", "heater")  # a heater names a tank


def test_new_cli_never_overwrites(tmp_path):
    from typer.testing import CliRunner

    from openfactory.cli import app

    path = tmp_path / "line-a.yaml"
    runner = CliRunner()
    first = runner.invoke(app, ["new", str(path), "--no-edit"])
    assert first.exit_code == 0, first.output
    before = path.read_text(encoding="utf-8")
    again = runner.invoke(app, ["new", str(path), "--no-edit"])
    assert again.exit_code == 1 and "exists" in again.output
    assert path.read_text(encoding="utf-8") == before
