"""PLAN 2.16: composite components declared in a scene. buffer-and-release is rebuilt as one
composite instance with byte-identical lockfiles and the same simulation; a recursive definition
and an unexposed required port are load errors naming the cycle and the port; a computed DI
follows its expression; lint shows W208 (a composite with no I/O) and W205 on composite parts
(ruled 2026-10-31)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from typer.testing import CliRunner

from openfactory.cli import app as cli
from openfactory.core.iotable import Writer
from openfactory.core.loader import load
from openfactory.core.schema import COMPONENT_MODELS, JSON_SCHEMA_PATH, Scene, load_scene
from openfactory.lint import lint_scene
from openfactory.protocols.modbus import lockfile_path, scene_register_map
from openfactory.pyplc import ScriptPlc
from openfactory.server.runtime import Runtime

REPO = Path(__file__).resolve().parents[2]
BUFFER = REPO / "scenes" / "buffer-and-release.yaml"
FLAT = REPO / "tests" / "scenes" / "buffer-and-release-flat.yaml"
GATED = REPO / "tests" / "scenes" / "gated-conveyor.yaml"
RENAMED = {"CONV_BUF": "BUF_BELT", "STP_01": "BUF_GATE", "PE_FULL": "BUF_FULL"}


def gated_data() -> dict:
    return yaml.safe_load(GATED.read_text(encoding="utf-8"))


def errors(data: dict) -> str:
    with pytest.raises(ValidationError) as info:
        Scene.model_validate(data)
    return str(info.value)


# --- buffer-and-release rebuilt on a composite ------------------------------------------------


def test_buffer_is_one_instance_expanded_where_the_three_components_were():
    composite, flat = load_scene(BUFFER), load_scene(FLAT)
    by_id = {c.id: c for c in composite.components}
    for old, new in RENAMED.items():
        before = next(c for c in flat.components if c.id == old)
        after = by_id[new]
        assert after.type == before.type and after.position == before.position, new
        assert after.description == before.description, new  # "Buffer Belt Conveyor", ...
        assert composite.composite_of(new) == "BUF" and composite.instance_type("BUF") == (
            "gated_buffer"
        )
    assert [b.name for b in composite.tag_bindings()] != []
    assert sorted(b.name for b in composite.tag_bindings()) == sorted(
        b.name for b in flat.tag_bindings()
    )


@pytest.mark.parametrize("encoding", ["float32", "int16_scaled"])
def test_buffer_lockfiles_are_byte_identical(tmp_path, encoding):
    scene = tmp_path / BUFFER.name
    shutil.copy(BUFFER, scene)
    lock = lockfile_path(BUFFER, encoding)
    shutil.copy(lock, tmp_path / lock.name)
    scene_register_map(load_scene(scene), encoding, scene_path=scene, write=True)
    assert (tmp_path / lock.name).read_bytes() == lock.read_bytes()


def test_buffer_composite_steps_exactly_like_the_flat_scene():
    """Same PLC script, 60 s: every component state and every tag equal, ids renamed."""
    runs = []
    for path in (BUFFER, FLAT):
        loaded = load(path)
        plc = ScriptPlc(REPO / "examples" / "pyplc" / "buffer-and-release.py", loaded.io)
        trace, t = [], 0.0
        for _ in range(3000):
            plc.scan(t)
            loaded.step(0.02)
            t += 0.02
            if loaded.tick % 50 == 0:
                states = {RENAMED.get(c.id, c.id): c.state() for c in loaded.components}
                parts = sorted((p.id, p.position) for p in loaded.world.parts.values())
                trace.append((states, loaded.io.values(), parts))
        runs.append(trace)
    assert runs[0] == runs[1]


# --- load errors --------------------------------------------------------------------------------


def test_a_recursive_definition_is_rejected_naming_the_cycle():
    data = gated_data()
    data["composites"].append(
        {"type": "loop_a", "parts": [{"id": "b", "type": "loop_b"}], "io": {}}
    )
    data["composites"].append(
        {"type": "loop_b", "parts": [{"id": "a", "type": "loop_a"}], "io": {}}
    )
    assert "recursive composite: loop_a -> loop_b -> loop_a" in errors(data)
    data["composites"][1:] = [{"type": "itself", "parts": [{"id": "me", "type": "itself"}]}]
    assert "recursive composite: itself -> itself" in errors(data)


def test_one_level_of_nesting_expands_and_two_levels_are_rejected():
    data = gated_data()
    data["composites"].append({
        "type": "gated_pair",
        "params": {"gap": 0.0},
        "parts": [
            {"id": "first", "type": "gated_conveyor", "position": [0, 0, 0]},
            {"id": "second", "type": "gated_conveyor", "position": ["2.0 + {gap}", 0, 0]},
        ],
        "io": {"run1": {"to": "first.run"}, "run2": {"to": "second.run"},
               "open1": {"to": "first.open"}, "open2": {"to": "second.open"},
               "eye1": {"from": "first.eye"}, "eye2": {"from": "second.eye"},
               "wait1": {"from": "first.waiting"}},
    })  # fmt: skip
    data["components"].append({
        "id": "PAIR", "type": "gated_pair", "description": "Pair", "position": [0, 0, 3],
        "io": {"run1": "PAIR_R1_DO", "run2": "PAIR_R2_DO", "open1": "PAIR_O1_DO",
               "open2": "PAIR_O2_DO", "eye1": "PAIR_E1_DI", "eye2": "PAIR_E2_DI",
               "wait1": "PAIR_W1_DI"},
    })  # fmt: skip
    scene = Scene.model_validate(data)
    ids = [c.id for c in scene.components if scene.composite_of(c.id) == "PAIR"]
    assert ids == ["PAIR_FIRST_BELT", "PAIR_FIRST_GATE", "PAIR_FIRST_EYE",
                   "PAIR_SECOND_BELT", "PAIR_SECOND_GATE", "PAIR_SECOND_EYE"]  # fmt: skip
    second = next(c for c in scene.components if c.id == "PAIR_SECOND_BELT")
    assert second.position == (2.0, 0.0, 3.0)
    wait = next(b for b in scene.tag_bindings() if b.name == "PAIR_W1_DI")
    assert wait.transform.expr == "PAIR_FIRST_GATE.raised and PAIR_FIRST_EYE.detect"
    data["composites"].append(
        {"type": "triple", "parts": [{"id": "p", "type": "gated_pair"}], "io": {}}
    )
    assert "composite nesting deeper than one level: triple -> gated_pair -> gated_conveyor" in (
        errors(data)
    )


def test_an_unexposed_required_port_is_a_load_error_naming_the_port():
    data = gated_data()
    del data["composites"][0]["io"]["run"]
    data["components"][1]["io"].pop("run")
    data["components"][2]["io"].pop("run")
    message = errors(data)
    assert "composite gated_conveyor: part belt's required port 'run' is unexposed" in message
    assert "`belt_run: {to: belt.run}`" in message


@pytest.mark.parametrize(
    "change, expected",
    [
        (lambda d: d["components"][1]["io"].pop("run"),
         "GC_01 (gated_conveyor): io.run is required (it binds belt.run, a required port)"),
        (lambda d: d["components"][1].update(width=2),
         "GC_01 (gated_conveyor): unknown parameter 'width' (parameters: gate_setback, length)"),
        (lambda d: d["components"][1].update(length="long"),
         "parameter length must be a number"),
        (lambda d: d["components"][1]["io"].update(speed="GC_01_SPD_AO"),
         "io.speed is not a port of gated_conveyor"),
        (lambda d: d["composites"][0]["io"].update(bad={"to": "eye.detect"}),
         "eye.detect is a DI port; `to` takes DO or AO ports"),
        (lambda d: d["composites"][0]["io"].update(bad={"from": "nope.detect"}),
         "no part 'nope'"),
        (lambda d: d["composites"][0]["io"].update(bad={"from": "eye.detect and belt.speed"}),
         "belt.speed is a AO port"),
        (lambda d: d["composites"][0]["io"].update(bad={"from": "eye.detect or len(x)"}),
         "Call is not allowed"),
        (lambda d: d["composites"][0]["io"].update(bad={"from": "eye.detect + 1"}),
         "io.bad: expression 'eye.detect + 1': arithmetic takes numbers, not true/false"),
        (lambda d: d["composites"][0]["parts"].append(
            {"id": "dist", "type": "distance_sensor", "position": [1, 0.3, 0], "beam_length": 1})
            or d["composites"][0]["io"].update(dist={"from": "dist.distance"},
                                               bad={"from": "dist.distance * 2"}),
         "io.bad: 'dist.distance * 2' gives a number; a DI needs true/false"),
        (lambda d: d["composites"][0]["io"].update(bad={"from": "eye.detect", "invert": True}),
         "`invert` is for a `to` on a DO port"),
        (lambda d: d["composites"][0]["io"].update(dup={"to": "belt.run"}),
         "belt.run is already bound by io.run"),
        (lambda d: d["composites"][0]["parts"][0].update(io={"run": "X_DO"}),
         "a part has no io of its own"),
        (lambda d: d["composites"][0]["parts"][1].update(position=["{length} - {nope}", 0, 0]),
         "unknown parameter 'nope'"),
        (lambda d: d["composites"][0].update(type="stopper"),
         "composite type 'stopper' has a built-in component's name"),
        (lambda d: d["components"][1].update(type="gated_conveyer"),
         "GC_01: unknown component type 'gated_conveyer'"),
    ],
)  # fmt: skip
def test_load_errors_name_what_is_wrong(change, expected):
    data = gated_data()
    change(data)
    assert expected in errors(data)


# --- behavior, exports, schema, viewer document ------------------------------------------------


def test_a_computed_di_follows_its_expression():
    loaded = load(GATED)
    io = loaded.io
    for tag in ("EMT_01_EN_DO", "GC_01_RUN_DO", "GC_02_RUN_DO"):
        io.set(tag, True, Writer.PLC)
    seen_wait = False
    for _ in range(600):  # 12 s: a box rides to the raised gate and stands at the eye
        loaded.step(0.02)
        seen_wait |= io.get("GC_01_WAIT_DI")
        assert io.get("GC_01_WAIT_DI") == (io.get("GC_01_EYE_DI") and loaded.component(
            "GC_01_GATE").raw["raised"])  # fmt: skip
    assert seen_wait and io.get("GC_01_WAIT_DI")
    io.set("GC_01_OPEN_DO", True, Writer.PLC)  # open: the inverted command lowers the gate
    for _ in range(100):
        loaded.step(0.02)
    assert not io.get("GC_01_WAIT_DI")


def test_the_csv_lists_instance_prefixed_parts_and_the_expression(tmp_path):
    scene = tmp_path / GATED.name
    shutil.copy(GATED, scene)
    shutil.copy(lockfile_path(GATED, "float32"), tmp_path)
    out = tmp_path / "io.csv"
    result = CliRunner().invoke(cli, ["export", str(scene), "-o", str(out)])
    assert result.exit_code == 0, result.output
    rows = out.read_text(encoding="utf-8").splitlines()
    wait = next(r for r in rows if r.startswith("GC_01_WAIT_DI,"))
    assert ",GC_01_GATE," in wait and wait.endswith("expr(GC_01_GATE.raised and GC_01_EYE.detect)")
    assert any(r.startswith("GC_01_RUN_DO,") and ",GC_01_BELT," in r for r in rows)
    assert any(r.startswith("GC_02_OPEN_DO,") and r.endswith("NC (inverted)") for r in rows)


def test_the_json_schema_describes_composite_definitions_and_instances():
    schema = json.loads(JSON_SCHEMA_PATH.read_text(encoding="utf-8"))
    defs = schema["$defs"]
    assert "composites" in schema["properties"]
    assert set(defs["CompositeDef"]["required"]) == {"type", "parts"}
    assert defs["CompositeIo"]["properties"].keys() >= {"to", "from", "invert"}
    items = schema["properties"]["components"]["items"]
    refs = json.dumps(items)
    assert "CompositeInstance" in refs and "BeltConveyor" in refs
    # a built-in component must match exactly one oneOf branch: the instance branch excludes them
    excluded = defs["CompositeInstance"]["properties"]["type"]["not"]["enum"]
    assert set(excluded) == set(COMPONENT_MODELS)


def test_the_scene_document_names_each_part_s_instance():
    doc = Runtime(load(BUFFER), realtime=False).scene_document()
    marked = {c["id"]: c.get("composite") for c in doc["components"]}
    assert marked["BUF_GATE"] == {"id": "BUF", "type": "gated_buffer"}
    assert marked["CONV_OUT"] is None


# --- lint ---------------------------------------------------------------------------------------


def test_lint_w208_a_composite_with_no_io(tmp_path):
    data = gated_data()
    data["composites"].append({
        "type": "drop_chute", "description": "Two removers side by side",
        "parts": [{"id": "left", "type": "part_remover", "position": [0, 0, -0.3],
                   "size": [0.3, 1, 0.3]},
                  {"id": "right", "type": "part_remover", "position": [0, 0, 0.3],
                   "size": [0.3, 1, 0.3]}],
        "io": {},
    })  # fmt: skip
    data["components"].append(
        {"id": "CHUTE", "type": "drop_chute", "description": "Spill", "position": [8, 0, 0]}
    )
    scene = tmp_path / "w208.yaml"
    scene.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    w208 = [f for f in lint_scene(scene) if f.code == "W208"]
    assert len(w208) == 1 and "composite drop_chute exposes no I/O" in w208[0].message
    assert not [f for f in lint_scene(GATED) if f.code == "W208"]


def test_lint_w205_catches_overlapping_composite_parts(tmp_path):
    data = gated_data()
    data["components"][2]["position"] = [1.0, 0, 0]  # GC_02 now starts on top of GC_01
    data["connections"] = []
    scene = tmp_path / "w205.yaml"
    scene.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    w205 = [f.message for f in lint_scene(scene) if f.code == "W205"]
    assert w205 == [
        "conveyors GC_01_BELT (part of GC_01, a gated_conveyor) and GC_02_BELT (part of GC_02, "
        "a gated_conveyor) overlap by 1 m x 0.5 m at the same height"
    ]
