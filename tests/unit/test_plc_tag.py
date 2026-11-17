"""PLAN 5.3a: the plant's tag names (``plc_tag``), ``scene.area``, ``plc.heartbeat_plc_tag``,
the exports by form, and W215."""

from __future__ import annotations

import csv
import io
import json
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from typer.testing import CliRunner

from openfactory.cli import app as cli
from openfactory.core.loader import build
from openfactory.core.plctag import parse_plc_tag
from openfactory.core.schema import Scene, load_scene
from openfactory.export.context_md import context_md
from openfactory.export.ignition_tags import ignition_tags
from openfactory.export.io_list_csv import COLUMNS, io_list_csv
from openfactory.export.l5x_stub import l5x_stub, undeclared_plc_tags
from openfactory.lint import lint_scene
from openfactory.observe import Plant, look
from openfactory.server.runtime import Runtime

REPO = Path(__file__).resolve().parents[2]
SORTER = REPO / "scenes" / "sorting-by-height.yaml"

#: Every binding of the sorter, a plant name of each form (the Done-when of 5.3a).
PLANT = {
    "CONV_01_RUN_DO": "Program:Sorter.Conv1_Run",  # program
    "CONV_01_SPD_AO": "Conv1_Speed",  # controller
    "CONV_01_RUN_DI": "Conv1.Running",  # member
    "PE_01_DI": "Sorter_DI_Word.4",  # bit
    "CYL_01_EXT_DO": "Pusher.Extend",
    "CYL_01_EXT_DI": "Pusher.Extended",
    "CYL_01_RET_DI": "Sorter_DI_Word.5",
    "EMT_01_EN_DO": "Sorter_DO_Word.0",
    "CONV_02_RUN_DO": "Conv2.Run",
    "CONV_02_SPD_AO": "Speeds[2]",  # element
    "CONV_02_RUN_DI": "Conv2.Running",
}
HEARTBEAT = "Sorter_DI_Word.0"


def mapped_doc(skip: str | None = None) -> dict:
    """The sorter with every binding given its plant name (all but ``skip``)."""
    doc = yaml.safe_load(SORTER.read_text(encoding="utf-8"))
    for comp in doc["components"]:
        for port, binding in (comp.get("io") or {}).items():
            spec = binding if isinstance(binding, dict) else {"tag": binding}
            if spec["tag"] != skip:
                spec = {**spec, "plc_tag": PLANT[spec["tag"]]}
            comp["io"][port] = spec
    doc["plc"]["heartbeat_plc_tag"] = HEARTBEAT
    return doc


def write(tmp_path: Path, doc: dict, name: str = "sorter-mapped.yaml") -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return path


ACCEPTED = {
    "LT101_PV": "controller",
    "_Spare": "controller",
    "a": "controller",
    "Program:Transfer.P101_Run": "program",
    "CV201.Running": "member",
    "CV201.Motor.Running": "member",
    "Program:Transfer.CV201.Running": "member",
    "Grid[3].Value": "member",
    "Word[3]": "element",
    "Grid[1,2]": "element",
    "Cube[1,2,3]": "element",
    "Line2_DI_Word.4": "bit",
    "Word[3].15": "bit",
    "CV201.Status.0": "bit",
    "Program:Transfer.Word.31": "bit",
}
REFUSED = [
    "",
    " LT101",
    "LT101 ",
    "1LT101",
    "LT-101",
    "LT 101",
    "LT101.",
    ".LT101",
    "LT101..PV",
    "Program:",
    "Program:Transfer",
    "Program:Transfer.",
    "Program:1X.Tag",
    "program:Transfer.Tag",
    "Word[]",
    "Word[1,2,3,4]",
    "Word[a]",
    "Word[-1]",
    "Word.4.Member",
    "Tag:Member",
    "Tag/Member",
]


@pytest.mark.parametrize(("name", "form"), sorted(ACCEPTED.items()))
def test_the_grammar_accepts(name: str, form: str) -> None:
    assert parse_plc_tag(name).form == form


@pytest.mark.parametrize("name", REFUSED)
def test_the_grammar_refuses(name: str) -> None:
    with pytest.raises(ValueError):
        parse_plc_tag(name)


def test_unchecked_until_the_manual_is_read() -> None:
    """The blocker under 5.3a: no length limit and no case rule are checked yet. These are
    accepted by shape alone and must be revisited when the vendor's rules are cited."""
    assert parse_plc_tag("A" * 60).form == "controller"
    assert parse_plc_tag("Tag__Name_").form == "controller"
    assert parse_plc_tag("Word.99").form == "bit"


def test_a_malformed_plc_tag_is_a_load_error_naming_it() -> None:
    doc = mapped_doc()
    doc["components"][0]["io"]["run"]["plc_tag"] = "Conv 1 Run"
    with pytest.raises(ValidationError, match="Conv 1 Run"):
        Scene.model_validate(doc)


def test_a_duplicate_plant_name_is_a_load_error_naming_both() -> None:
    doc = mapped_doc()
    doc["components"][0]["io"]["running"]["plc_tag"] = "Conv1_Speed"
    with pytest.raises(ValidationError, match=r"CONV_01\.io\.speed and CONV_01\.io\.running"):
        Scene.model_validate(doc)
    # a binding without one reaches the controller by its scene tag, which counts too
    doc = mapped_doc(skip="CONV_01_RUN_DI")
    doc["components"][0]["io"]["speed"]["plc_tag"] = "CONV_01_RUN_DI"
    with pytest.raises(ValidationError, match="names two points"):
        Scene.model_validate(doc)
    doc = mapped_doc()
    doc["plc"]["heartbeat_plc_tag"] = "Conv1.Running"
    with pytest.raises(ValidationError, match="plc.heartbeat_plc_tag"):
        Scene.model_validate(doc)


def test_the_mapped_sorter_exports_the_plant_names(tmp_path: Path) -> None:
    """Done-when: the CSV's plc_tag column equals the scene's; the L5X declares one controller
    tag and one program tag and lists the member, element and bit rows in its comment; the SCADA
    export's device-path parameters are the plant names."""
    path = write(tmp_path, mapped_doc())
    scene = load_scene(path)
    rows = list(csv.DictReader(io.StringIO(io_list_csv(scene, scene_path=path))))
    assert list(rows[0]) == [*COLUMNS, "plc_tag"]
    assert {r["tag"]: r["plc_tag"] for r in rows} == {**PLANT, "SIM_HEARTBEAT_DI": HEARTBEAT}

    text = l5x_stub(scene, scene_path=path)
    root = ET.fromstring(text.encode("utf-8"))
    controller = root.find("Controller")
    assert [t.get("Name") for t in controller.find("Tags")] == ["Conv1_Speed"]
    programs = controller.find("Programs")
    assert [(p.get("Name"), p.get("Use")) for p in programs] == [("Sorter", "Context")]
    assert [t.get("Name") for t in programs.find("Program").find("Tags")] == ["Conv1_Run"]
    assert controller.find("Tags").find("Tag").get("DataType") == "REAL"
    comment = text.split("<!--", 1)[1].split("-->", 1)[0]
    declared = ("controller", "program")
    undeclared = {n for n in PLANT.values() if parse_plc_tag(n).form not in declared}
    for name in [*undeclared, HEARTBEAT]:
        assert f"  {name} (" in comment
    assert "  Speeds[2] (element of Speeds) for CONV_02_SPD_AO" in comment
    assert "  Sorter_DI_Word.4 (bit of Sorter_DI_Word) for PE_01_DI" in comment
    assert len(undeclared_plc_tags(scene)) == len(undeclared) + 1 == 10
    assert text.index("<!--") < text.index("<RSLogix5000Content")

    doc = ignition_tags(scene, device="SorterPLC")
    instances = {i["name"]: i for i in doc["tags"][1]["tags"] if i["tagType"] == "UdtInstance"}
    assert instances["CONV_01"]["parameters"]["Tag_Run"]["value"] == "Program:Sorter.Conv1_Run"
    assert instances["PE_01"]["parameters"]["Tag_Detect"]["value"] == "Sorter_DI_Word.4"
    params = {
        v["value"]
        for i in instances.values()
        for k, v in i["parameters"].items()
        if k.startswith("Tag_")
    }
    assert params == set(PLANT.values())
    beat = next(t for t in doc["tags"][1]["tags"] if t["name"] == "SIM_HEARTBEAT")
    assert beat["opcItemPath"] == f"ns=1;s=[SorterPLC]{HEARTBEAT}"
    # OpenFactory's own OPC UA server: the scene tag stays, as the node name
    own = ignition_tags(scene)
    pe = next(i for i in own["tags"][1]["tags"] if i["name"] == "PE_01")
    assert pe["parameters"]["Tag_Detect"]["value"] == "PE_01_DI"

    context = context_md(scene, path)
    assert "| Tag | Plant tag | Kind |" in context
    assert "| `PE_01_DI` | `Sorter_DI_Word.4` | DI |" in context

    out = CliRunner().invoke(cli, ["export", str(path), "--format", "l5x"])
    assert out.exit_code == 0, out.output
    assert (
        "10 plant tags are members, elements or bits of the plant's own tags and are not "
        "declared here"
    ) in out.output


def test_removing_one_plc_tag_raises_w215_for_that_binding_only(tmp_path: Path) -> None:
    full = write(tmp_path, mapped_doc(), "full.yaml")
    shutil.copy(REPO / "examples" / "pyplc" / "sorting-by-height.py", tmp_path / "full.py")
    assert not [f for f in lint_scene(full) if f.code == "W215"]
    half = write(tmp_path, mapped_doc(skip="CYL_01_RET_DI"), "half.yaml")
    w215 = [f for f in lint_scene(half) if f.code == "W215"]
    assert len(w215) == 1
    assert w215[0].message.startswith("CYL_01_RET_DI (CYL_01.io.retracted) has no plc_tag while "
                                      "10 other bindings have one")  # fmt: skip


def test_a_scene_without_plc_tag_exports_as_before() -> None:
    scene = load_scene(SORTER)
    text = io_list_csv(scene, scene_path=SORTER, write_lock=False)
    assert text.splitlines()[0] == ",".join(COLUMNS)
    assert "Plant tag" not in context_md(scene, SORTER)
    assert "<!--" not in l5x_stub(scene, scene_path=SORTER, write_lock=False)
    assert not [f for f in lint_scene(SORTER) if f.code == "W215"]


def test_scene_area_is_the_default_prefix_and_never_touches_a_plant_name(tmp_path: Path) -> None:
    doc = mapped_doc(skip="EMT_01_EN_DO")
    doc["scene"]["area"] = "SORT1"
    path = write(tmp_path, doc)
    scene = load_scene(path)
    text = l5x_stub(scene, scene_path=path)
    root = ET.fromstring(text.encode("utf-8")).find("Controller")
    assert {t.get("Name") for t in root.find("Tags")} == {"Conv1_Speed", "SORT1_EMT_01_EN_DO"}
    assert ignition_tags(scene)["tags"][1]["name"] == "SORT1"
    assert ignition_tags(scene, area="OTHER")["tags"][1]["name"] == "OTHER"
    doc["scene"]["area"] = "sort1"
    with pytest.raises(ValidationError, match="area"):
        Scene.model_validate(doc)


def test_a_composite_instance_carries_plc_tag_on_its_command_and_pass_through_io() -> None:
    path = REPO / "scenes" / "examples" / "accumulation-demo.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    inst = next(c for c in doc["components"] if c["type"] == "accumulation_conveyor")
    inst["io"]["release"] = {"tag": "ACC_REL_DO", "plc_tag": "Acc.Release"}
    inst["io"]["zone1_eye"] = {"tag": "ACC_Z1_DI", "plc_tag": "Acc.Zone1"}
    scene = Scene.model_validate(doc, context={"scene_dir": path.parent})
    plant = {b.name: b.plc_tag for b in scene.tag_bindings()}
    assert plant["ACC_REL_DO"] == "Acc.Release" and plant["ACC_Z1_DI"] == "Acc.Zone1"
    assert plant["ACC_Z2_DI"] is None


def test_look_shows_the_plant_name_beside_the_tag(tmp_path: Path) -> None:
    path = write(tmp_path, mapped_doc())
    runtime = Runtime(build(load_scene(path)), realtime=False)
    for _ in range(10):
        runtime._step(runtime.scheduler.dt)
    document = runtime.scene_document(meshes=False, snaps=False)
    assert "area" not in document["scene"]
    text = look(document, runtime.snapshot, [], Plant())
    assert "PE_01_DI [plant Sorter_DI_Word.4] = false" in text
    assert f"SIM_HEARTBEAT_DI [plant {HEARTBEAT}] = " in text
    plain = Runtime(build(load_scene(SORTER)), realtime=False).scene_document(meshes=False)
    assert all("plc_tag" not in t for t in plain["tags"]) and "area" not in plain["scene"]


def test_the_json_schema_carries_the_new_keys() -> None:
    schema = json.loads((REPO / "web" / "public" / "scene.schema.json").read_text("utf-8"))
    text = json.dumps(schema)
    assert '"plc_tag"' in text and '"heartbeat_plc_tag"' in text and '"area"' in text
