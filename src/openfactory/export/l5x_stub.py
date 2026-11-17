"""L5X controller tag stubs (PLAN task 2.3): ``openfactory export SCENE --format l5x``.

Behavior spec
-------------
:func:`l5x_stub` turns a scene into a Studio 5000 partial-import file holding one controller-scoped
base tag per row of the I/O list CSV (:func:`~openfactory.export.io_list_csv.io_list_rows`), in
the same order, so the two always name the same tags, ``SIM_HEARTBEAT_DI`` included (PLAN §6.1).
A ``bool`` tag becomes ``BOOL`` (radix Decimal) and a ``float32`` tag ``REAL`` (radix Float): the
PLC holds engineering values whatever the Modbus analog encoding. Tag names are the scene's own,
which is what the Logix path (PLAN §6.2) and the PLC programs use; ``prefix="AREA1"``
(``--prefix AREA1``) prepends ``AREA1_`` to every name and ``AREA1 — `` to every description
and changes nothing else. Each name is checked against the Logix name rule
``^[A-Z][A-Z0-9_]{0,39}$`` and for duplicates. Each tag carries the CSV
description, plus ``[lo to hi units]`` for an analog tag, and ``ExternalAccess="Read/Write"``
because the simulator (or Modbus mapping logic) writes the inputs and reads the outputs.

**The plant's names (PLAN 5.3a).** A row with a ``plc_tag`` names a tag the plant's program
already has, so the stub follows the name's form (:mod:`openfactory.core.plctag`):

- a controller-scope name (``LT101_PV``): one controller ``Base`` tag of that name;
- a program-scope name (``Program:Transfer.P101_Run``): one tag of that name in that program's
  ``<Tags>``, under ``<Programs><Program Use="Context" Name="Transfer">``;
- a structure member, an array element or a bit (``CV201.Running``, ``Word[3]``,
  ``Line2_DI_Word.4``): **not declared**, because it is part of a tag the plant's program
  declares with a type the stub cannot know. Each is listed in an XML comment at the top of the
  file, with its scene tag, and ``openfactory export`` prints their count on standard error.

``--prefix`` applies only to rows without a ``plc_tag`` (a plant name is already the plant's full
name), and it defaults to the scene's ``scene.area`` when there is one. A row's ``plc_tag`` is
checked by the shape of :func:`~openfactory.core.plctag.parse_plc_tag` at load; the controller's
own length and case rules are not checked yet (PLAN 5.3a blocker). A scene with no ``plc_tag``
and no ``scene.area`` gives exactly the file it gave before 5.3a.

The root is ``RSLogix5000Content`` with ``SchemaRevision="1.0"``, ``SoftwareRevision="35.00"``
(default), ``TargetType="Controller"`` and ``ContainsContext="true"``, the controller wrapped as
``Use="Context"`` so Studio 5000 imports only the tags. The L5X format reference is Rockwell
Automation, Logix 5000 Controllers Import/Export Reference Manual, publication 1756-RM014
(1756-RM014A-EN-P, March 2022), which supersedes its predecessor 1756-RM084:
https://literature.rockwellautomation.com/idc/groups/literature/documents/rm/1756-rm014_-en-p.pdf
Attributes and tag layout also follow the HIS Controls L5X rules. There is no ``ExportDate`` and
no ``<Data>`` element, so the file is byte-identical for the same scene (PLAN 2.14 regenerates
it) and the tags take Studio 5000's defaults; an import into Studio 5000 has not been tested yet
(PLAN 2.3).
The text is parsed back before it is returned, so a malformed file is never written.

These are stubs for a training and demo setup, not a controller program; anything bound for a
client still needs HIS Controls PE review. OpenFactory is not a safety-rated simulation.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import quoteattr

from openfactory.core.loader import build_iotable
from openfactory.core.plctag import DECLARED, PlcTag, parse_plc_tag
from openfactory.core.schema import Scene
from openfactory.export.io_list_csv import PLC_TAG_COLUMN, io_list_rows, plant_names
from openfactory.protocols.modbus import Encoding

SOFTWARE_REVISION = "35.00"
DEFAULT_CONTROLLER = "OpenFactory"
#: Logix tag and controller names (Studio 5000: 40 characters, letter first).
LOGIX_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,39}$")
#: ``--prefix``: an area prefix, ``AREA1`` (PLAN 2.3 ruling, 2026-10-30).
AREA_PREFIX = re.compile(r"^[A-Z][A-Z0-9]*$")
CONTROLLER_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
DATA_TYPE = {"bool": ("BOOL", "Decimal"), "float32": ("REAL", "Float")}


def _cdata(text: str) -> str:
    return "<![CDATA[" + text.replace("]]>", "]]]]><![CDATA[>") + "]]>"


def _description(row: dict[str, str], units: str | None) -> str:
    """The row's description with its span, in the tag's own units: a binding's `units` label
    shows only in the CSV and CONTEXT.md (ruled 2026-11-08), not here."""
    text = row["description"]
    if row["io_type"] in ("AI", "AO") and row["range_min"] and row["range_max"]:
        units = f" {units}" if units else ""
        text += f" [{row['range_min']} to {row['range_max']}{units}]"
    return text


def undeclared_plc_tags(scene: Scene) -> list[tuple[str, PlcTag]]:
    """``(scene tag, plc_tag)`` for each plant name that is a member, element or bit, which the
    stub lists in its comment and does not declare (PLAN 5.3a)."""
    parsed = [(tag, parse_plc_tag(name)) for tag, name in plant_names(scene).items()]
    return [(tag, p) for tag, p in parsed if p.form not in DECLARED]


def _tag(name: str, row: dict[str, str], description: str) -> list[str]:
    data_type, radix = DATA_TYPE[row["dtype"]]
    return [
        f'<Tag Name={quoteattr(name)} TagType="Base" DataType="{data_type}"'
        f' Radix="{radix}" Constant="false" ExternalAccess="Read/Write">',
        f"<Description>{_cdata(description)}</Description>",
        "</Tag>",
    ]


def l5x_stub(
    scene: Scene,
    encoding: Encoding | None = None,
    *,
    scene_path: str | Path | None = None,
    write_lock: bool = True,
    controller: str = DEFAULT_CONTROLLER,
    prefix: str | None = None,
    software_revision: str = SOFTWARE_REVISION,
) -> str:
    """The L5X text (UTF-8, ``\\n`` line endings). ``scene_path`` and ``write_lock`` are as for
    the I/O list, whose rows these tags are."""
    if prefix is None:
        prefix = scene.scene.area  # PLAN 5.3a: the scene's area is the default prefix
    area = (prefix or "").rstrip("_")
    if prefix is not None and not AREA_PREFIX.match(area):
        raise ValueError(f"prefix {prefix!r}: use capitals and digits, letter first (e.g. AREA1)")
    if not CONTROLLER_NAME.match(controller):
        raise ValueError(f"controller name {controller!r} is not a valid Logix name")
    rows = io_list_rows(scene, encoding, scene_path=scene_path, write_lock=write_lock)
    table = build_iotable(scene)
    seen: set[str] = set()
    lines = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        f'<RSLogix5000Content SchemaRevision="1.0" SoftwareRevision={quoteattr(software_revision)}'
        f" TargetName={quoteattr(controller)}"
        ' TargetType="Controller" ContainsContext="true" Owner="OpenFactory">',
        f'<Controller Use="Context" Name={quoteattr(controller)}>',
        "<Tags>",
    ]
    tags: list[str] = []
    programs: dict[str, list[str]] = {}
    undeclared: list[tuple[str, PlcTag]] = []
    for row in rows:
        text = _description(row, table.tag(row["tag"]).units)
        plant = row.get(PLC_TAG_COLUMN)
        if plant:  # the plant's own name, by its form (PLAN 5.3a); no prefix
            parsed = parse_plc_tag(plant)
            if parsed.form not in DECLARED:
                undeclared.append((row["tag"], parsed))
                continue
            if plant in seen:
                raise ValueError(f"{plant}: duplicate tag")
            seen.add(plant)
            if parsed.program is not None:
                local = plant.split(".", 1)[1]
                programs.setdefault(parsed.program, []).extend(_tag(local, row, text))
            else:
                tags += _tag(plant, row, text)
            continue
        name = f"{area}_{row['tag']}" if area else row["tag"]
        if not LOGIX_NAME.match(name):
            raise ValueError(f"{name}: not a valid Logix tag name (^[A-Z][A-Z0-9_]{{0,39}}$)")
        if name in seen:
            raise ValueError(f"{name}: duplicate tag")
        seen.add(name)
        tags += _tag(name, row, f"{area} — {text}" if area else text)
    if undeclared:  # after the declaration, before the root: a comment, never a tag
        listed = [
            f"  {p.name} ({p.form} of {p.base}) for {tag}"
            for tag, p in sorted(undeclared, key=lambda u: u[1].name)
        ]
        lines[1:1] = [
            "<!-- Plant tags that are members, elements or bits of the plant's own tags. The",
            "  plant's program declares them; this file does not (OpenFactory, PLAN 5.3a):",
            *listed,
            "-->",
        ]
    lines += tags
    lines.append("</Tags>")
    if programs:
        lines.append("<Programs>")
        for program in sorted(programs):
            lines += [
                f'<Program Use="Context" Name={quoteattr(program)}>',
                "<Tags>",
                *programs[program],
                "</Tags>",
                "</Program>",
            ]
        lines.append("</Programs>")
    lines += ["</Controller>", "</RSLogix5000Content>", ""]
    text = "\n".join(lines)
    ET.fromstring(text.encode("utf-8"))  # well-formed, or raise before anything is written
    return text
