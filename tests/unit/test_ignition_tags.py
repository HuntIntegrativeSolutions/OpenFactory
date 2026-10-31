"""PLAN 2.4 Done-when, Ignition half: every shipped scene's Ignition 8.3 tag JSON validates
against the structure in docs/ignition.md (folder per area, UDT instance per component, types
first), covers every I/O list tag exactly once, and is deterministic."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openfactory.cli import app
from openfactory.core.loader import build_iotable
from openfactory.core.schema import load_scene
from openfactory.export.ignition_tags import ignition_tags, ignition_tags_json, pascal, udt_name

REPO = Path(__file__).resolve().parents[2]
SCENES = sorted(p for p in (REPO / "scenes").glob("*.yaml") if not p.name.endswith(".modbus.yaml"))
NSU = "nsu=urn:openfactory:sim;s="
ATOMIC_KEYS = {
    "name", "tagType", "valueSource", "opcServer", "opcItemPath", "dataType", "accessRights",
    "historyEnabled", "documentation", "enabled",
}  # fmt: skip


def check_atomic(tag: dict, kind: str) -> None:
    assert set(tag) == ATOMIC_KEYS, set(tag) ^ ATOMIC_KEYS
    assert tag["tagType"] == "AtomicTag" and tag["valueSource"] == "opc"
    assert tag["dataType"] in ("Boolean", "Float4")
    assert tag["accessRights"] == ("Read_Write" if kind in ("DO", "AO") else "Read_Only")
    assert tag["historyEnabled"] is (kind == "AI")
    assert tag["documentation"] and tag["enabled"] is True


def validate(doc: dict, scene) -> dict[str, str]:
    """Hold ``doc`` to docs/ignition.md; return {scene tag: its Ignition path}."""
    table = build_iotable(scene)
    assert set(doc) == {"version", "tags"} and doc["version"] == "8.3.0"
    types_folder, area = doc["tags"]
    assert (types_folder["name"], types_folder["tagType"]) == ("_types_", "Folder")
    assert area["tagType"] == "Folder" and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", area["name"])
    types = {t["name"]: t for t in types_folder["tags"]}
    kinds = {}
    for udt in types.values():
        assert udt["tagType"] == "UdtType" and udt["name"].startswith("OF_")
        members = {m["name"]: m for m in udt["tags"]}
        assert set(udt["parameters"]) == {"InstanceName"} | {f"Tag_{m}" for m in members}
        for name, member in members.items():
            assert member["opcItemPath"] == f"{NSU}{{Tag_{name}}}"
        kinds[udt["name"]] = members

    paths: dict[str, str] = {}
    components = {c.id: c for c in scene.components}
    for item in area["tags"]:
        if item["tagType"] == "AtomicTag":  # the heartbeat
            assert item["name"] == "SIM_HEARTBEAT"
            check_atomic(item, "DI")
            paths["SIM_HEARTBEAT_DI"] = f"{area['name']}/SIM_HEARTBEAT"
            assert item["opcItemPath"] == f"{NSU}SIM_HEARTBEAT_DI"
            continue
        assert item["tagType"] == "UdtInstance"
        comp = components[item["name"]]  # one instance per component, named by its id
        assert item["typeId"] == udt_name(comp.type) and item["typeId"] in types
        assert item["documentation"] == comp.description
        params = {k: v["value"] for k, v in item["parameters"].items()}
        assert params.pop("InstanceName") == comp.id
        overrides = {o["name"]: o for o in item.get("tags", [])}
        for member, spec in kinds[item["typeId"]].items():
            tag_name = params[f"Tag_{member}"]
            if not tag_name:
                assert overrides[member] == {"name": member, "enabled": False}
                continue
            tag = table.tag(tag_name)
            assert tag.owner == comp.id
            check_atomic(spec, tag.kind)
            assert spec["dataType"] == ("Boolean" if tag.dtype == "bool" else "Float4")
            if tag.kind in ("AI", "AO") and tag.range:
                o = overrides[member]
                assert (o["engLow"], o["engHigh"]) == tag.range
            assert tag_name not in paths
            paths[tag_name] = f"{area['name']}/{comp.id}/{member}"
    assert set(paths) == {t.name for t in table.tags()}  # every I/O list tag, exactly once
    with_io = {b.component_id for b in scene.tag_bindings()}
    assert {i["name"] for i in area["tags"] if i["tagType"] == "UdtInstance"} == with_io
    return paths


@pytest.mark.parametrize("scene_path", SCENES, ids=lambda p: p.stem)
def test_every_shipped_scene_validates_against_the_documented_structure(scene_path):
    scene = load_scene(scene_path)
    doc = ignition_tags(scene)
    paths = validate(doc, scene)
    text = ignition_tags_json(scene)
    assert json.loads(text) == doc and text == ignition_tags_json(scene)  # deterministic
    print(f"{scene_path.stem}: {len(doc['tags'][0]['tags'])} UDTs, "
          f"{len(doc['tags'][1]['tags']) - 1} instances, {len(paths)} tags")  # fmt: skip


def test_sorter_details_and_the_logix_device_form():
    scene = load_scene(REPO / "scenes" / "sorting-by-height.yaml")
    doc = ignition_tags(scene)
    paths = validate(doc, scene)
    assert doc["tags"][1]["name"] == "SORTING_BY_HEIGHT"
    assert paths["CONV_01_RUN_DO"] == "SORTING_BY_HEIGHT/CONV_01/Run"
    assert paths["PE_01_DI"] == "SORTING_BY_HEIGHT/PE_01/Detect"
    assert [t["name"] for t in doc["tags"][0]["tags"]] == [
        "OF_BeltConveyor", "OF_BoxEmitter", "OF_PhotoEye", "OF_Pusher",
    ]  # fmt: skip
    logix = ignition_tags(scene, device="CLX_SIM", area="AREA1")
    member = logix["tags"][0]["tags"][0]["tags"][0]
    assert member["opcServer"] == "Ignition OPC UA Server"
    assert member["opcItemPath"] == "ns=1;s=[CLX_SIM]{Tag_Run}"
    assert logix["tags"][1]["tags"][-1]["opcItemPath"] == "ns=1;s=[CLX_SIM]SIM_HEARTBEAT_DI"
    with pytest.raises(ValueError, match="not a valid Ignition device name"):
        ignition_tags(scene, device="192.0.2.10")
    assert pascal("at_place") == "AtPlace"


def test_cli_exports_ignition_json(tmp_path):
    out = tmp_path / "tags.json"
    scene = REPO / "scenes" / "sorting-by-height.yaml"
    result = CliRunner().invoke(
        app, ["export", str(scene), "--format", "ignition", "--area", "AREA1", "-o", str(out)]
    )
    assert result.exit_code == 0, result.output
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["tags"][1]["name"] == "AREA1"
    validate(doc, load_scene(scene))
