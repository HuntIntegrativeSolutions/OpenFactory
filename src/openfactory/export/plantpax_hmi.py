"""Ignition UDTs and Perspective faceplates for the PlantPAx demo (PLAN 3.8; faceplates moved here
from 3.4, ruled 2026-11-09): ``export SCENE --format plantpax-udts`` and ``--format
plantpax-faceplates``.

Behavior spec
-------------
:func:`plantpax_udts` writes Ignition 8.3 tag JSON for a scene with a PlantPAx map
(:data:`openfactory.export.plantpax.MAPS`), in the structure of ``--format ignition`` and the HIS
Controls conventions: a ``_types_`` folder with one ``UdtType`` per object type the map uses,
named ``<AOI>_UDT`` (``P_AIn_UDT``), then an area folder (``DEMO``) with one ``UdtInstance`` per
instance, named by its equipment part (``LT_101``), its ``InstanceName`` parameter the controller
tag (``DEMO_LT_101``). **The members are exactly the cited names** of that object
(:data:`openfactory.export.plantpax.CITED`, PROCES-RM013I), nothing more: each an ``AtomicTag``
reading ``ns=1;s=[{Device}]{InstanceName}.<Member>`` through Ignition's Logix driver (``Device``,
a UDT parameter, is the gateway's device alias, never an address), ``Boolean`` or ``Float4``,
read-only, with history on the process value only (``P_AIn.Val``). The members are the rungs'
cited names (``CITED``) and the faceplates' (``DISPLAY``: mode, owner, state, interlock and
``Alm_`` bits), with ``Int4`` for the owner id. **No Ignition alarms are configured** on any
member, the ``Alm_`` bits included: nothing is rationalized (ISA-18.2), so the bits are read and
shown, never alarmed.

:func:`plantpax_faceplates` writes one Perspective view per object type, in a single JSON
document ``{"generated": ..., "views": {"PlantPAx/<AOI>_Faceplate": view}}``, each view in the
shape of the shipped ``sorting-by-height-perspective.json``: a header (equipment name, its tag's
documentation, the mode from the command-source bits, ``Program``/``Operator``/``Out of
Service``, plus the loop mode for ``P_PIDE``, and the owner id), the main readouts (running state
for the motor, position for the valve, the interlock trip bit), an alarm line naming the active
``Alm_`` bits in ISA-101 alarm red, and a breadcrumb footer, every member bound indirectly to
``{Root}/<Member>`` from the view parameter ``tagPath`` (template reuse: no absolute path).
**The faceplates are display-only.** Operator commands and setpoints go through the objects'
``OCmd_``/``OSet_`` members, which are local tags whose names the manual's extracted lists did
not give; until the owner verifies them in Studio 5000 the views write nothing (each says so).

Both are deterministic and need HIS Controls PE review before client use; neither has been loaded
into a gateway. Training and demo use only; OpenFactory is not a safety-rated simulation.
"""

from __future__ import annotations

import json
from typing import Any

from openfactory.core.schema import Scene
from openfactory.export.plantpax import AREA, CITED, DATA_TYPE, DISPLAY, MAPS, PUBLICATION

VERSION = "8.3.0"
LOGIX_OPC_SERVER = "Ignition OPC UA Server"
DEFAULT_DEVICE = "PlantPAxDemo"
GENERATED = "generated, pending PE review"
#: History on the process value only (HIS Controls Ignition conventions).
HISTORY = frozenset({("P_AIn", "Val")})
#: What each faceplate shows: (label, member, units hint) in display order.
READOUTS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "P_AIn": (("PV", "Val", "m"), ("Input", "Inp_PV", "m"), ("PV bad", "Sts_PVBad", "")),
    "P_PIDE": (
        ("PV", "Inp_PV", "m"), ("SP", "PSet_SP", "m"), ("CV", "Out_CV", "%"),
        ("Interlock OK", "Inp_IntlkOK", ""),
    ),
    "P_ValveC": (
        ("CV", "Out_CV", "%"), ("Position", "Inp_PosFdbk", "%"),
        ("Interlock OK", "Inp_IntlkOK", ""),
    ),
    "P_Motor": (
        ("Run command", "Out_Run", ""), ("Running", "Inp_RunFdbk", ""),
        ("Interlock OK", "Inp_IntlkOK", ""),
    ),
}  # fmt: skip
#: Status shown on each faceplate (PLAN 3.8 follow-up, ruled 2026-11-10), all from the cited
#: members (:data:`openfactory.export.plantpax.DISPLAY`). Each list is (member, text) in
#: precedence order: the first member that is on decides the text.
MODE: dict[str, tuple[tuple[str, str], ...]] = {
    "P_PIDE": (("Sts_OoS", "Out of Service"), ("Sts_Prog", "Program"), ("Sts_Oper", "Operator")),
    "P_ValveC": (("Sts_OoS", "Out of Service"), ("Sts_Prog", "Program"), ("Sts_Oper", "Operator")),
    "P_Motor": (("Sts_OoS", "Out of Service"), ("Sts_Prog", "Program"), ("Sts_Oper", "Operator")),
}
LOOP_MODE: dict[str, tuple[tuple[str, str], ...]] = {
    "P_PIDE": (("Sts_Casc", "Cascade"), ("Sts_Auto", "Auto"), ("Sts_Man", "Manual")),
}
RUN_STATE: dict[str, tuple[tuple[str, str], ...]] = {
    "P_Motor": (
        ("Sts_Running", "Running"), ("Sts_Starting", "Starting"),
        ("Sts_Stopping", "Stopping"), ("Sts_Stopped", "Stopped"),
    ),
}  # fmt: skip
#: Extra readouts after READOUTS: (label, member, units).
STATE: dict[str, tuple[tuple[str, str, str], ...]] = {
    "P_AIn": (("PV failed", "Sts_Fail", ""),),
    "P_PIDE": (("Interlock trip", "Sts_IntlkTrip", ""),),
    "P_ValveC": (("Valve position", "Val_Pos", "%"), ("Interlock trip", "Sts_IntlkTrip", "")),
    "P_Motor": (("Interlock trip", "Sts_IntlkTrip", ""),),
}
#: The alarm bits each faceplate lists when on.
ALARMS: dict[str, tuple[str, ...]] = {
    "P_AIn": ("Alm_HiHi", "Alm_Hi", "Alm_Lo", "Alm_LoLo", "Alm_Fail"),
    "P_PIDE": (
        "Alm_HiHiDev", "Alm_HiDev", "Alm_LoDev", "Alm_LoLoDev", "Alm_IntlkTrip", "Alm_Fail",
    ),
    "P_ValveC": ("Alm_IntlkTrip", "Alm_ActuatorFault", "Alm_IOFault"),
    "P_Motor": ("Alm_FailToStart", "Alm_FailToStop", "Alm_IntlkTrip", "Alm_IOFault"),
}  # fmt: skip
#: Logix type -> Ignition tag dataType.
IGNITION_TYPE = {"BOOL": "Boolean", "REAL": "Float4", "DINT": "Int4"}
BG, HEADER_BG, TEXT, MUTED, HIGHLIGHT = "#1F2A36", "#0F172A", "#E5E7EB", "#9CA3AF", "#60A5FA"
ALARM = "#EF4444"  # ISA-101 alarmHigh: an active alarm bit


def _map(scene: Scene) -> Any:
    plan = MAPS.get(scene.scene.name)
    if plan is None:
        known = ", ".join(sorted(MAPS))
        raise ValueError(f"no PlantPAx map for scene {scene.scene.name!r} (maps: {known})")
    return plan


def members(aoi: str) -> list[str]:
    """The cited members of one object (the rungs' and the faceplate's), sorted."""
    return sorted(m for a, m in {**CITED, **DISPLAY} if a == aoi)


def shown(aoi: str) -> list[str]:
    """Every member the faceplate of ``aoi`` binds, in display order, each once."""
    out = [m for _, m, _ in READOUTS[aoi]]
    out += [m for m, _ in MODE.get(aoi, ())] + [m for m, _ in LOOP_MODE.get(aoi, ())]
    out += ["Val_Owner"] if (aoi, "Val_Owner") in DISPLAY else []
    out += [m for m, _ in RUN_STATE.get(aoi, ())] + [m for _, m, _ in STATE.get(aoi, ())]
    out += list(ALARMS[aoi]) + ["Sts_AlmInh"]
    return list(dict.fromkeys(out))


def _pick(pairs: tuple[tuple[str, str], ...], default: str) -> str:
    """An expression: the text of the first member that is on, else ``default``."""
    expr = f'"{default}"'
    for member, text in reversed(pairs):
        expr = f'if({{view.custom.{member}}}, "{text}", {expr})'
    return expr


def plantpax_udts(scene: Scene, *, device: str = DEFAULT_DEVICE) -> dict[str, Any]:
    plan = _map(scene)
    aois = sorted({i.aoi for i in plan.instances})
    types = []
    for aoi in aois:
        tags = [
            {
                "name": m,
                "tagType": "AtomicTag",
                "valueSource": "opc",
                "opcServer": LOGIX_OPC_SERVER,
                "opcItemPath": f"ns=1;s=[{{Device}}]{{InstanceName}}.{m}",
                "dataType": IGNITION_TYPE[DATA_TYPE[aoi, m]],
                "accessRights": "Read_Only",
                "historyEnabled": (aoi, m) in HISTORY,
                "documentation": f"{aoi}.{m} ({PUBLICATION})",
                "enabled": True,
            }
            for m in members(aoi)
        ]
        types.append(
            {
                "name": f"{aoi}_UDT",
                "tagType": "UdtType",
                "documentation": f"{aoi}: the members the PlantPAx demo uses; {GENERATED}",
                "parameters": {
                    "InstanceName": {"dataType": "String", "value": ""},
                    "Device": {"dataType": "String", "value": device},
                },
                "tags": tags,
            }
        )
    instances = [
        {
            "name": inst.tag.removeprefix(f"{AREA}_"),
            "tagType": "UdtInstance",
            "typeId": f"{inst.aoi}_UDT",
            "parameters": {
                "InstanceName": {"dataType": "String", "value": inst.tag},
                "Device": {"dataType": "String", "value": device},
            },
            "documentation": inst.description,
        }
        for inst in plan.instances
    ]
    return {
        "version": VERSION,
        "tags": [
            {"name": "_types_", "tagType": "Folder", "tags": types},
            {"name": AREA, "tagType": "Folder", "tags": instances},
        ],
    }


def _label(name: str, style: dict[str, Any], **extra: Any) -> dict[str, Any]:
    node: dict[str, Any] = {
        "type": "ia.display.label",
        "version": 0,
        "meta": {"name": name},
        "props": {"style": style},
    }
    node.update(extra)
    return node


def _bound(member: str) -> dict[str, Any]:
    return {
        "binding": {
            "type": "tag",
            "config": {
                "fallbackDelay": 2.5,
                "mode": "indirect",
                "references": {"Root": "{view.params.tagPath}"},
                "tagPath": f"{{Root}}/{member}",
            },
        }
    }


def _expr_label(name: str, expr: str, color: str = TEXT, size: str = "18px") -> dict[str, Any]:
    return _label(
        name,
        {"color": color, "fontSize": size, "padding": "4px 8px"},
        propConfig={"props.text": {"binding": {"type": "expr", "config": {"expression": expr}}}},
    )


def _view(aoi: str) -> dict[str, Any]:
    readouts = READOUTS[aoi] + STATE.get(aoi, ())
    rows = []
    if aoi in RUN_STATE:
        rows.append(_expr_label("RunStateLabel", '"State: " + ' + _pick(RUN_STATE[aoi], "unknown")))
    for label, member, units in readouts:
        if DATA_TYPE[aoi, member] == "BOOL":
            expr = f'if({{view.custom.{member}}}, "{label}: yes", "{label}: no")'
        else:
            expr = f'"{label}: " + numberFormat({{view.custom.{member}}}, "#,##0.00") + " {units}"'
        rows.append(
            _label(
                f"{member}Label",
                {"color": TEXT, "fontSize": "18px", "padding": "4px 8px"},
                propConfig={
                    "props.text": {"binding": {"type": "expr", "config": {"expression": expr}}}
                },
            )
        )
    default = {"BOOL": False, "REAL": 0.0, "DINT": 0}
    custom = {m: default[DATA_TYPE[aoi, m]] for m in shown(aoi)}
    prop_config: dict[str, Any] = {f"custom.{m}": {**_bound(m), "persistent": True} for m in custom}
    prop_config["params.tagPath"] = {"paramDirection": "input", "persistent": True}
    prop_config["params.equipmentName"] = {"paramDirection": "input", "persistent": True}
    header = {
        "type": "ia.container.flex",
        "version": 0,
        "meta": {"name": "Header"},
        "props": {"direction": "column", "style": {"backgroundColor": HEADER_BG, "padding": "8px"}},
        "children": [
            _label(
                "TagName",
                {"color": TEXT, "fontSize": "16px", "fontWeight": "bold"},
                propConfig={
                    "props.text": {
                        "binding": {
                            "type": "expr",
                            "config": {"expression": "{view.params.equipmentName}"},
                        }
                    }
                },
            ),
            _label(
                "Description",
                {"color": MUTED, "fontSize": "12px"},
                propConfig={
                    "props.text": {
                        "binding": {
                            "type": "tag",
                            "config": {
                                "fallbackDelay": 2.5,
                                "mode": "indirect",
                                "references": {"Root": "{view.params.tagPath}"},
                                "tagPath": "{Root}.Documentation",
                            },
                        }
                    }
                },
            ),
            _mode(aoi),
            _owner(aoi),
        ],
    }
    commands = _label(
        "Commands",
        {"color": MUTED, "fontSize": "12px", "padding": "8px"},
    )
    commands["props"]["text"] = (
        "Display only: operator commands and setpoints use the object's OCmd_/OSet_ members, "
        "not yet verified in Studio 5000 (docs/plantpax.md)."
    )
    alarms = _alarms(aoi)
    footer = {
        "type": "ia.container.flex",
        "version": 0,
        "meta": {"name": "Footer"},
        "props": {
            "direction": "row",
            "style": {"backgroundColor": HEADER_BG, "padding": "4px 8px"},
        },
        "children": [
            _label(
                "Breadcrumb",
                {"color": MUTED, "fontSize": "11px"},
                propConfig={
                    "props.text": {
                        "binding": {
                            "type": "expr",
                            "config": {
                                "expression": f'"{AREA} / " + {{view.params.equipmentName}}'
                            },
                        }
                    }
                },
            )
        ],
    }
    main = {
        "type": "ia.container.flex",
        "version": 0,
        "meta": {"name": "Main"},
        "props": {"direction": "column", "style": {"padding": "8px"}},
        "children": rows,
    }
    return {
        "custom": {
            "generated": GENERATED,
            "object": aoi,
            "members_from": f"{PUBLICATION}; names only, verify in Studio 5000",
            **custom,
        },
        "params": {"tagPath": f"[default]{AREA}/", "equipmentName": ""},
        "propConfig": prop_config,
        "props": {"defaultSize": {"width": 360, "height": 420}},
        "root": {
            "type": "ia.container.flex",
            "version": 0,
            "meta": {"name": "root"},
            "props": {"direction": "column", "style": {"backgroundColor": BG}},
            "children": [header, main, commands, alarms, footer],
        },
    }


def _mode(aoi: str) -> dict[str, Any]:
    """The command source (and the loop mode), or a fixed line for an object that has none."""
    if aoi not in MODE:
        node = _label("Mode", {"color": HIGHLIGHT, "fontSize": "12px"})
        node["props"]["text"] = "Mode: none (an input object has no command source)"
        return node
    expr = '"Mode: " + ' + _pick(MODE[aoi], "none selected")
    if aoi in LOOP_MODE:
        expr += ' + " / " + ' + _pick(LOOP_MODE[aoi], "no loop mode")
    return _expr_label("Mode", expr, HIGHLIGHT, "12px")


def _owner(aoi: str) -> dict[str, Any]:
    if (aoi, "Val_Owner") not in DISPLAY:
        node = _label("Owner", {"color": MUTED, "fontSize": "12px"})
        node["props"]["text"] = "Owner: none (no command source)"
        return node
    expr = (
        'if({view.custom.Val_Owner} = 0, "Owner: none", "Owner: " + toStr({view.custom.Val_Owner}))'
    )
    return _expr_label("Owner", expr, MUTED, "12px")


def _alarms(aoi: str) -> dict[str, Any]:
    """The active alarm bits by name, red while any is on; the UDTs configure no Ignition alarms
    (nothing is rationalized, ISA-18.2): this line only shows the controller's own bits."""
    bits = ALARMS[aoi]
    anyone = " || ".join(f"{{view.custom.{b}}}" for b in bits)
    names = " + ".join(f'if({{view.custom.{b}}}, "{b.removeprefix("Alm_")} ", "")' for b in bits)
    text = (
        f'"Alarms (controller bits, not rationalized): " + if({anyone}, {names}, "none active")'
        ' + if({view.custom.Sts_AlmInh}, " (some shelved or disabled)", "")'
    )
    node = _expr_label("AlarmSummary", text, MUTED, "12px")
    node["propConfig"]["props.style.color"] = {
        "binding": {"type": "expr", "config": {"expression": f'if({anyone}, "{ALARM}", "{MUTED}")'}}
    }
    return node


def plantpax_faceplates(scene: Scene) -> dict[str, Any]:
    plan = _map(scene)
    aois = sorted({i.aoi for i in plan.instances})
    return {
        "generated": GENERATED,
        "note": "One Perspective view per PlantPAx object; open each with tagPath "
        f"[default]{AREA}/<instance> (for example [default]{AREA}/LT_101) and equipmentName. "
        "Display only; not loaded into a Designer yet; training and demo use, not safety-rated.",
        "views": {f"PlantPAx/{aoi}_Faceplate": _view(aoi) for aoi in aois},
    }


def as_json(doc: dict[str, Any]) -> str:
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
