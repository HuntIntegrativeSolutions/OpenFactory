"""PLAN 3.4: `openfactory export SCENE --format plantpax`, the PlantPAx demo L5X. The file is
well-formed, carries the scene's I/O tags and one instance per mapped object with no <Data>, its
routines name only members the source table cites (and the table nothing they do not use), every
tag a rung names is declared, and docs/plantpax.md lists the table as the code has it."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from openfactory.core.schema import load_scene
from openfactory.export import plantpax
from openfactory.export.l5x_stub import l5x_stub

REPO = Path(__file__).resolve().parents[2]
SCENES = {name: REPO / "scenes" / f"{name}.yaml" for name in plantpax.MAPS}


def export(name: str, tmp_path: Path) -> ET.Element:
    scene = tmp_path / f"{name}.yaml"  # the export writes lockfiles next to the scene: a copy
    scene.write_text(SCENES[name].read_text(encoding="utf-8"), encoding="utf-8")
    return ET.fromstring(plantpax.plantpax_l5x(load_scene(scene), scene_path=scene))


@pytest.mark.parametrize("name", sorted(plantpax.MAPS))
def test_the_file_is_a_partial_import_with_the_io_tags_and_the_instances(name, tmp_path):
    root = export(name, tmp_path)
    assert root.tag == "RSLogix5000Content"
    assert (root.get("SoftwareRevision"), root.get("ContainsContext")) == ("35.00", "true")
    tags = {t.get("Name"): t for t in root.iter("Tag")}
    stub = ET.fromstring(l5x_stub(load_scene(SCENES[name]), write_lock=False))
    assert {t.get("Name") for t in stub.iter("Tag")} <= set(tags)  # the I/O, as --format l5x
    for inst in plantpax.MAPS[name].instances:
        tag = tags[inst.tag]
        assert tag.get("DataType") == inst.aoi
        assert tag.find("Data") is None  # the library's defaults; Init writes the configuration
        assert inst.isa in tag.findtext("Description")
    assert all(re.fullmatch(r"[A-Z][A-Z0-9_]{0,39}", n) for n in tags)
    program = root.find("Controller/Programs/Program")
    assert program.get("MainRoutineName") == "MainRoutine"
    routines = [r.get("Name") for r in program.iter("Routine")]
    assert routines == ["MainRoutine", "Init", "Control"]


@pytest.mark.parametrize("name", sorted(plantpax.MAPS))
def test_every_rung_is_commented_balanced_and_names_declared_tags(name, tmp_path):
    root = export(name, tmp_path)
    declared = {t.get("Name") for t in root.iter("Tag")}
    for rung in root.iter("Rung"):
        text, comment = rung.findtext("Text"), rung.findtext("Comment")
        assert comment and text.endswith(";")
        assert text.count("(") == text.count(")")
        for word in re.findall(r"\b[A-Z][A-Z0-9_]*_(?:DI|DO|AI|AO)\b|\bDEMO_[A-Z0-9_]+", text):
            assert word in declared, (word, text)
    texts = [r.findtext("Text") for r in root.iter("Rung")]
    calls = [t for t in texts if re.match(r"P_\w+\(", t)]
    assert len(calls) == len(plantpax.MAPS[name].instances)  # each object called once


def test_the_source_table_is_exactly_the_members_the_routines_name():
    used = set().union(*(plantpax.members_used(m) for m in plantpax.MAPS.values()))
    assert used == set(plantpax.CITED)
    assert set(plantpax.WORKBOOK) == {aoi for aoi, _ in plantpax.CITED}


def test_a_scene_without_a_map_fails_and_says_which_have_one():
    scene = load_scene(REPO / "scenes" / "batch-mix-heat.yaml")
    with pytest.raises(ValueError, match="no PlantPAx map for scene 'batch-mix-heat'"):
        plantpax.plantpax_l5x(scene, write_lock=False)


def test_the_export_is_deterministic(tmp_path):
    a = plantpax.plantpax_l5x(load_scene(SCENES["plantpax-tank-level"]), write_lock=False)
    b = plantpax.plantpax_l5x(load_scene(SCENES["plantpax-tank-level"]), write_lock=False)
    assert a == b


def test_the_owners_page_lists_the_source_table_as_the_code_has_it():
    page = (REPO / "docs" / "plantpax.md").read_text(encoding="utf-8")
    assert plantpax.source_table() in page
