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


CONDITIONS = {"XIC", "XIO", "ONS", "OSR", "OSF", "EQU", "NEQ", "GRT", "GEQ", "LES", "LEQ", "LIM",
              "MEQ", "CMP"}  # fmt: skip
COILS = {"OTE", "OTL", "OTU"}


def outputs_then_conditions(text: str) -> list[str]:
    """The condition instructions that follow a coil on the same path outside any branch: in
    Logix ladder such a condition is in series *after* the coil, so it gates only what comes
    after it, never the coil, and a rung written as two lines in one is wrong (review of
    2026-11-10: the pump's interlock was true only while the pump already ran)."""
    found, depth, coil_seen, i = [], 0, [False], 0
    while i < len(text):
        ch = text[i]
        if ch == "[":
            depth += 1
            coil_seen.append(coil_seen[-1])
            i += 1
        elif ch == ",":
            coil_seen[-1] = coil_seen[-2]  # a new branch leg starts where the branch started
            i += 1
        elif ch == "]":
            depth -= 1
            leg = coil_seen.pop()
            coil_seen[-1] = coil_seen[-1] or leg
            i += 1
        else:
            m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\(", text[i:])
            if not m:
                i += 1
                continue
            name, j, level = m.group(1).upper(), i + m.end(), 1
            while level:  # skip the operands, which may hold parentheses
                level += {"(": 1, ")": -1}.get(text[j], 0)
                j += 1
            if depth == 0 and name in CONDITIONS and coil_seen[-1]:
                found.append(name)
            if name in COILS:
                coil_seen[-1] = True
            i = j
    return found


def test_the_checker_catches_a_condition_after_a_coil_and_allows_branches_and_series_outputs():
    old_pump_rung = "XIC(P_01_RUN_DI)OTE(A.Inp_RunFdbk)XIO(TK_01_LSL_DI)OTE(A.Inp_IntlkOK);"
    assert outputs_then_conditions(old_pump_rung) == ["XIO"]
    assert outputs_then_conditions("XIC(A)OTE(B)OTL(C)MOV(1,D);") == []
    assert outputs_then_conditions("XIC(A)[OTE(B),XIC(C)OTE(D)];") == []  # parallel legs
    assert outputs_then_conditions("XIC(A)[OTE(B),XIO(C)]OTE(D);") == []
    assert outputs_then_conditions("MOV(A,B)TON(T,?,?)XIC(T.DN)OTL(C);") == []
    assert outputs_then_conditions("OTE(A)GRT(B,1)OTE(C);") == ["GRT"]


@pytest.mark.parametrize("name", sorted(plantpax.MAPS))
def test_no_rung_puts_a_condition_after_a_coil(name, tmp_path):
    root = export(name, tmp_path)
    texts = [r.findtext("Text") for r in root.iter("Rung")]
    assert len(texts) > 10
    bad = [t for t in texts if outputs_then_conditions(t)]
    assert bad == []


@pytest.mark.parametrize("name", sorted(plantpax.MAPS))
def test_program_commands_are_never_issued_in_the_first_scan(name, tmp_path):
    """Review of 2026-11-10 (fix 2): RM013 says P_PIDE (p. 82), P_Motor (p. 130) and P_ValveC
    (p. 287) discard commands received at power-up, so Init, which runs on the first scan before
    the objects, issues none; the ownership request is the last rung of Control (after every
    object call, read on the second scan); Auto follows the loop's own Sts_Prog; and the
    setpoint is written every scan, since in Operator the program settings track the
    operator's."""
    root = export(name, tmp_path)
    routine = {
        r.get("Name"): [x.findtext("Text") for x in r.iter("Rung")] for r in root.iter("Routine")
    }
    init, control = "".join(routine["Init"]), routine["Control"]
    assert "PCmd_" not in init and "PSet_" not in init
    assert f"OTL({plantpax.PROG_REQ})" in init
    last_call = max(i for i, t in enumerate(control) if re.match(r"P_\w+\(", t))
    owners = [i for i, t in enumerate(control) if "PCmd_Prog" in t]
    assert owners == [len(control) - 1] and owners[0] > last_call
    assert control[-1].startswith(f"XIC({plantpax.PROG_REQ})")
    assert control[-1].endswith(f"OTU({plantpax.PROG_REQ});")
    with_prog = {i.tag for i in plantpax.MAPS[name].instances if i.aoi != "P_AIn"}
    assert set(re.findall(r"OTL\((\w+)\.PCmd_Prog\)", control[-1])) == with_prog
    lic = plantpax.LIC.tag
    (auto,) = [t for t in control if "PCmd_Auto" in t]
    assert auto.startswith(f"XIC({lic}.Sts_Prog)ONS(")
    pide_call = control.index(f"P_PIDE({lic});")
    assert control.index(auto) > pide_call
    assert control.index(f"MOV({plantpax.SP},{lic}.PSet_SP);") < pide_call
    assert "S:FS" not in "".join(control + routine["Init"])


def test_the_l5x_starts_the_pump_once_and_the_stand_in_restarts_it_when_lsl_clears(tmp_path):
    """Review of 2026-11-10 (fix 3), documented in docs/plantpax.md and the scene's guide: the L5X
    gives P_Motor one start (a one-shot at 20 s), while the stand-in (like the OpenPLC program)
    runs the pump again as soon as the low level switch clears."""
    root = export("plantpax-tank-level", tmp_path)
    starts = [r.findtext("Text") for r in root.iter("Rung") if "PCmd_Start" in r.findtext("Text")]
    assert len(starts) == 1 and "ONS(" in starts[0] and "LSL" not in starts[0]
    import runpy

    script = runpy.run_path(str(REPO / "examples" / "pyplc" / "plantpax-tank-level.py"))
    io = {"TK_01_LSH_DI": False, "TK_01_LSL_DI": False, "TK_01_LT_AI": 1.0}
    run = []
    for n in range(1500):  # 30 s at 20 ms, LSL on from 22 s to 24 s
        t = n * 0.02
        io["TK_01_LSL_DI"] = 22.0 <= t < 24.0
        script["step"](io, t)
        run.append(io["P_01_RUN_DO"])
    assert run[int(21 / 0.02)] and not run[int(23 / 0.02)] and run[int(25 / 0.02)]
    page = (REPO / "docs" / "plantpax.md").read_text(encoding="utf-8")
    guide = (REPO / "docs" / "guides" / "plantpax-tank-level.md").read_text(encoding="utf-8")
    assert "## The pump after a low-level trip" in page and "check 11 expects" in page
    assert "plantpax.md#the-pump-after-a-low-level-trip" in guide


@pytest.mark.parametrize("name", sorted(plantpax.MAPS))
def test_every_write_matches_the_members_data_type_in_rm013(name, tmp_path):
    """Review of 2026-11-10 (fix 4): Cfg_FailToStartT is REAL in RM013's Motors workbook, so its
    literal 5.0 is right. Every MOV literal into a cited member has the member's type's form
    (REAL with a decimal point, an integer type without), and every coil writes a BOOL."""
    assert set(plantpax.DATA_TYPE) == set(plantpax.CITED)
    assert plantpax.DATA_TYPE["P_Motor", "Cfg_FailToStartT"] == "REAL"
    aoi = {i.tag: i.aoi for i in plantpax.MAPS[name].instances}
    root = export(name, tmp_path)
    checked = 0
    for text in (r.findtext("Text") for r in root.iter("Rung")):
        for literal, tag, member in re.findall(r"MOV\((-?[0-9.]+),(\w+)\.(\w+)\)", text):
            if tag in aoi:
                real = plantpax.DATA_TYPE[aoi[tag], member] == "REAL"
                assert ("." in literal) == real, (literal, tag, member)
                checked += 1
        for tag, member in re.findall(r"OT[ELU]\((\w+)\.(\w+)\)", text):
            if tag in aoi:
                assert plantpax.DATA_TYPE[aoi[tag], member] == "BOOL", (tag, member)
    assert checked >= 20
