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


def test_the_sample_passes_check_and_strict_fails_on_its_open_questions() -> None:
    done = run(str(SAMPLE))
    assert done.exit_code == 0, done.output
    assert "32 rows (13 equipment, 4 connections, 15 I/O)" in done.output
    assert "WARN  EQ-5 (L-001): AG-101 has type unknown" in done.output
    assert "checked: current (XX, 2026-11-20)" in done.output
    strict = run(str(SAMPLE), "--strict")
    assert strict.exit_code == 1
    assert (
        "FAIL (--strict): 11 rows have open questions: EQ-1, EQ-3, EQ-4, EQ-5, EQ-7, EQ-8, "
        "EQ-10, EQ-11, EQ-12, EQ-13, IO-15" in strict.output
    )
    assert (
        tk.rows_digest(sample()) == "366010d67977895a"
    )  # the mark is current (re-marked 2026-11-20)


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
    text = text.replace("rows: 366010d67977895a}", "rows: ffffffffffffffff}")
    path.write_bytes(text.encode("utf-8"))
    assert "checked: stale, rows changed since checking" in run(str(path)).output
    new = tk.write_mark(tk.load(path), "QA", dt.date(2026, 11, 16))
    head, tail = text.split("checked: ", 1)
    after = tail.split("}\n", 1)[1]
    assert new.startswith(head) and new.endswith(after)  # every other byte as it was
    assert (
        "checked: {by: QA, date: 2026-11-16, drawings: [P-001@A, L-001@A, IO-001@A], rows: "
        "366010d67977895a}\n" in new
    )
    done = run(str(path), "--mark", "QA")
    assert done.exit_code == 0 and "checked: current (QA, " in done.output
    assert tk.load(path).model.checked.rows == "366010d67977895a"
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
    edited = edited.replace("366010d67977895a", digest)
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
    row(data, "EQ-5")["params"]["note"] = "{__import__('os')}"  # a literal, never evaluated
    assert not [f for f in findings(tmp_path, data) if f.level == "error"]
    data = sample()
    row(data, "EQ-3")["params"]["diameter"] = "{__import__('os')}"  # a wrong type, reported
    assert [str(f) for f in findings(tmp_path, data) if f.level == "error"] == [
        "ERROR EQ-3 (L-001): params.diameter: Input should be a valid number, unable to parse "
        "string as a number"
    ]


def test_the_json_schema_is_current_and_refuses_unknown_keys() -> None:
    assert tk.main(["--check"]) == 0
    schema = json.loads(tk.JSON_SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["Equipment"]["additionalProperties"] is False


# --- review fixes, ruled 2026-11-17 -----------------------------------------------------------

ONE_LINE_FIELDS = [  # (row, the edit) for each field the ruling names
    ("EQ-4", lambda r, v: r.update(tag="LT-101" + v)),
    ("EQ-4", lambda r, v: r["ref"][0].update(grid="C3" + v)),
    ("CN-2", lambda r, v: r.update(line="25-W-101" + v)),
    ("EQ-4", lambda r, v: r.update(questions=["Is it?" + v])),
    ("EQ-10", lambda r, v: r["mount"].update(rail="left" + v)),
    ("EQ-10", lambda r, v: r["mount"].update(conveyor="CV-201" + v)),
    ("CN-2", lambda r, v: r.update({"from": "LV-101" + v})),
    ("CN-2", lambda r, v: r.update(to="TK-101.top" + v)),
    ("EQ-4", lambda r, v: r.update(description="Level" + v)),
]


@pytest.mark.parametrize("brk", ["\n  - id: X", "\r", "\t", "\x85", " ", " ", "\x00"])
@pytest.mark.parametrize(("rid", "edit"), ONE_LINE_FIELDS)
def test_text_with_a_line_break_or_control_character_is_an_error_naming_its_row(
    tmp_path, rid, edit, brk
) -> None:
    data = sample()
    edit(row(data, rid), brk)
    errors = [f for f in findings(tmp_path, data) if f.level == "error"]
    assert errors and all(f.row == rid for f in errors), errors
    assert any("a takeoff's text is one line" in f.message for f in errors), errors


def _text_with(old: str, new: str) -> str:
    text = SAMPLE.read_text(encoding="utf-8")
    assert text.count(old) == 1
    return text.replace(old, new)


def test_a_key_written_twice_is_refused_naming_the_key_and_its_line(tmp_path) -> None:
    path = tmp_path / "t.takeoff.yaml"
    text = _text_with("x: 3700,", "x: 3700, x: 9999,")
    line = text[: text.index("x: 9999")].count("\n") + 1
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match=rf"line {line}: key 'x' appears twice in one mapping"):
        tk.load(path)
    done = run(str(path))
    assert done.exit_code == 1 and "key 'x' appears twice" in done.output


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("plant:", "plant: &p", "anchors and aliases"),
        ("questions: []}\n  - {row: EQ-3", "questions: []}\n  - &a {row: EQ-3", "anchors"),
        ('takeoff: "0.1"', 'takeoff: "0.1"\nx: &a [1]\ny: *a', "anchors and aliases"),
        ("plant:", "<<: {extra: 1}\nplant:", "the merge key << is not allowed"),
    ],
)
def test_anchors_aliases_and_merge_keys_are_refused(tmp_path, old, new, message) -> None:
    path = tmp_path / "t.takeoff.yaml"
    path.write_text(_text_with(old, new), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        tk.load(path)


def test_an_alias_chain_is_refused_before_it_expands(tmp_path) -> None:
    lines = ["a0: &a0 [x, x, x, x, x, x, x, x, x, x]"]
    lines += [f"a{i}: &a{i} [*a{i - 1}, *a{i - 1}, *a{i - 1}, *a{i - 1}]" for i in range(1, 30)]
    path = tmp_path / "t.takeoff.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="line 1: anchors and aliases"):
        tk.load(path)


@pytest.mark.parametrize(
    ("rid", "params", "message"),
    [
        ("EQ-9", {"lenght": 3.0}, "params.lenght: a belt_conveyor has no parameter 'lenght'"),
        ("EQ-9", {"length": "long"}, "params.length: Input should be a valid number"),
        ("EQ-3", {"position": [9, 9, 9]}, "params.position: set by the row itself"),
        ("EQ-3", {"id": "TK_999"}, "params.id: set by the row itself"),
        ("EQ-3", {"type": "drain"}, "params.type: set by the row itself"),
        ("EQ-3", {"description": "x"}, "params.description: set by the row itself"),
        ("EQ-3", {"rotation_deg": 90}, "params.rotation_deg: set by the row itself"),
        ("EQ-3", {"mesh": {"file": "x.glb"}}, "params.mesh: set by the row itself"),
        ("EQ-3", {"laid_out": True}, "params.laid_out: set by the row itself"),  # PLAN 5.5
        ("EQ-3", {"io": {}}, "params.io: set by the row itself"),
    ],
)
def test_params_are_checked_against_the_rows_type(tmp_path, rid, params, message) -> None:
    data = sample()
    row(data, rid)["params"].update(params)
    errors = [f for f in findings(tmp_path, data) if f.level == "error"]  # never a traceback
    assert [f.row for f in errors] == [rid], errors
    assert message in errors[0].message


def test_a_pipe_id_equal_to_an_equipment_rows_scene_id_is_an_error_naming_both(tmp_path) -> None:
    data = sample()
    row(data, "EQ-12")["id"] = "PIPE_101"  # CN-2 makes PIPE_101
    errors = [str(f) for f in findings(tmp_path, data) if f.level == "error"]
    assert errors == [
        "ERROR CN-2 (P-001): scaffold makes this line pipe PIPE_101, which is also the scene id "
        "of EQ-12 EMT-201; give EQ-12 another `id:`"
    ]


def test_plc_tags_that_differ_only_in_the_case_of_their_tag_name_are_duplicates(tmp_path) -> None:
    """The takeoff uses the scene's parser and key (PLAN 5.3a, 1756-PM004L-EN-P ch. 2)."""
    data = sample()
    row(data, "IO-2")["plc_tag"] = "lv101_cv"  # IO-1 is LV101_CV
    errors = [str(f) for f in findings(tmp_path, data) if f.level == "error"]
    assert errors == [
        "ERROR IO-2 (IO-001): duplicate plc_tag 'lv101_cv' (also row IO-1, 'LV101_CV' differing "
        "only in case)"
    ]
    row(data, "IO-2")["plc_tag"] = "LV101_POS.99"
    assert any("bit 99 is past bit 31" in f.message for f in findings(tmp_path, data))
