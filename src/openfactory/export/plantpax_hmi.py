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
read-only, with history on the process value only (``P_AIn.Val``). No alarms (none are
rationalized, ISA-18.2).

:func:`plantpax_faceplates` writes one Perspective view per object type, in a single JSON
document ``{"generated": ..., "views": {"PlantPAx/<AOI>_Faceplate": view}}``, each view in the
shape of the shipped ``sorting-by-height-perspective.json``: a header (equipment name and its
tag's documentation, the mode shown as not configured), the main readouts bound indirectly to
``{Root}/<Member>`` from the view parameter ``tagPath`` (template reuse: no absolute path), an
alarm summary line, and a breadcrumb footer, in the ISA-101 palette (``ignition-conventions.md``).
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
from openfactory.export.plantpax import AREA, CITED, MAPS, PUBLICATION

VERSION = "8.3.0"
LOGIX_OPC_SERVER = "Ignition OPC UA Server"
DEFAULT_DEVICE = "PlantPAxDemo"
GENERATED = "generated, pending PE review"
#: The cited members that are BOOL in RM013's workbooks; every other cited member is REAL.
BOOL_MEMBERS = frozenset(
    {
        "Sts_PVBad", "Inp_PVBad", "Inp_IntlkOK", "Inp_RunFdbk", "Cfg_CtrlAction", "Cfg_Depend",
        "Cfg_HasPosFdbk", "Cfg_HasRunFdbk", "Cfg_UseRunFdbk", "PCmd_Prog", "PCmd_Auto",
        "PCmd_Start", "Out_Run",
    }
)  # fmt: skip
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
BG, HEADER_BG, TEXT, MUTED, HIGHLIGHT = "#1F2A36", "#0F172A", "#E5E7EB", "#9CA3AF", "#60A5FA"


def _map(scene: Scene) -> Any:
    plan = MAPS.get(scene.scene.name)
    if plan is None:
        known = ", ".join(sorted(MAPS))
        raise ValueError(f"no PlantPAx map for scene {scene.scene.name!r} (maps: {known})")
    return plan


def members(aoi: str) -> list[str]:
    """The cited members of one object, sorted."""
    return sorted(m for a, m in CITED if a == aoi)


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
                "dataType": "Boolean" if m in BOOL_MEMBERS else "Float4",
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


def _view(aoi: str) -> dict[str, Any]:
    readouts = READOUTS[aoi]
    rows = []
    for label, member, units in readouts:
        if member in BOOL_MEMBERS:
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
    custom = {m: (False if m in BOOL_MEMBERS else 0.0) for _, m, _ in readouts}
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
            _label("Mode", {"color": HIGHLIGHT, "fontSize": "12px"}),
        ],
    }
    header["children"][2]["props"]["text"] = "Mode: not shown (no mode member cited yet)"
    commands = _label(
        "Commands",
        {"color": MUTED, "fontSize": "12px", "padding": "8px"},
    )
    commands["props"]["text"] = (
        "Display only: operator commands and setpoints use the object's OCmd_/OSet_ members, "
        "not yet verified in Studio 5000 (docs/plantpax.md)."
    )
    alarms = _label("AlarmSummary", {"color": MUTED, "fontSize": "12px", "padding": "8px"})
    alarms["props"]["text"] = "Alarms: none configured (not rationalized, ISA-18.2)."
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
