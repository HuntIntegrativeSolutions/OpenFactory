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

The root is ``RSLogix5000Content`` with ``SchemaRevision="1.0"``, ``SoftwareRevision="35.00"``
(default), ``TargetType="Controller"`` and ``ContainsContext="true"``, the controller wrapped as
``Use="Context"`` so Studio 5000 imports only the tags. The L5X format reference is Rockwell
Automation, Logix 5000 Controllers Import/Export Reference Manual, publication 1756-RM084:
https://literature.rockwellautomation.com/idc/groups/literature/documents/rm/1756-rm084_-en-p.pdf
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

from openfactory.core.schema import Scene
from openfactory.export.io_list_csv import io_list_rows
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


def _description(row: dict[str, str]) -> str:
    text = row["description"]
    if row["io_type"] in ("AI", "AO") and row["range_min"] and row["range_max"]:
        units = f" {row['units']}" if row["units"] else ""
        text += f" [{row['range_min']} to {row['range_max']}{units}]"
    return text


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
    area = (prefix or "").rstrip("_")
    if prefix is not None and not AREA_PREFIX.match(area):
        raise ValueError(f"prefix {prefix!r}: use capitals and digits, letter first (e.g. AREA1)")
    if not CONTROLLER_NAME.match(controller):
        raise ValueError(f"controller name {controller!r} is not a valid Logix name")
    rows = io_list_rows(scene, encoding, scene_path=scene_path, write_lock=write_lock)
    seen: set[str] = set()
    lines = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        f'<RSLogix5000Content SchemaRevision="1.0" SoftwareRevision={quoteattr(software_revision)}'
        f" TargetName={quoteattr(controller)}"
        ' TargetType="Controller" ContainsContext="true" Owner="OpenFactory">',
        f'<Controller Use="Context" Name={quoteattr(controller)}>',
        "<Tags>",
    ]
    for row in rows:
        name = f"{area}_{row['tag']}" if area else row["tag"]
        if not LOGIX_NAME.match(name):
            raise ValueError(f"{name}: not a valid Logix tag name (^[A-Z][A-Z0-9_]{{0,39}}$)")
        if name in seen:
            raise ValueError(f"{name}: duplicate tag")
        seen.add(name)
        data_type, radix = DATA_TYPE[row["dtype"]]
        description = f"{area} — {_description(row)}" if area else _description(row)
        lines += [
            f'<Tag Name="{name}" TagType="Base" DataType="{data_type}" Radix="{radix}"'
            ' Constant="false" ExternalAccess="Read/Write">',
            f"<Description>{_cdata(description)}</Description>",
            "</Tag>",
        ]
    lines += ["</Tags>", "</Controller>", "</RSLogix5000Content>", ""]
    text = "\n".join(lines)
    ET.fromstring(text.encode("utf-8"))  # well-formed, or raise before anything is written
    return text
