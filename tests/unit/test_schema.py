"""PLAN 1.1: the scene schema loads the example, rejects unknown keys, and rejects bad tag names."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from openfactory.core import schema
from openfactory.core.schema import Scene, load_scene, parse_scene, port_specs

REPO = Path(__file__).resolve().parents[2]
EXAMPLE = REPO / "scenes" / "sorting-by-height.yaml"


@pytest.fixture
def example_data() -> dict:
    return yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))


def _component(data: dict, cid: str) -> dict:
    return next(c for c in data["components"] if c["id"] == cid)


# --- Loads the example scene -------------------------------------------------------------------


def test_loads_example_scene():
    scene = load_scene(EXAMPLE)
    assert scene.scene.name == "sorting-by-height"
    assert [c.id for c in scene.components] == [
        "CONV_01",
        "PE_01",
        "CYL_01",
        "EMT_01",
        "REM_01",
        "CONV_02",
        "REM_02",
    ]
    assert scene.plc.protocol == "modbus_tcp"
    assert scene.plc.role == "server"
    assert scene.plc.bind is None and scene.plc.port is None  # openfactory.toml supplies these
    assert scene.plc.analog_encoding == "float32"


def test_example_tag_bindings_and_kinds():
    bindings = {b.name: b for b in load_scene(EXAMPLE).tag_bindings()}
    assert set(bindings) == {
        "CONV_01_RUN_DO",
        "CONV_01_SPD_AO",
        "CONV_01_RUN_DI",
        "PE_01_DI",
        "CYL_01_EXT_DO",
        "CYL_01_EXT_DI",
        "CYL_01_RET_DI",
        "EMT_01_EN_DO",
        "CONV_02_RUN_DO",
        "CONV_02_SPD_AO",
        "CONV_02_RUN_DI",
    }
    speed = bindings["CONV_01_SPD_AO"]
    assert speed.kind == "AO"
    assert speed.port.range == (0.0, 100.0)
    assert speed.port.units == "%"
    assert bindings["PE_01_DI"].component_id == "PE_01"
    assert speed.initial == 100.0 and bindings["CONV_02_SPD_AO"].initial == 100.0
    assert bindings["CONV_01_RUN_DO"].initial is None


def test_analog_encoding_int16_scaled_accepted(example_data):
    example_data["plc"]["analog_encoding"] = "int16_scaled"
    assert Scene.model_validate(example_data).plc.analog_encoding == "int16_scaled"


# --- Rejects unknown keys at every level -------------------------------------------------------


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda d: d.update(colour="red"), id="top-level"),
        pytest.param(lambda d: d["scene"].update(sede=1), id="scene-meta"),
        pytest.param(lambda d: d["plc"].update(adress="x"), id="plc"),
        pytest.param(lambda d: _component(d, "CONV_01").update(lenght=4.0), id="component"),
        pytest.param(lambda d: _component(d, "CONV_01")["io"].update(stop="X_DO"), id="io"),
        pytest.param(
            lambda d: _component(d, "EMT_01")["parts"][0].update(colour="red"), id="emitter-part"
        ),
    ],
)
def test_rejects_unknown_key(example_data, mutate):
    data = copy.deepcopy(example_data)
    mutate(data)
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        Scene.model_validate(data)


def test_rejects_unknown_component_type(example_data):
    _component(example_data, "PE_01")["type"] = "laser_scanner"
    with pytest.raises(ValidationError, match="does not match any of the expected tags"):
        Scene.model_validate(example_data)


# --- Rejects bad tag names ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("port", "tag", "reason"),
    [
        ("detect", "pe_01_di", "lowercase"),
        ("detect", "PE-01_DI", "dash"),
        ("detect", "1PE_01_DI", "leading digit"),
        ("detect", "PE_01", "no kind suffix"),
        ("detect", "PE_01_DO", "suffix does not match port direction"),
        ("detect", "P" * 38 + "_DI", "longer than 40 characters"),
    ],
)
def test_rejects_bad_tag_name(example_data, port, tag, reason):
    _component(example_data, "PE_01")["io"][port] = tag
    with pytest.raises(ValidationError, match="String should"):
        Scene.model_validate(example_data)


def test_accepts_tag_of_exactly_40_characters(example_data):
    tag = "P" * 37 + "_DI"
    assert len(tag) == 40
    _component(example_data, "PE_01")["io"]["detect"] = tag
    Scene.model_validate(example_data)


def test_rejects_duplicate_tag(example_data):
    _component(example_data, "CYL_01")["io"]["extended"] = "PE_01_DI"
    with pytest.raises(ValidationError, match="bound twice"):
        Scene.model_validate(example_data)


def test_rejects_duplicate_component_id(example_data):
    _component(example_data, "REM_01")["id"] = "PE_01"
    with pytest.raises(ValidationError, match="duplicate component id"):
        Scene.model_validate(example_data)


def test_rejects_missing_required_port(example_data):
    del _component(example_data, "CONV_01")["io"]["run"]
    with pytest.raises(ValidationError, match="Field required"):
        Scene.model_validate(example_data)


def test_component_description_required(example_data):
    del _component(example_data, "REM_01")["description"]
    with pytest.raises(ValidationError, match="Field required"):
        Scene.model_validate(example_data)


# --- YAML 1.1 boolean trap and schema export ---------------------------------------------------


def test_yaml_on_key_is_not_a_boolean():
    text = """
openfactory: "0.1"
scene: {name: lamp-test, description: Pilot light only}
components:
  - id: PL_01
    type: pilot_light
    description: Line Running Pilot Light
    position: [0, 1, 0]
    io: {on: PL_01_DO}
"""
    scene = parse_scene(text)
    assert scene.tag_bindings()[0].name == "PL_01_DO"


def test_every_port_kind_matches_its_tag_pattern():
    for type_name, model in schema.COMPONENT_MODELS.items():
        io_model = model.model_fields["io"].annotation
        for name, spec in port_specs(io_model).items():
            pattern = str(io_model.model_json_schema()["properties"][name])
            assert f"_{spec.kind}$" in pattern, f"{type_name}.io.{name}"


def test_committed_json_schema_is_current():
    assert schema.JSON_SCHEMA_PATH.read_text(encoding="utf-8") == schema.scene_json_schema(), (
        "run: uv run python -m openfactory.core.schema"
    )


# --- connections -------------------------------------------------------------------------------


def _two_conveyors(example_data: dict) -> dict:
    """The example already has two conveyors: CONV_01 and the divert lane CONV_02."""
    assert _component(example_data, "CONV_02")["type"] == "belt_conveyor"
    return example_data


def test_connections_accepted_including_loops(example_data):
    data = _two_conveyors(example_data)
    data["connections"] = [
        {"from": "CONV_01", "to": "CONV_02"},
        {"from": "CONV_02", "to": "CONV_01"},
    ]
    scene = Scene.model_validate(data)
    assert [(c.from_, c.to) for c in scene.connections] == [
        ("CONV_01", "CONV_02"),
        ("CONV_02", "CONV_01"),
    ]


@pytest.mark.parametrize(
    ("connections", "message"),
    [
        ([{"from": "CONV_01", "to": "CONV_02", "speed": 1}], "Extra inputs are not permitted"),
        ([{"from_": "CONV_01", "to": "CONV_02"}], "Extra inputs are not permitted"),
        ([{"from": "CONV_01", "to": "CONV_09"}], "unknown component 'CONV_09'"),
        ([{"from": "CONV_01", "to": "PE_01"}], "is a photo_eye, not a conveyor"),
        ([{"from": "CONV_01", "to": "CONV_01"}], "cannot feed itself"),
        (
            [{"from": "CONV_01", "to": "CONV_02"}, {"from": "CONV_01", "to": "CONV_01X"}],
            "feeds both",
        ),
    ],
)
def test_bad_connections_rejected(example_data, connections, message):
    data = _two_conveyors(example_data)
    conv3 = copy.deepcopy(_component(data, "CONV_02"))
    conv3.update(id="CONV_01X", io={"run": "CONV_01X_RUN_DO"})
    data["components"].append(conv3)
    data["connections"] = connections
    with pytest.raises(ValidationError, match=message):
        Scene.model_validate(data)
