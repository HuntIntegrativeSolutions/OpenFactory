"""What else in a scene names a component (PLAN 4.1f): the editor's Delete removes those references
with it, and lists them before the user confirms.

Behavior spec
-------------
:func:`names_of` is the set of ids a deleted component takes with it: its own id, and for a
composite instance (PLAN 2.16) every component expanded from it (``GC_01_BELT``, nested parts
too), read from the scene as it loads; when the scene does not load, the id alone.

:func:`references` lists, for those ids, every place in the scene (parsed YAML, values as
written) that names one, as a :class:`Reference` with the ``path`` to it and what Delete does:

- **removed** (``action == "remove"``), because the scene loads without them:
  - a ``connections:`` entry whose ``from`` or ``to`` is one of the ids;
  - a ``checks:`` entry naming one: a key of ``parts_reached``, ``level_within``,
    ``max_starts``, ``max_reversals``, ``fractions_within`` or ``temperature_within``, or an
    item of ``never_overflow`` or ``never_dry``. A check left empty goes too, and ``checks:``
    itself when nothing is left in it (and ``connections:`` when its last entry goes);
- **left to fix** (``action == "fix"``), because the component that holds it cannot work without
  one and only the user knows the replacement: a link's ``from`` or ``to`` (a pipe, valve or
  pump end, ``TK_01.outlet`` naming ``TK_01``), a heater's ``tank``, a transmitter's
  ``measures``, a VFD's ``drives``, and an entry of an I/O binding's ``from`` (a DI combined from
  another component's port). They stay as written; the preview's load error names each one
  until it is changed.

:func:`remove_references` applies the removals to a :class:`~openfactory.core.migrate.Document`
one at a time, re-reading the text after each, so every splice is exact and comments and order
elsewhere stay byte for byte; it returns the splices' inverses, latest first.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from openfactory.core.migrate import Document

#: Checks keyed by a component id.
CHECK_MAPS = (
    "parts_reached",
    "level_within",
    "max_starts",
    "max_reversals",
    "fractions_within",
    "temperature_within",
)
#: Checks that list component ids.
CHECK_LISTS = ("never_overflow", "never_dry")
#: Component fields that name another component (``ID`` or ``ID.nozzle``); each is required.
REFERENCE_FIELDS = ("from", "to", "tank", "measures", "drives")


@dataclass(frozen=True)
class Reference:
    path: tuple[Any, ...]
    action: Literal["remove", "fix"]
    text: str

    def document(self) -> dict[str, Any]:
        return {"path": list(self.path), "action": self.action, "text": self.text}


def names_of(text: str, scene_dir: Path, comp_id: str) -> list[str]:
    """``comp_id`` and every component expanded from it (a composite instance's parts)."""
    from openfactory.core.schema import parse_scene

    try:
        scene = parse_scene(text, scene_dir)
    except Exception:  # noqa: BLE001 - a scene that does not load: the id alone
        return [comp_id]
    ids = [comp_id]
    grown = True
    while grown:
        grown = False
        for comp in scene.components:
            if comp.id not in ids and scene.composite_of(comp.id) in ids:
                ids.append(comp.id)
                grown = True
    return ids


def _named(value: Any, ids: set[str]) -> bool:
    return isinstance(value, str) and value.partition(".")[0] in ids


def references(data: Any, ids: list[str] | set[str]) -> list[Reference]:
    """Every reference to ``ids`` in ``data`` (the scene as parsed YAML), in file order."""
    names = set(ids)
    out: list[Reference] = []
    if not isinstance(data, dict):
        return out
    for i, conn in enumerate(data.get("connections") or []):
        if isinstance(conn, dict) and (conn.get("from") in names or conn.get("to") in names):
            out.append(
                Reference(("connections", i), "remove",
                          f"connection {conn.get('from')} -> {conn.get('to')}")
            )  # fmt: skip
    checks = data.get("checks")
    if isinstance(checks, dict):
        for key in CHECK_MAPS:
            entries = checks.get(key)
            if isinstance(entries, dict):
                for cid in entries:
                    if cid in names:
                        out.append(Reference(("checks", key, cid), "remove", f"check {key}: {cid}"))
        for key in CHECK_LISTS:
            entries = checks.get(key)
            if isinstance(entries, list):
                for i, cid in enumerate(entries):
                    if cid in names:
                        out.append(Reference(("checks", key, i), "remove", f"check {key}: {cid}"))
    for comp in data.get("components") or []:
        if not isinstance(comp, dict) or comp.get("id") in names:
            continue
        cid = comp.get("id")
        for field in REFERENCE_FIELDS:
            if _named(comp.get(field), names):
                out.append(
                    Reference(("components", cid, field), "fix",
                              f"{cid}.{field} names {comp[field]}")
                )  # fmt: skip
        io = comp.get("io")
        if isinstance(io, dict):
            for port, binding in io.items():
                sources = binding.get("from") if isinstance(binding, dict) else None
                listed = sources if isinstance(sources, list) else [sources]
                for ref in listed:
                    if _named(ref, names):
                        out.append(
                            Reference(("components", cid, "io", port, "from"), "fix",
                                      f"{cid}.io.{port} combines {ref}")
                        )  # fmt: skip
    return out


def _remove(doc: Document, ref: Reference) -> list[tuple[int, int, str]]:
    """Remove one reference; the splices made, in order."""
    path = ref.path
    spans: list[tuple[int, int, str]] = []
    if isinstance(path[-1], int):
        spans.append(doc.remove_item(path[:-1], path[-1]))
    else:
        spans.append(doc.delete_key(path))
    # a check left empty goes, then checks: (or connections:) itself when nothing is left in it
    parents = [path[:2], path[:1]] if path[0] == "checks" else [path[:1]]
    for parent in parents:
        if not doc.has(parent):
            continue
        _, node = doc.find(parent)
        if isinstance(node, yaml.MappingNode | yaml.SequenceNode) and not node.value:
            spans.append(doc.delete_key(parent))
        elif isinstance(node, yaml.ScalarNode) and node.value == "":
            spans.append(doc.delete_key(parent))  # a block key with nothing under it
    return spans


def remove_references(doc: Document, ids: list[str]) -> list[tuple[int, int, str]]:
    """Remove every ``remove`` reference to ``ids``; the splices made, latest first."""
    spans: list[tuple[int, int, str]] = []
    for _ in range(10_000):  # each pass removes one; a bound against a loop
        todo = [r for r in references(yaml.safe_load(doc.text), ids) if r.action == "remove"]
        if not todo:
            return spans[::-1]
        spans += _remove(doc, todo[-1])  # the last in file order: earlier positions stay put
    raise ValueError("too many references to remove")
