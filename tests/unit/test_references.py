"""PLAN 4.1f Done-when: for each kind of reference, deleting a referenced component in the editor
removes the reference and the scene still loads, or the preview names the reference left to fix."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from openfactory.core.references import names_of, references
from openfactory.core.sceneedit import apply_ops, undo
from openfactory.core.schema import parse_scene
from openfactory.server.editor import EditSession

REPO = Path(__file__).resolve().parents[2]


def delete(text: str, scene_dir: Path, comp_id: str) -> tuple[str, list]:
    """What the editor sends on a confirmed Delete: delete, then unreference."""
    ids = names_of(text, scene_dir, comp_id)
    ops = [{"op": "delete", "id": comp_id}, {"op": "unreference", "ids": ids}]
    new, inverses = apply_ops(text, ops)
    assert undo(new, inverses) == text  # byte for byte back
    return new, ids


def shipped(name: str) -> tuple[str, Path]:
    path = REPO / "scenes" / f"{name}.yaml"
    return path.read_text(encoding="utf-8"), path.parent


def test_a_connection_naming_it_goes_and_the_scene_loads():
    text, where = shipped("two-lane-merge")
    data = yaml.safe_load(text)
    victim = data["connections"][0]["from"]
    refs = references(data, [victim])
    assert [r.action for r in refs if r.path[0] == "connections"] == ["remove"]
    new, _ = delete(text, where, victim)
    scene = parse_scene(new, where)
    assert all(victim not in (c.from_, c.to) for c in scene.connections)
    assert len(scene.connections) == len(data["connections"]) - 1


def test_the_last_connection_takes_the_block_with_it():
    text, where = shipped("buffer-and-release")
    assert len(yaml.safe_load(text)["connections"]) == 1
    new, _ = delete(text, where, "CONV_OUT")
    assert "connections" not in yaml.safe_load(new)
    parse_scene(new, where)  # a bare `connections:` would not load


def test_a_composite_instance_takes_the_connections_naming_its_parts():
    text, where = shipped("buffer-and-release")
    assert "BUF_BELT" in names_of(text, where, "BUF")
    new, ids = delete(text, where, "BUF")
    assert "connections" not in yaml.safe_load(new)  # its one entry named BUF_BELT
    parse_scene(new, where)


def test_check_entries_naming_it_go_and_the_scene_loads():
    text, where = shipped("sorting-by-height")
    assert "REM_02" in yaml.safe_load(text)["checks"]["parts_reached"]
    new, _ = delete(text, where, "REM_02")
    checks = yaml.safe_load(new)["checks"]
    assert "REM_02" not in checks["parts_reached"]
    assert "REM_01" in checks["parts_reached"]
    parse_scene(new, where)


def test_a_check_left_empty_goes_too():
    text, where = shipped("two-lane-merge")
    new, _ = delete(text, where, "REM_OUT")  # the only remover in parts_reached
    checks = yaml.safe_load(new)["checks"]
    assert "parts_reached" not in checks
    assert checks["none_lost"] is True  # the rest of the block stays
    parse_scene(new, where)


def test_a_tank_takes_its_checks_and_leaves_its_pipes_and_instruments_to_fix():
    text, where = shipped("batch-mix-heat")
    data = yaml.safe_load(text)
    refs = references(data, ["TK_01"])
    removed = {r.text for r in refs if r.action == "remove"}
    assert {
        "check fractions_within: TK_01",
        "check temperature_within: TK_01",
        "check level_within: TK_01",
        "check never_overflow: TK_01",
    } <= removed
    fix = [r.text for r in refs if r.action == "fix"]
    assert any(".measures names TK_01.level" in f for f in fix), fix
    assert any(".tank names TK_01" in f for f in fix), fix
    assert any(".from names TK_01" in f or ".to names TK_01" in f for f in fix), fix
    new, _ = delete(text, where, "TK_01")
    after = yaml.safe_load(new)
    assert not any("TK_01" in str(v) for v in (after.get("checks") or {}).values())
    with pytest.raises(Exception, match="TK_01"):  # the load error names what is left to fix
        parse_scene(new, where)


def test_one_item_of_a_flow_list_goes():
    text, where = shipped("tank-level-onoff")
    assert "never_overflow: [TK_01, TK_02]" in text
    new, _ = apply_ops(text, [{"op": "unreference", "ids": ["TK_02"]}])
    assert "never_overflow: [TK_01]" in new
    new, _ = apply_ops(text, [{"op": "unreference", "ids": ["TK_01"]}])
    assert "never_overflow: [TK_02]" in new


def test_a_pump_takes_never_dry_and_max_starts():
    text, where = shipped("tank-level-onoff")
    new, _ = delete(text, where, "P_01")
    checks = yaml.safe_load(new)["checks"]
    assert "never_dry" not in checks
    assert "P_01" not in (checks.get("max_starts") or {})


def test_a_drive_and_a_combined_input_are_left_to_fix():
    data = {
        "components": [
            {"id": "CONV_01", "type": "belt_conveyor"},
            {"id": "VFD_01", "type": "vfd_motor", "drives": "CONV_01"},
            {"id": "PE_01", "type": "photo_eye"},
            {"id": "PE_02", "type": "photo_eye",
             "io": {"detect": {"tag": "X_DI", "from": ["PE_02.detect", "PE_01.detect"]}}},
        ]
    }  # fmt: skip
    assert [r.text for r in references(data, ["CONV_01"])] == ["VFD_01.drives names CONV_01"]
    combined = [r.text for r in references(data, ["PE_01"])]
    assert combined == ["PE_02.io.detect combines PE_01.detect"]
    assert all(r.action == "fix" for r in references(data, ["CONV_01", "PE_01"]))


def test_the_edit_server_lists_the_references(tmp_path):
    for f in (REPO / "scenes").glob("sorting-by-height*"):
        shutil.copy(f, tmp_path / f.name)
    session = EditSession(tmp_path / "sorting-by-height.yaml")
    listed = session.references(session.base, [], "REM_02")
    assert listed["ids"] == ["REM_02"]
    assert [r["text"] for r in listed["remove"]] == ["check parts_reached: REM_02"]
    assert listed["fix"] == []
    with pytest.raises(ValueError, match="no component"):
        session.references(session.base, [], "NOPE_01")
    ops = [{"op": "delete", "id": "REM_02"}, {"op": "unreference", "ids": listed["ids"]}]
    preview = session.preview(session.base, ops)
    assert preview["scene"] is not None
    assert not [f for f in preview["findings"] if f["level"] == "error"]
