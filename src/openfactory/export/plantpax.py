"""PlantPAx demo L5X (PLAN 3.4): ``openfactory export SCENE --format plantpax``.

Behavior spec
-------------
:func:`plantpax_l5x` turns a scene that has a PlantPAx map (:data:`MAPS`: ``plantpax-tank-level``
and ``tank-level-pid``) into a Studio 5000 partial-import file for Rockwell's Library of Process
Objects 4.x (4.10.03 or later):

- the scene's I/O as controller tags, exactly as ``--format l5x`` writes them
  (:func:`~openfactory.export.l5x_stub.l5x_stub`);
- one instance tag per mapped object (``P_AIn``, ``P_PIDE``, ``P_ValveC``, ``P_Motor``), its
  ``DataType`` the object's name, with an ISA-5.1 description and **no** ``<Data>`` element: the
  library's own defaults apply, and the configuration is written by ``Init``;
- a few support tags the routines need (a TIMER and its preset for the demo's load step);
- program ``PlantPAx_Demo`` with three ladder routines: ``MainRoutine`` (``Init`` on the first
  scan only, then ``Control`` every scan), ``Init`` (one commented rung per configuration value)
  and ``Control`` (the scene tags wired to the objects, each object called once, in signal order).

What it does not contain (docs/plantpax.md): the Add-On Instruction definitions and the library's
data types (Rockwell distributes them; the owner imports the library into the project first), I/O
modules, rationalized alarms, HMI, and anything safety-related.

Parameter names. Every member a rung names is in :data:`CITED`, the source table: the object, the
member, and where Rockwell's public manual lists it, PROCES-RM013I-EN-P (*Library of Process
Objects: Logic Instructions*, April 2023, library 4.10.06), whose parameter lists are attachments
embedded in the PDF, one workbook per family (labelled 4.1) with "Input Parameters" and "Output
Parameters" sheets. Names only: no description or default is copied from the manual, whose
reproduction is not permitted. The names were read from a third-party copy of the manual
(Rockwell's site refused automated download), so **the owner verifies every name against the
official manual in Studio 5000 on import** (docs/plantpax.md). A test checks that every member a
rung names is in the table and every table entry is used, so the table is the complete list.

Unverified, marked in the rung comments and in docs/plantpax.md: each AOI call's operand count
(the instance alone here; an object with Required parameters needs them), the sense of
``Cfg_CtrlAction`` and ``Cfg_Depend`` (which ``PIDE`` form the gains are in), and whether an
instance tag without ``<Data>`` imports with the library's defaults.

The file is deterministic (no export date) and parsed back as XML before it is returned. It is a
training and demo artifact, not a client deliverable: it needs HIS Controls PE review, and the
live run waits on PLAN 2.1 and 2.2 (OpenFactory reaching a Logix controller's tags). OpenFactory
is not a safety-rated simulation.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import quoteattr

from openfactory.core.schema import Scene
from openfactory.export.l5x_stub import SOFTWARE_REVISION, l5x_stub
from openfactory.protocols.modbus import Encoding

PUBLICATION = "PROCES-RM013I-EN-P, April 2023 (library 4.10.06)"
#: The workbook embedded in RM013 that lists each object's parameters.
WORKBOOK = {
    "P_AIn": "IO Processing 4.1",
    "P_PIDE": "Regulatory_Procedural Control 4.1",
    "P_ValveC": "Valves 4.1",
    "P_Motor": "Motors 4.1",
}
IN, OUT = "Input Parameters", "Output Parameters"

#: The source table: (object, member) -> the workbook sheet that lists it. Names only.
CITED: dict[tuple[str, str], str] = {
    ("P_AIn", "Inp_PV"): IN,
    ("P_AIn", "Cfg_InpRawMin"): IN,
    ("P_AIn", "Cfg_InpRawMax"): IN,
    ("P_AIn", "Cfg_PVEUMin"): IN,
    ("P_AIn", "Cfg_PVEUMax"): IN,
    ("P_AIn", "Val"): OUT,
    ("P_AIn", "Sts_PVBad"): OUT,
    ("P_PIDE", "Inp_PV"): IN,
    ("P_PIDE", "Inp_PVBad"): IN,
    ("P_PIDE", "Inp_IntlkOK"): IN,
    ("P_PIDE", "Cfg_PVEUMin"): IN,
    ("P_PIDE", "Cfg_PVEUMax"): IN,
    ("P_PIDE", "Cfg_CVEUMin"): IN,
    ("P_PIDE", "Cfg_CVEUMax"): IN,
    ("P_PIDE", "Cfg_MinCV"): IN,
    ("P_PIDE", "Cfg_MaxCV"): IN,
    ("P_PIDE", "Cfg_PGain"): IN,
    ("P_PIDE", "Cfg_IGain"): IN,
    ("P_PIDE", "Cfg_DGain"): IN,
    ("P_PIDE", "Cfg_CtrlAction"): IN,
    ("P_PIDE", "Cfg_Depend"): IN,
    ("P_PIDE", "Cfg_IntlkCV"): IN,
    ("P_PIDE", "PSet_SP"): IN,
    ("P_PIDE", "PCmd_Prog"): IN,
    ("P_PIDE", "PCmd_Auto"): IN,
    ("P_PIDE", "Out_CV"): OUT,
    ("P_ValveC", "Inp_PosFdbk"): IN,
    ("P_ValveC", "Inp_IntlkOK"): IN,
    ("P_ValveC", "Cfg_HasPosFdbk"): IN,
    ("P_ValveC", "Cfg_CVEUMin"): IN,
    ("P_ValveC", "Cfg_CVEUMax"): IN,
    ("P_ValveC", "Cfg_CVRawMin"): IN,
    ("P_ValveC", "Cfg_CVRawMax"): IN,
    ("P_ValveC", "Cfg_FdbkRawMin"): IN,
    ("P_ValveC", "Cfg_FdbkRawMax"): IN,
    ("P_ValveC", "Cfg_IntlkCV"): IN,
    ("P_ValveC", "PSet_CV"): IN,
    ("P_ValveC", "PCmd_Prog"): IN,
    ("P_ValveC", "Out_CV"): OUT,
    ("P_Motor", "Inp_RunFdbk"): IN,
    ("P_Motor", "Inp_IntlkOK"): IN,
    ("P_Motor", "Cfg_HasRunFdbk"): IN,
    ("P_Motor", "Cfg_UseRunFdbk"): IN,
    ("P_Motor", "Cfg_FailToStartT"): IN,
    ("P_Motor", "PCmd_Prog"): IN,
    ("P_Motor", "PCmd_Start"): IN,
    ("P_Motor", "Out_Run"): OUT,
}

AREA = "DEMO"  # HIS Controls AREA_EQUIP_NNN; the area prefix is the owner's to confirm
PROGRAM = "PlantPAx_Demo"


@dataclass(frozen=True)
class Instance:
    tag: str  # instance tag name
    aoi: str  # P_AIn, P_PIDE, ...
    isa: str  # ISA-5.1 tag, LT-101
    description: str


@dataclass(frozen=True)
class Rung:
    comment: str
    text: str


@dataclass(frozen=True)
class PlantPaxMap:
    instances: tuple[Instance, ...]
    support: tuple[tuple[str, str, str], ...]  # (name, data type, description)
    init: tuple[Rung, ...]
    control: tuple[Rung, ...]


LT = Instance(f"{AREA}_LT_101", "P_AIn", "LT-101", "Level Control Tank Level Transmitter LT-101")
LIC = Instance(
    f"{AREA}_LIC_101", "P_PIDE", "LIC-101", "Level Control Tank Level Controller LIC-101"
)
LV = Instance(f"{AREA}_LV_101", "P_ValveC", "LV-101", "Tank Inlet Level Control Valve LV-101")
PMP = Instance(f"{AREA}_P_101", "P_Motor", "P-101", "Level Control Tank Transfer Pump P-101")

# Tuning carried over from tank-level-pid's PI (OpenPLC's PID: KP -400 %/m on a 2 m span, TR 6 s):
# PIDE gains are % of CV per % of PV and per minute, so the proportional gain is 400 * 2 / 100 = 8
# and the reset 6 s = 0.1 min, 10 repeats a minute in the dependent form.
_LOOP_INIT = (
    Rung(
        "LT-101 P_AIn: raw and engineering ranges equal, 0 to 2 m (OpenFactory writes metres)",
        f"MOV(0.0,{LT.tag}.Cfg_InpRawMin)MOV(2.0,{LT.tag}.Cfg_InpRawMax)"
        f"MOV(0.0,{LT.tag}.Cfg_PVEUMin)MOV(2.0,{LT.tag}.Cfg_PVEUMax);",
    ),
    Rung(
        "LIC-101 P_PIDE: PV 0 to 2 m, CV 0 to 100 %, CV limited 0 to 100 %",
        f"MOV(0.0,{LIC.tag}.Cfg_PVEUMin)MOV(2.0,{LIC.tag}.Cfg_PVEUMax)"
        f"MOV(0.0,{LIC.tag}.Cfg_CVEUMin)MOV(100.0,{LIC.tag}.Cfg_CVEUMax)"
        f"MOV(0.0,{LIC.tag}.Cfg_MinCV)MOV(100.0,{LIC.tag}.Cfg_MaxCV);",
    ),
    Rung(
        "LIC-101 P_PIDE: PI from tank-level-pid, gain 8 %/%, 10 repeats/min, no derivative; "
        "dependent form, reverse acting (CV rises as the level falls) "
        "[TBD: verify the sense of Cfg_Depend and Cfg_CtrlAction in RM013 and Studio 5000]",
        f"MOV(8.0,{LIC.tag}.Cfg_PGain)MOV(10.0,{LIC.tag}.Cfg_IGain)MOV(0.0,{LIC.tag}.Cfg_DGain)"
        f"OTL({LIC.tag}.Cfg_Depend)OTU({LIC.tag}.Cfg_CtrlAction);",
    ),
    Rung(
        "LIC-101 P_PIDE: setpoint 1.0 m (tunable: PSet_SP), CV 0 % while interlocked",
        f"MOV(1.0,{LIC.tag}.PSet_SP)MOV(0.0,{LIC.tag}.Cfg_IntlkCV);",
    ),
    Rung(
        "LIC-101 P_PIDE: program owner, automatic (one-shot program commands)",
        f"OTL({LIC.tag}.PCmd_Prog)OTL({LIC.tag}.PCmd_Auto);",
    ),
    Rung(
        "LV-101 P_ValveC: CV and feedback 0 to 100 % both sides (the scene's AO and AI are in %); "
        "position feedback used; closed while interlocked",
        f"OTL({LV.tag}.Cfg_HasPosFdbk)MOV(0.0,{LV.tag}.Cfg_CVEUMin)MOV(100.0,{LV.tag}.Cfg_CVEUMax)"
        f"MOV(0.0,{LV.tag}.Cfg_CVRawMin)MOV(100.0,{LV.tag}.Cfg_CVRawMax)"
        f"MOV(0.0,{LV.tag}.Cfg_FdbkRawMin)MOV(100.0,{LV.tag}.Cfg_FdbkRawMax)"
        f"MOV(0.0,{LV.tag}.Cfg_IntlkCV)OTL({LV.tag}.PCmd_Prog);",
    ),
)

_LOOP_CONTROL = (
    Rung(
        "LT-101 Level Control Tank level: TK_01_LT_AI (m) into P_AIn",
        f"MOV(TK_01_LT_AI,{LT.tag}.Inp_PV);",
    ),
    Rung(
        "LT-101 P_AIn instance call [TBD: operand count, instance only here]",
        f"P_AIn({LT.tag});",
    ),
    Rung(
        "LIC-101 PV from LT-101; a bad PV marks the controller's PV bad",
        f"MOV({LT.tag}.Val,{LIC.tag}.Inp_PV)XIC({LT.tag}.Sts_PVBad)OTE({LIC.tag}.Inp_PVBad);",
    ),
    Rung(
        "LIC-101 interlock: the high level switch (LSH) holds the controller at 0 %",
        f"XIO(TK_01_LSH_DI)OTE({LIC.tag}.Inp_IntlkOK);",
    ),
    Rung(
        "LIC-101 P_PIDE instance call [TBD: operand count, instance only here]",
        f"P_PIDE({LIC.tag});",
    ),
    Rung(
        "LV-101 CV from LIC-101, position feedback from FV_01_ZT_AI, LSH closes the valve",
        f"MOV({LIC.tag}.Out_CV,{LV.tag}.PSet_CV)MOV(FV_01_ZT_AI,{LV.tag}.Inp_PosFdbk)"
        f"XIO(TK_01_LSH_DI)OTE({LV.tag}.Inp_IntlkOK);",
    ),
    Rung(
        "LV-101 P_ValveC instance call [TBD: operand count, instance only here]",
        f"P_ValveC({LV.tag});",
    ),
    Rung(
        "LV-101 valve position command to the simulator, FV_01_POS_AO (%)",
        f"MOV({LV.tag}.Out_CV,FV_01_POS_AO);",
    ),
)

_PUMP_INIT = (
    Rung(
        "P-101 P_Motor: run feedback used, fail to start after 5 s, program owner",
        f"OTL({PMP.tag}.Cfg_HasRunFdbk)OTL({PMP.tag}.Cfg_UseRunFdbk)"
        f"MOV(5.0,{PMP.tag}.Cfg_FailToStartT)OTL({PMP.tag}.PCmd_Prog);",
    ),
)

_PUMP_CONTROL = (
    Rung(
        "P-101 demo load step: start the transfer pump once, DEMO_LOAD_DELAY_MS after power-up",
        f"MOV({AREA}_LOAD_DELAY_MS,{AREA}_LOAD_TMR.PRE)TON({AREA}_LOAD_TMR,?,?)"
        f"XIC({AREA}_LOAD_TMR.DN)ONS({AREA}_LOAD_ONS)OTL({PMP.tag}.PCmd_Start);",
    ),
    Rung(
        "P-101 running feedback P_01_RUN_DI into P_Motor",
        f"XIC(P_01_RUN_DI)OTE({PMP.tag}.Inp_RunFdbk);",
    ),
    Rung(
        "P-101 interlock: the low level switch (LSL) interlocks the pump off",
        f"XIO(TK_01_LSL_DI)OTE({PMP.tag}.Inp_IntlkOK);",
    ),
    Rung(
        "P-101 P_Motor instance call [TBD: operand count, instance only here]",
        f"P_Motor({PMP.tag});",
    ),
    Rung(
        "P-101 run command to the simulator, P_01_RUN_DO",
        f"XIC({PMP.tag}.Out_Run)OTE(P_01_RUN_DO);",
    ),
)

MAPS: dict[str, PlantPaxMap] = {
    "plantpax-tank-level": PlantPaxMap(
        (LT, LIC, LV, PMP),
        (
            (f"{AREA}_LOAD_TMR", "TIMER", "Demo load step: transfer pump start delay timer"),
            (f"{AREA}_LOAD_DELAY_MS", "DINT", "Demo load step: pump start delay, ms (20000)"),
            (f"{AREA}_LOAD_ONS", "BOOL", "Demo load step: one-shot storage bit"),
        ),
        _LOOP_INIT + _PUMP_INIT + (
            Rung("Demo load step: start the transfer pump 20 s after power-up (tunable)",
                 f"MOV(20000,{AREA}_LOAD_DELAY_MS);"),
        ),
        _LOOP_CONTROL + _PUMP_CONTROL,
    ),
    "tank-level-pid": PlantPaxMap((LT, LIC, LV), (), _LOOP_INIT, _LOOP_CONTROL),
}  # fmt: skip

MEMBER = re.compile(r"\b(" + AREA + r"_[A-Z0-9_]+)\.([A-Za-z][A-Za-z0-9_]*)")
LOGIX_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,39}$")
TIMER_MEMBERS = {"PRE", "DN", "ACC", "EN", "TT"}


def members_used(plan: PlantPaxMap) -> set[tuple[str, str]]:
    """(object, member) for every instance member the routines name."""
    aoi = {i.tag: i.aoi for i in plan.instances}
    out = set()
    for rung in plan.init + plan.control:
        for tag, member in MEMBER.findall(rung.text):
            if tag in aoi:
                out.add((aoi[tag], member))
    return out


def _cdata(text: str) -> str:
    return "<![CDATA[" + text.replace("]]>", "]]]]><![CDATA[>") + "]]>"


def _routine(name: str, rungs: tuple[Rung, ...]) -> list[str]:
    out = [f'<Routine Use="Target" Name="{name}" Type="RLL">', "<RLLContent>"]
    for n, rung in enumerate(rungs):
        out += [
            f'<Rung Number="{n}" Type="N">',
            f"<Comment>{_cdata(rung.comment)}</Comment>",
            f"<Text>{_cdata(rung.text)}</Text>",
            "</Rung>",
        ]
    return out + ["</RLLContent>", "</Routine>"]


def plantpax_l5x(
    scene: Scene,
    encoding: Encoding | None = None,
    *,
    scene_path: str | Path | None = None,
    write_lock: bool = True,
    controller: str = "OpenFactory",
    software_revision: str = SOFTWARE_REVISION,
) -> str:
    """The L5X text. Raises ValueError for a scene with no PlantPAx map, or when the map names a
    member the source table does not cite."""
    plan = MAPS.get(scene.scene.name)
    if plan is None:
        known = ", ".join(sorted(MAPS))
        raise ValueError(f"no PlantPAx map for scene {scene.scene.name!r} (maps: {known})")
    uncited = sorted(members_used(plan) - set(CITED))
    if uncited:
        raise ValueError(f"members not in the source table: {uncited}")
    stub = l5x_stub(
        scene,
        encoding,
        scene_path=scene_path,
        write_lock=write_lock,
        controller=controller,
        software_revision=software_revision,
    ).splitlines()
    io_tags = stub[stub.index("<Tags>") + 1 : stub.index("</Tags>")]
    lines = stub[: stub.index("<Tags>") + 1] + io_tags
    for inst in plan.instances:
        lines += [
            f'<Tag Name="{inst.tag}" TagType="Base" DataType="{inst.aoi}" Constant="false"'
            ' ExternalAccess="Read/Write">',
            f"<Description>{_cdata(inst.description)}</Description>",
            "</Tag>",
        ]
    for name, data_type, description in plan.support:
        radix = "" if data_type in ("TIMER", "BOOL") else ' Radix="Decimal"'
        lines += [
            f'<Tag Name="{name}" TagType="Base" DataType="{data_type}"{radix} Constant="false"'
            ' ExternalAccess="Read/Write">',
            f"<Description>{_cdata(description)}</Description>",
            "</Tag>",
        ]
    main = (
        Rung("First scan only: write the objects' configuration (Init)", "XIC(S:FS)JSR(Init,0);"),
        Rung(
            "Every scan: wire the scene's tags to the PlantPAx objects (Control)", "JSR(Control,0);"
        ),
    )
    lines += [
        "</Tags>",
        "<Programs>",
        f'<Program Use="Target" Name={quoteattr(PROGRAM)} MainRoutineName="MainRoutine"'
        ' TestEdits="false" Disabled="false" UseAsFolder="false">',
        "<Description>"
        + _cdata(
            "PlantPAx demo (OpenFactory PLAN 3.4): training and demo only, not safety-rated; "
            f"names cited from {PUBLICATION}; pending HIS Controls PE review"
        )
        + "</Description>",
        "<Routines>",
        *_routine("MainRoutine", main),
        *_routine("Init", plan.init),
        *_routine("Control", plan.control),
        "</Routines>",
        "</Program>",
        "</Programs>",
        "</Controller>",
        "</RSLogix5000Content>",
        "",
    ]
    text = "\n".join(lines)
    ET.fromstring(text.encode("utf-8"))  # well-formed, or raise before anything is written
    return text


def source_table() -> str:
    """The source table as Markdown, for docs/plantpax.md and the kit."""
    rows = ["| Object | Member | Where RM013 lists it |", "|---|---|---|"]
    for (aoi, member), sheet in sorted(CITED.items()):
        rows.append(
            f'| `{aoi}` | `{member}` | {PUBLICATION}, workbook "{WORKBOOK[aoi]}", {sheet} |'
        )
    return "\n".join(rows) + "\n"
