"""PLAN 5.1b: ``openfactory takeoff scaffold`` (design note §3.4)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from openfactory import takeoff as tk
from openfactory.cli import app as cli
from openfactory.core.mounts import place_on_rail
from openfactory.core.schema import load_scene
from openfactory.lint import lint_scene
from openfactory.scaffold import OUTCOMES, scaffold

REPO = Path(__file__).resolve().parents[2]
SAMPLE = REPO / "docs" / "samples" / "wash-skid" / "wash-skid.takeoff.yaml"


def sample() -> dict:
    return yaml.safe_load(SAMPLE.read_text(encoding="utf-8"))


def from_data(tmp_path: Path, data: dict):
    path = tmp_path / "t.takeoff.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return scaffold(tk.load(path))


def counts(result) -> dict[str, int]:
    return {k: len(v) for k, v in result.tally().items()}


def test_the_sample_scaffolds_to_a_scene_that_validates_and_reports_every_row(tmp_path) -> None:
    """Done-when: 32 rows, 25 placed, 4 assumed, 3 stand-in; AG-101 a stand-in carrying IO-5
    and IO-6; exit 3."""
    out = tmp_path / "wash-skid.yaml"
    done = CliRunner().invoke(cli, ["takeoff", "scaffold", str(SAMPLE), "-o", str(out)])
    assert done.exit_code == 3, done.output
    assert "32 rows (13 equipment, 4 connections, 15 I/O)" in done.output
    assert "25 + 4 + 3 + 0 + 0 = 32: every row accounted for" in done.output
    scene = load_scene(out)  # validates
    result = scaffold(tk.load(SAMPLE))
    assert counts(result) == {
        "placed": 25, "assumed": 4, "stand-in": 3, "unplaced": 0, "not connected": 0
    }  # fmt: skip
    assert {o.row for o in result.tally()["assumed"]} == {"EQ-1", "EQ-8", "EQ-12", "EQ-13"}
    assert [o.row for o in result.tally()["stand-in"]] == ["EQ-5", "IO-5", "IO-6"]
    ag = next(c for c in scene.components if c.id == "AG_101_BODY")
    assert ag.type == "scenery" and scene.instance_type("AG_101") == "standin_ag_101"
    tags = {b.name: b.plc_tag for b in scene.tag_bindings()}
    assert tags["AG_101_RUN_DO"] == "AG101.Run" and tags["AG_101_RUN_DI"] == "AG101.Running"
    assert (
        "open questions, written as # QUESTION comments (6): EQ-1, EQ-5, EQ-8, EQ-12, "
        "EQ-13, IO-15" in done.output
    )
    for row in ("EQ-1", "EQ-5", "EQ-8", "EQ-12", "EQ-13", "IO-15"):
        assert f"# QUESTION ({row}):" in result.text


def test_the_scaffolded_scene_lints_with_only_no_plc_side_warnings(tmp_path) -> None:
    out = tmp_path / "wash-skid.yaml"
    out.write_text(scaffold(tk.load(SAMPLE)).text, encoding="utf-8")
    codes = {f.code for f in lint_scene(out)}
    assert codes <= {"W201", "W202"} and codes


def test_devices_sit_where_place_on_rail_puts_them() -> None:
    model = tk.load(SAMPLE).model
    scene = load_scene_text(scaffold(tk.load(SAMPLE)).text)
    comps = {c.id: c for c in scene.components}
    belt = comps["CV_201"]
    for cid, along, kind in (("PE_201", 1.2, "photo_eye"), ("CYL_201", 2.0, "pusher")):
        placed = place_on_rail(kind, {}, belt, "rail_left", along, tk.tallest_part(model))
        assert tuple(comps[cid].position) == pytest.approx(placed.position, abs=1e-6)
        assert comps[cid].rotation_deg == pytest.approx(placed.rotation_deg)
    assert tuple(comps["PE_201"].position) == pytest.approx((1.2, 0.1, 2.5))
    assert tuple(comps["CYL_201"].position) == pytest.approx((2.0, 0.1375, 2.2))


def load_scene_text(text: str):
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "s.yaml"
        path.write_text(text, encoding="utf-8")
        return load_scene(path)


def test_the_positions_line() -> None:
    report = scaffold(tk.load(SAMPLE)).report()
    assert ("positions: 9 from L-001 dimensions (EQ-1, EQ-2, EQ-3, EQ-4, EQ-5, EQ-6, EQ-7, EQ-8, "
            "EQ-9); 2 on CV-201's left rail (L-001) (EQ-10, EQ-11); 2 in scene metres with no "
            "layout sheet (EQ-12 EMT-201, EQ-13 REM-201)") in report  # fmt: skip


def test_removing_a_type_adds_one_stand_in_and_never_removes_a_row(tmp_path) -> None:
    for tag in ("LT-101", "PE-201"):  # one on the layout, one on a rail
        data = sample()
        item = next(e for e in data["equipment"] if e["tag"] == tag)
        del item["type"]
        result = from_data(tmp_path, data)
        before = scaffold(tk.load(SAMPLE))
        assert len(result.outcomes) == len(before.outcomes) == 32
        equip = [o for o in result.tally()["stand-in"] if o.row.startswith("EQ-")]
        assert len(equip) == 2, tag  # AG-101 and this one
        assert load_scene_text(result.text) is not None


def test_two_runs_write_the_same_bytes(tmp_path) -> None:
    a, b = tmp_path / "a.yaml", tmp_path / "b.yaml"
    for out in (a, b):
        CliRunner().invoke(cli, ["takeoff", "scaffold", str(SAMPLE), "-o", str(out)])
    assert a.read_bytes() == b.read_bytes()


def test_it_refuses_an_existing_scene_scenes_folder_and_an_invalid_takeoff(tmp_path) -> None:
    runner = CliRunner()
    out = tmp_path / "s.yaml"
    out.write_text("keep me\n", encoding="utf-8")
    done = runner.invoke(cli, ["takeoff", "scaffold", str(SAMPLE), "-o", str(out)])
    assert done.exit_code == 1 and out.read_text(encoding="utf-8") == "keep me\n"
    assert runner.invoke(cli, ["takeoff", "scaffold", str(SAMPLE), "-o", str(out),
                               "--force"]).exit_code == 3  # fmt: skip
    inside = REPO / "scenes" / "wash-skid-should-not-exist.yaml"
    done = runner.invoke(cli, ["takeoff", "scaffold", str(SAMPLE), "-o", str(inside)])
    assert done.exit_code == 1 and not inside.exists() and "--in-scenes" in done.output
    data = sample()
    data["equipment"][2]["type"] = "vat"
    bad = tmp_path / "bad.takeoff.yaml"
    bad.write_text(yaml.safe_dump(data), encoding="utf-8")
    fresh = tmp_path / "fresh.yaml"
    done = runner.invoke(cli, ["takeoff", "scaffold", str(bad), "-o", str(fresh)])
    assert done.exit_code == 1 and not fresh.exists()


def test_unplaced_and_not_connected_rows_are_reported_not_dropped(tmp_path) -> None:
    data = sample()
    item = next(e for e in data["equipment"] if e["tag"] == "FT-101")
    del item["layout"]  # no position: the holding row
    data["connections"].append({"row": "CN-5", "from": "CV-201", "to": "TK-101.top",
                                "confidence": "read"})  # fmt: skip
    result = from_data(tmp_path, data)
    assert counts(result)["unplaced"] == 1 and counts(result)["not connected"] == 1
    assert sum(counts(result).values()) == 33 == len(result.outcomes)
    assert "# NOT CONNECTED (takeoff CN-5): CV-201 -> TK-101.top" in result.text
    scene = load_scene_text(result.text)
    ft = next(c for c in scene.components if c.id == "FT_101")
    assert ft.position[0] == pytest.approx(3.15 + 1.5)  # past the far edge (REM-201 at 3.15)
    assert set(OUTCOMES) == set(counts(result))


def test_a_clean_takeoff_exits_0(tmp_path) -> None:
    data = sample()
    keep = {"CV-201", "PE-201"}
    data["equipment"] = [e for e in data["equipment"] if e["tag"] in keep]
    data["connections"] = []
    data["io"] = [r for r in data["io"] if r["equipment"] in keep]
    out = tmp_path / "clean.yaml"
    path = tmp_path / "clean.takeoff.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    done = CliRunner().invoke(cli, ["takeoff", "scaffold", str(path), "-o", str(out)])
    assert done.exit_code == 0, done.output
