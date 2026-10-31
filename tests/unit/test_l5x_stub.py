"""PLAN 2.3 Done-when: the sorter exports an L5X whose tag count equals the I/O list row count
(the scene's 11 bound tags plus SIM_HEARTBEAT_DI), well-formed, a controller-scoped partial
import (SoftwareRevision 35.00, ContainsContext true), BOOL and REAL tags with descriptions, and
the same bytes every time."""

from __future__ import annotations

import csv
import io
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openfactory.cli import app
from openfactory.core.schema import load_scene
from openfactory.export.io_list_csv import io_list_csv
from openfactory.export.l5x_stub import l5x_stub

REPO = Path(__file__).resolve().parents[2]
SORTER = REPO / "scenes" / "sorting-by-height.yaml"
SCENES = sorted(p for p in (REPO / "scenes").glob("*.yaml") if not p.name.endswith(".modbus.yaml"))


def tags_of(text: str) -> list[ET.Element]:
    root = ET.fromstring(text.encode("utf-8"))
    return root.findall("./Controller/Tags/Tag")


def test_sorter_l5x_has_one_tag_per_io_list_row():
    scene = load_scene(SORTER)
    text = l5x_stub(scene, scene_path=SORTER, write_lock=False)
    csv_text = io_list_csv(scene, scene_path=SORTER, write_lock=False)
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    tags = tags_of(text)
    print(f"sorter: {len(scene.tag_bindings())} bound tags + SIM_HEARTBEAT_DI = {len(rows)} I/O "
          f"list rows; L5X tags {len(tags)}")  # fmt: skip
    assert len(scene.tag_bindings()) == 11 and len(rows) == 12 and len(tags) == 12
    assert [t.get("Name") for t in tags] == [r["tag"] for r in rows]
    assert [t.get("Name") for t in tags].count("SIM_HEARTBEAT_DI") == 1

    root = ET.fromstring(text.encode("utf-8"))
    assert root.tag == "RSLogix5000Content"
    assert root.get("SchemaRevision") == "1.0" and root.get("SoftwareRevision") == "35.00"
    assert root.get("ContainsContext") == "true" and root.get("TargetType") == "Controller"
    assert root.get("ExportDate") is None  # deterministic
    controller = root.find("Controller")
    assert controller is not None and controller.get("Use") == "Context"
    by_name = {t.get("Name"): t for t in tags}
    for row in rows:
        tag = by_name[row["tag"]]
        want = {"bool": ("BOOL", "Decimal"), "float32": ("REAL", "Float")}[row["dtype"]]
        assert (tag.get("DataType"), tag.get("Radix")) == want
        assert tag.get("TagType") == "Base" and tag.get("ExternalAccess") == "Read/Write"
        description = tag.findtext("Description")
        assert description and description.startswith(row["description"])
    assert by_name["CONV_01_SPD_AO"].findtext("Description").endswith("[0 to 100 %]")
    assert "<![CDATA[" in text


@pytest.mark.parametrize("scene_path", SCENES, ids=lambda p: p.stem)
def test_every_shipped_scene_exports_valid_unique_logix_names(scene_path):
    text = l5x_stub(load_scene(scene_path), scene_path=scene_path, write_lock=False)
    names = [t.get("Name") for t in tags_of(text)]
    assert len(names) == len(set(names))
    assert all(re.fullmatch(r"[A-Z][A-Z0-9_]{0,39}", n) for n in names)
    assert text == l5x_stub(load_scene(scene_path), scene_path=scene_path, write_lock=False)


def test_cli_writes_the_l5x_where_told_and_rejects_a_bad_controller(tmp_path):
    out = tmp_path / "sorter-tags.L5X"
    runner = CliRunner()
    ok = runner.invoke(app, ["export", str(SORTER), "--format", "l5x", "-o", str(out)])
    assert ok.exit_code == 0, ok.output
    assert len(tags_of(out.read_text(encoding="utf-8"))) == 12
    bad = runner.invoke(
        app, ["export", str(SORTER), "--format", "l5x", "--controller", "1bad name"]
    )
    assert bad.exit_code == 1 and "not a valid Logix name" in bad.output


def test_prefix_changes_names_and_descriptions_and_nothing_else():
    scene = load_scene(SORTER)
    plain = l5x_stub(scene, scene_path=SORTER, write_lock=False)
    prefixed = l5x_stub(scene, scene_path=SORTER, write_lock=False, prefix="SIM")
    names = [t.get("Name") for t in tags_of(prefixed)]
    assert "SIM_PE_01_DI" in names and all(n.startswith("SIM_") for n in names)
    assert tags_of(prefixed)[0].findtext("Description").startswith("SIM — ")
    undone = prefixed.replace('Name="SIM_', 'Name="').replace("<![CDATA[SIM — ", "<![CDATA[")
    assert undone == plain  # otherwise unchanged
    assert l5x_stub(scene, scene_path=SORTER, write_lock=False, prefix="SIM_") == prefixed
    for bad in ("sim", "1A", "A-B"):
        with pytest.raises(ValueError, match="use capitals and digits"):
            l5x_stub(scene, scene_path=SORTER, write_lock=False, prefix=bad)
    long = "A" * 30  # 30 + "_" + "CONV_01_RUN_DI" > 40 characters
    with pytest.raises(ValueError, match="not a valid Logix tag name"):
        l5x_stub(scene, scene_path=SORTER, write_lock=False, prefix=long)
    out = CliRunner().invoke(app, ["export", str(SORTER), "--format", "l5x", "--prefix", "SIM"])
    assert out.exit_code == 0 and 'Name="SIM_PE_01_DI"' in out.output
