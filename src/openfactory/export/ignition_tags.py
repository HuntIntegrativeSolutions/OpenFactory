"""Ignition 8.3 tag JSON (PLAN task 2.4): ``openfactory export SCENE --format ignition``.

Behavior spec
-------------
:func:`ignition_tags` turns a scene into an Ignition tag import file in the structure the HIS
Controls conventions give (``~/.claude/rules/ignition-conventions.md``, "Full Tag Export
Structure"; ``docs/ignition.md`` here):

- the root is ``{"version": "8.3.0", "tags": [...]}``;
- first a ``_types_`` folder with one ``UdtType`` per component type the scene uses, named
  ``OF_<Type>`` (``OF_BeltConveyor``) so it cannot clash with a client's own UDTs, imported before
  any instance (the vendor-docs store's Ignition 8.3 tag export schema: "UdtType must be imported
  before any UdtInstance that references it");
- then one ``Folder`` per area holding one ``UdtInstance`` per component, named by component id.
  A scene has no areas yet, so there is one area: ``--area`` or the scene name in capitals
  (``SORTING_BY_HEIGHT``). ``SIM_HEARTBEAT_DI`` (PLAN §6.1), which belongs to no component, is an
  ``AtomicTag`` named ``SIM_HEARTBEAT`` in the area folder.

A UDT's members are the union of the ports bound by any instance of that type in the scene, one
``AtomicTag`` each, named by the port in PascalCase (``run`` -> ``Run``), ``Boolean`` or
``Float4``, read-only for DI/AI and read/write for DO/AO, with history on AI members only (the
process values). Each member's OPC item path takes the tag name from a UDT parameter,
``Tag_<Member>``, because a scene's tag names do not follow one pattern per type; each instance
sets ``InstanceName`` and those parameters. An instance that does not bind one of its type's
ports overrides that member to ``enabled: false``; an analog member's ``engUnit``, ``engLow`` and
``engHigh`` are overridden per instance, since ranges can differ (a distance sensor's beam).

Two sources for the OPC item paths:

- OpenFactory's own OPC UA server (default): ``opcServer`` is the name of the OPC connection
  made in the gateway for it (``--opc-server``, default ``OpenFactory``) and the path is
  ``nsu=urn:openfactory:sim;s=<TAG>``, by namespace URI so it does not depend on the namespace
  index (:mod:`openfactory.protocols.opcua`);
- a Logix controller through Ignition's driver (``--device NAME``): ``opcServer`` ``Ignition OPC
  UA Server`` and path ``ns=1;s=[NAME]<TAG>``, the controller-scoped form, for tags made from the
  2.3 L5X stubs. The device name is the gateway's alias, never an address.

No alarms: a scene has no rationalized alarm setpoints (ISA-18.2), so none are invented. The
output is deterministic (sorted keys, two-space indent). It has not been imported into a gateway
in this repo's tests; anything for a client needs PE review. Not a safety-rated simulation.
"""

from __future__ import annotations

import json
import re
from typing import Any

from openfactory.core.config import OPCUA_NAMESPACE as NAMESPACE
from openfactory.core.heartbeat import HEARTBEAT_TAG
from openfactory.core.schema import Scene

VERSION = "8.3.0"
DEFAULT_OPC_SERVER = "OpenFactory"
LOGIX_OPC_SERVER = "Ignition OPC UA Server"
DATA_TYPE = {"bool": "Boolean", "float32": "Float4", "int16": "Int2"}
WRITABLE = frozenset({"DO", "AO"})
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def pascal(name: str) -> str:
    return "".join(part[:1].upper() + part[1:] for part in name.split("_"))


def udt_name(component_type: str) -> str:
    return f"OF_{pascal(component_type)}"


def default_area(scene: Scene) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", scene.scene.name.upper()).strip("_")


def _path(tag_param: str, device: str | None) -> str:
    if device is not None:
        return f"ns=1;s=[{device}]{{{tag_param}}}"
    return f"nsu={NAMESPACE};s={{{tag_param}}}"


def _atomic(name: str, kind: str, dtype: str, path: str, server: str, doc: str) -> dict[str, Any]:
    return {
        "name": name,
        "tagType": "AtomicTag",
        "valueSource": "opc",
        "opcServer": server,
        "opcItemPath": path,
        "dataType": DATA_TYPE[dtype],
        "accessRights": "Read_Write" if kind in WRITABLE else "Read_Only",
        "historyEnabled": kind == "AI",
        "documentation": doc,
        "enabled": True,
    }


def ignition_tags(
    scene: Scene,
    *,
    area: str | None = None,
    opc_server: str = DEFAULT_OPC_SERVER,
    device: str | None = None,
) -> dict[str, Any]:
    """The tag import document (see the module docstring)."""
    from openfactory.core.loader import build_iotable  # the I/O table, heartbeat included

    area = area or default_area(scene)
    if not NAME.match(area):
        raise ValueError(f"area {area!r} is not a valid Ignition tag name")
    if device is not None and not NAME.match(device):
        raise ValueError(f"device {device!r} is not a valid Ignition device name")
    server = LOGIX_OPC_SERVER if device is not None else opc_server
    table = build_iotable(scene)
    components = {c.id: c for c in scene.components}
    bindings = scene.tag_bindings()

    # member name -> (kind, dtype, port description), per component type, in first-seen order
    members: dict[str, dict[str, tuple[str, str, str]]] = {}
    for b in bindings:
        ctype = components[b.component_id].type
        tag = table.tag(b.name)
        members.setdefault(ctype, {}).setdefault(
            pascal(b.port.name), (tag.kind, tag.dtype, b.description)
        )
    types = []
    for ctype in sorted(members):
        params = {"InstanceName": {"dataType": "String", "value": ""}}
        tags = []
        for member, (kind, dtype, doc) in members[ctype].items():
            params[f"Tag_{member}"] = {"dataType": "String", "value": ""}
            tags.append(_atomic(member, kind, dtype, _path(f"Tag_{member}", device), server, doc))
        types.append(
            {"name": udt_name(ctype), "tagType": "UdtType", "parameters": params, "tags": tags}
        )

    instances = []
    for comp in scene.components:
        own = [b for b in bindings if b.component_id == comp.id]
        if not own:
            continue
        params = {"InstanceName": {"dataType": "String", "value": comp.id}}
        overrides = []
        bound = {pascal(b.port.name): b for b in own}
        for member in members[comp.type]:
            b = bound.get(member)
            params[f"Tag_{member}"] = {"dataType": "String", "value": b.name if b else ""}
            if b is None:
                overrides.append({"name": member, "enabled": False})
                continue
            tag = table.tag(b.name)
            if tag.kind in ("AI", "AO") and tag.range is not None:
                override = {"name": member, "engLow": tag.range[0], "engHigh": tag.range[1]}
                if tag.units:
                    override["engUnit"] = tag.units
                overrides.append(override)
        instance: dict[str, Any] = {
            "name": comp.id,
            "tagType": "UdtInstance",
            "typeId": udt_name(comp.type),
            "parameters": params,
            "documentation": comp.description,
        }
        if overrides:
            instance["tags"] = overrides
        instances.append(instance)
    beat = table.tag(HEARTBEAT_TAG)
    heartbeat = _atomic("SIM_HEARTBEAT", beat.kind, beat.dtype, "", server, beat.description)
    heartbeat["opcItemPath"] = (
        f"ns=1;s=[{device}]{HEARTBEAT_TAG}" if device else f"nsu={NAMESPACE};s={HEARTBEAT_TAG}"
    )
    return {
        "version": VERSION,
        "tags": [
            {"name": "_types_", "tagType": "Folder", "tags": types},
            {"name": area, "tagType": "Folder", "tags": [*instances, heartbeat]},
        ],
    }


def ignition_tags_json(scene: Scene, **kwargs: Any) -> str:
    doc = ignition_tags(scene, **kwargs)
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
