"""PLAN 5.1a: the takeoff file and ``openfactory takeoff check`` (design note §3.2, §3.3)."""

from __future__ import annotations

import copy
import datetime as dt
import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from openfactory import takeoff as tk
from openfactory.cli import app as cli

REPO = Path(__file__).resolve().parents[2]
SAMPLE = REPO / "docs" / "samples" / "wash-skid" / "wash-skid.takeoff.yaml"

#: Note §3.6: each row's scene position (L-001 converted, or place_on_rail, or at:).
SCENE = {
    "SUP-101": (-2.0, 2.4, 0.0), "LV-101": (-1.2, 2.4, 0.0), "TK-101": (0.0, 0.5, 0.0),
    "LT-101": (0.7, 1.4, 0.0), "AG-101": (0.0, 2.4, 0.0), "P-101": (1.2, 0.3, 0.0),
    "FT-101": (1.8, 0.6, 0.0), "DR-101": (2.6, 0.3, 0.0), "CV-201": (0.0, 0.0, 2.5),
    "PE-201": (1.2, 0.1, 2.5), "CYL-201": (2.0, 0.1375, 2.2), "EMT-201": (0.3, 0.155, 2.5),
    "REM-201": (3.15, 0.0, 2.5),
}  # fmt: skip


def sample() -> dict:
    return yaml.safe_load(SAMPLE.read_text(encoding="utf-8"))


def write(tmp_path: Path, data: dict, name: str = "t.takeoff.yaml") -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def findings(tmp_path: Path, data: dict) -> list[tk.Finding]:
    return tk.check(tk.load(write(tmp_path, data)))


def row(data: dict, rid: str) -> dict:
    return next(r for k in ("equipment", "connections", "io") for r in data[k] if r["row"] == rid)


def run(*args: str):
    return CliRunner().invoke(cli, ["takeoff", "check", *args])


def test_the_sample_passes_check_and_strict_fails_on_its_six_open_questions() -> None:
    done = run(str(SAMPLE))
    assert done.exit_code == 0, done.output
    assert "32 rows (13 equipment, 4 connections, 15 I/O)" in done.output
    assert "WARN  EQ-5 (L-001): AG-101 has type unknown" in done.output
    assert "checked: current (XX, 2026-11-15)" in done.output
    strict = run(str(SAMPLE), "--strict")
    assert strict.exit_code == 1
    assert (
        "FAIL (--strict): 6 rows have open questions: EQ-1, EQ-5, EQ-8, EQ-12, EQ-13, IO-15"
        in strict.output
    )
    assert tk.rows_digest(sample()) == "2c9c61b4ae244f3a"  # the note's mark is current


def _err(data: dict, rid: str, edit) -> dict:  # noqa: ANN001
    data = copy.deepcopy(data)
    edit(data, row(data, rid) if rid else None)
    return data


CASES = [  # (row named, sheet named, a fragment of the message, the edit)
    ("EQ-3", "L-001", "confidence", lambda d, r: r.update(confidence="sure")),
    (
        "EQ-3",
        "L-001",
        "colour: Extra inputs are not permitted",
        lambda d, r: r.update(colour="blue"),
    ),
    ("EQ-3", "L-001", "duplicate row 'EQ-3'", lambda d, r: r.update(row="EQ-3")),  # the second EQ-3
    ("EQ-4", "L-001", "duplicate tag 'TK-101'", lambda d, r: r.update(tag="TK-101")),
    ("EQ-4", "L-001", "duplicate scene id 'TK_101'", lambda d, r: r.update(tag="TK_101")),
    (
        "IO-2",
        "IO-001",
        "duplicate scene tag 'LV_101_POS_AO'",
        lambda d, r: r.update(scene_tag="LV_101_POS_AO"),
    ),
    ("IO-2", "IO-001", "duplicate plc_tag 'LV101_CV'", lambda d, r: r.update(plc_tag="LV101_CV")),
    ("EQ-3", "P-009", "ref names sheet P-009", lambda d, r: r["ref"].append({"sheet": "P-009"})),
    ("EQ-3", "L-001", "unknown type 'vat'", lambda d, r: r.update(type="vat")),
    (
        "CN-1",
        "P-001",
        "SUP-999 is not an equipment tag",
        lambda d, r: r.update(**{"from": "SUP-999"}),
    ),
    ("CN-3", "P-001", "a pump has no port 'inlet'", lambda d, r: r.update(to="P-101.inlet")),
    (
        "IO-3",
        "IO-001",
        "TK-999, which is not an equipment tag",
        lambda d, r: r.update(equipment="TK-999"),
    ),
    ("IO-3", "IO-001", "a tank has no port 'mid_sw'", lambda d, r: r.update(port="mid_sw")),
    ("IO-3", "IO-001", "TK-101.hi_sw is a DI port, not DO", lambda d, r: r.update(signal="DO")),
    (
        "IO-4",
        "IO-001",
        "is not a controller tag reference",
        lambda d, r: r.update(plc_tag="LT 101"),
    ),
    ("EQ-3", "L-001", "more than one position: layout, at", lambda d, r: r.update(at=[0, 0, 0])),
    ("EQ-3", "L-009", "layout names sheet L-009", lambda d, r: r["layout"].update(sheet="L-009")),
    (
        "EQ-1",
        "L-001",
        "layout sheet L-001 has no frame",
        lambda d, r: d["drawings"][1].pop("frame"),
    ),
    (
        "drawings",
        "L-001",
        "frame.units",
        lambda d, r: d["drawings"][1]["frame"].update(units="cubit"),
    ),
    (
        "drawings",
        "L-001",
        "frame.datum",
        lambda d, r: d["drawings"][1]["frame"].update(datum=[1, 2]),
    ),
    (
        "EQ-10",
        "L-001",
        "mount conveyor TK-101 is a tank",
        lambda d, r: r["mount"].update(conveyor="TK-101"),
    ),
    (
        "EQ-10",
        "L-001",
        "a transmitter is not placed on a rail",
        lambda d, r: r.update(
            type="transmitter", params={"measures": "TK-101.level", "range": [0, 1]}
        ),
    ),
    ("EQ-10", "L-001", "rail 'middle'", lambda d, r: r["mount"].update(rail="middle")),
    (
        "EQ-11",
        "L-001",
        "along 3400 mm is 3.4 m, outside CV-201 (0 to 3 m); it is not clamped",
        lambda d, r: r["mount"].update(along=3400),
    ),
    (
        "IO-3",
        "IO-001",
        "range and units are for an analog signal",
        lambda d, r: r.update(range=[0, 1]),
    ),
    (
        "IO-4",
        "IO-001",
        "scene tag 'LT_101_PV_AI_WITH_A_NAME_FAR_TOO_LONG_FOR_ANY_PLC_AI'",
        lambda d, r: r.update(scene_tag="LT_101_PV_AI_WITH_A_NAME_FAR_TOO_LONG_FOR_ANY_PLC_AI"),
    ),
]


@pytest.mark.parametrize(("rid", "sheet", "fragment", "edit"), CASES)
def test_each_error_names_its_row_and_sheet(tmp_path, rid, sheet, fragment, edit) -> None:
    targets = {"duplicate row 'EQ-3'": "EQ-4", "layout sheet L-001 has no frame": None}
    target = targets.get(fragment, rid if rid.startswith(("EQ", "CN", "IO")) else None)
    data = _err(sample(), target, edit)
    errors = [f for f in findings(tmp_path, data) if f.level == "error"]
    hit = [f for f in errors if fragment in f.message]
    assert hit, (fragment, [str(f) for f in errors])
    assert (hit[0].row, hit[0].sheet) == (rid, sheet), str(hit[0])
    assert run(str(tmp_path / "t.takeoff.yaml")).exit_code == 1


def test_the_two_warnings(tmp_path) -> None:
    out = findings(tmp_path, sample())
    assert [(f.level, f.row) for f in out] == [("warning", "EQ-5")]
    data = sample()
    row(data, "EQ-12")["ref"] = [{"sheet": "L-001", "grid": "A4"}]
    warns = [f for f in findings(tmp_path, data) if f.level == "warning"]
    assert any(f.row == "EQ-12" and f.sheet == "L-001" and "layout: as printed" in f.message
               for f in warns)  # fmt: skip


def test_mark_writes_the_mark_and_changes_no_other_byte(tmp_path) -> None:
    path = tmp_path / "w.takeoff.yaml"
    text = SAMPLE.read_text(encoding="utf-8")
    text = text.replace("rows: 2c9c61b4ae244f3a}", "rows: ffffffffffffffff}")
    path.write_bytes(text.encode("utf-8"))
    assert "checked: stale, rows changed since checking" in run(str(path)).output
    new = tk.write_mark(tk.load(path), "QA", dt.date(2026, 11, 16))
    head, tail = text.split("checked: ", 1)
    after = tail.split("}\n", 1)[1]
    assert new.startswith(head) and new.endswith(after)  # every other byte as it was
    assert (
        "checked: {by: QA, date: 2026-11-16, drawings: [P-001@A, L-001@A, IO-001@A], rows: "
        "2c9c61b4ae244f3a}\n" in new
    )
    done = run(str(path), "--mark", "QA")
    assert done.exit_code == 0 and "checked: current (QA, " in done.output
    assert tk.load(path).model.checked.rows == "2c9c61b4ae244f3a"
    # a takeoff with no mark gets one at the end, its other bytes kept
    bare = text.split("checked: ", 1)[0] + after
    path.write_text(bare, encoding="utf-8")
    assert run(str(path)).output.count("checked: missing") == 1
    assert run(str(path), "--mark", "QA").exit_code == 0
    assert path.read_text(encoding="utf-8").startswith(bare)
    # with an error, nothing is written
    broken = bare.replace("type: tank", "type: vat")
    path.write_text(broken, encoding="utf-8")
    assert run(str(path), "--mark", "QA").exit_code == 1
    assert path.read_text(encoding="utf-8") == broken


def test_a_row_edited_after_checking_fails_strict(tmp_path) -> None:
    text = SAMPLE.read_text(encoding="utf-8")
    edited = text.replace("x: 3700, y: 4000, elevation: 1400", "x: 3750, y: 4000, elevation: 1400")
    assert edited != text
    path = tmp_path / "e.takeoff.yaml"
    path.write_text(edited, encoding="utf-8")
    done = run(str(path), "--strict")
    assert done.exit_code == 1
    assert "FAIL (--strict): checked: stale, rows changed since checking" in done.output


def test_a_changed_sheet_revision_fails_strict_naming_the_sheet(tmp_path) -> None:
    text = SAMPLE.read_text(encoding="utf-8")
    path = tmp_path / "r.takeoff.yaml"
    # the rev changes and the mark's digest is renewed for it: only the revision is stale
    data = yaml.safe_load(text)
    data["drawings"][1]["rev"] = "B"
    digest = tk.rows_digest(data)
    edited = text.replace("title: Wash Water Skid and Reject Belt Layout, rev: A",
                          "title: Wash Water Skid and Reject Belt Layout, rev: B")  # fmt: skip
    edited = edited.replace("2c9c61b4ae244f3a", digest)
    path.write_text(edited, encoding="utf-8")
    done = run(str(path), "--strict")
    assert done.exit_code == 1
    assert "checked: stale, L-001 is rev B, checked at rev A" in done.output
    assert "rows changed" not in done.output


def test_positions_convert_within_1_mm_and_the_same_from_a_turned_sheet(tmp_path) -> None:
    model = tk.load(SAMPLE).model
    for e in model.equipment:
        pos, heading = tk.position_of(e, model)
        assert pos == pytest.approx(SCENE[e.tag], abs=1e-3), e.tag
        assert heading == 0.0
    # the same plant on a sheet turned 90 degrees: x' = y, y' = -x, belts drawn up the sheet
    data = sample()
    data["drawings"][1]["frame"]["rotation_deg"] = 90
    for e in data["equipment"]:
        lay = e.get("layout")
        if lay:
            lay["x"], lay["y"] = lay["y"], -lay["x"]
            lay["heading_deg"] = -90
    turned = tk.load(write(tmp_path, data)).model
    assert not [f for f in tk.check(tk.load(tmp_path / "t.takeoff.yaml")) if f.level == "error"]
    for e in turned.equipment:
        pos, heading = tk.position_of(e, turned)
        assert pos == pytest.approx(SCENE[e.tag], abs=1e-3), e.tag
        assert heading == pytest.approx(0.0, abs=1e-9), e.tag


def test_a_takeoff_is_data_never_code(tmp_path) -> None:
    path = tmp_path / "x.takeoff.yaml"
    path.write_text("takeoff: '0.1'\nplant: !!python/object/apply:os.system ['echo hi']\n",
                    encoding="utf-8")  # fmt: skip
    with pytest.raises(yaml.YAMLError):
        tk.load(path)
    path.write_bytes(b"#" * (tk.MAX_BYTES + 1))
    with pytest.raises(ValueError, match="at most"):
        tk.load(path)
    data = sample()
    data["extra"] = 1
    assert any("extra" in f.message for f in findings(tmp_path, data))
    data = sample()
    row(data, "EQ-3")["params"]["diameter"] = "{__import__('os')}"  # a literal, never evaluated
    assert not [f for f in findings(tmp_path, data) if f.level == "error"]


def test_the_json_schema_is_current_and_refuses_unknown_keys() -> None:
    assert tk.main(["--check"]) == 0
    schema = json.loads(tk.JSON_SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["Equipment"]["additionalProperties"] is False
