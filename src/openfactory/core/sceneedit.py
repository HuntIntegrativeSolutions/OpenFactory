"""Scene edit operations (PLAN 4.1a): what the editor sends, applied to a scene's YAML text.

Behavior spec
-------------
The scene editor never writes YAML itself (ruled 2026-11-10). It sends a list of operations, and
:func:`apply_ops` applies them, in order, to the file's text through the 4.7
:class:`~openfactory.core.migrate.Document`: every change is a splice at the positions PyYAML's
composer reports, so every comment, blank line, quote and key order outside the edited text stays
byte for byte.

Operations (JSON objects). A ``path`` is a list of keys from the top of the scene; inside
``components`` (or any list of mappings with ``id``) a component is named by its id, so
``["components", "CONV_01", "speed_mps"]``:

- ``{"op": "set", "path": [...], "value": V}``: sets a scalar or a flow value (a position
  ``[x, y, z]``, a binding ``{tag: X}``), or adds the key as the mapping's last if it is not there.
  A block mapping is set key by key.
  A key under a component mapping it does not have yet (``io`` on a type with no required
  port) adds the mapping in flow style, ``io: {enable: EMT_01_ENABLE_DO}``. A key under
  ``checks`` when the scene has no ``checks:`` creates the block just above
  ``components:`` (the scene settings form).
- ``{"op": "set_raw", "path": [...], "literal": "text"}``: the same with YAML source text, as the
  inverse of a set uses to restore the exact original spelling.
- ``{"op": "unset", "path": [...]}``: removes the key (its whole lines).
- ``{"op": "move", "id": "CONV_02", "position": [x, y, z], "rotation_deg": r}``: a set of the
  position and, when given, the rotation.
- ``{"op": "add", "component": {...}}``: appends a component, written in the shipped scenes'
  style (block keys, flow lists and small mappings, a nested block for ``io``-like mappings).
- ``{"op": "delete", "id": "X"}``: removes the component and the comment lines directly above it
  (ruled 2026-11-10).
- ``{"op": "use_library", "name": "accumulation_conveyor"}``: adds ``{library: name}`` to the
  ``composites:`` block (PLAN 4.1c), creating the block just above ``components:`` if the scene
  has none; nothing if the scene already uses it.
- ``{"op": "connect", "from": "CONV_01", "to": "CONV_02"}``: adds ``{from: A, to: B}`` to the
  ``connections:`` list (PLAN 4.1d, what a snap adds), as a new ``connections:`` block at the end
  of the file if the scene has none; nothing if the scene already has that connection.
- ``{"op": "unreference", "ids": ["TK_01", ...]}``: removes every ``connections:`` and
  ``checks:`` entry naming one of the ids (PLAN 4.1f, :mod:`openfactory.core.references`); the
  editor sends it with a ``delete``. References a component cannot work without (a pipe's end, a
  heater's tank) are not touched: the preview names them.
- ``{"op": "splice", "at": i, "remove": n, "insert": "text"}``: a raw text splice, only what an
  inverse uses to put a deleted component back exactly.

:func:`apply_ops` returns the new text and, for each operation, its inverse; applying the
inverses in reverse order gives the original bytes back (``tests/unit/test_sceneedit.py`` does,
on every shipped scene). Errors (an unknown id, a path that is not there, a bad op) raise
``ValueError`` naming the operation.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

import yaml

from openfactory.core.migrate import Document

Op = dict[str, Any]
_PLAIN = re.compile(r"^[A-Za-z_][A-Za-z0-9_ ./()%+-]*$")


def emit(value: Any) -> str:
    """One value as YAML source text, flow style for lists and mappings."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{value!r} is not a scene value")
        text = repr(value)
        return text
    if isinstance(value, str):
        plain = _PLAIN.match(value) and value == value.strip()
        if plain and yaml.safe_load(value) == value and ": " not in value:
            return value
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list | tuple):
        return "[" + ", ".join(emit(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{emit_key(k)}: {emit(v)}" for k, v in value.items()) + "}"
    raise ValueError(f"{value!r} is not a scene value")


def emit_key(key: Any) -> str:
    if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
        raise ValueError(f"{key!r} is not a scene key")
    return key


def emit_item(component: dict[str, Any]) -> list[str]:
    """A component as block lines relative to its dash: ``- id: X`` then two-space keys. A
    mapping whose values are mappings (``io``) is a nested block; anything else is flow."""
    if not component:
        raise ValueError("an empty component")
    lines: list[str] = []
    for i, (key, value) in enumerate(component.items()):
        lead = "- " if i == 0 else "  "
        nested = (
            isinstance(value, dict)
            and value
            and all(isinstance(v, dict | str) for v in value.values())
        )
        if nested and i > 0:
            lines.append(f"{lead}{emit_key(key)}:")
            lines += [f"    {emit_key(k)}: {emit(v)}" for k, v in value.items()]
        else:
            lines.append(f"{lead}{emit_key(key)}: {emit(value)}")
    return lines


def _resolve(doc: Document, path: list[Any]) -> tuple[Any, ...]:
    """A path with component ids turned into list indexes."""
    out: list[Any] = []
    for step in path:
        if isinstance(step, str) and out:
            _, node = doc.find(tuple(out))
            if isinstance(node, yaml.SequenceNode):
                for i, item in enumerate(node.value):
                    if isinstance(item, yaml.MappingNode) and any(
                        k.value == "id" and v.value == step for k, v in item.value
                    ):
                        out.append(i)
                        break
                else:
                    raise ValueError(f"no item with id {step!r} in {'.'.join(map(str, out))}")
                continue
        out.append(step)
    return tuple(out)


def _index_of(doc: Document, comp_id: str) -> int:
    return _resolve(doc, ["components", comp_id])[-1]


#: Top-level blocks the editor may create when a set names a key under one that is not there
#: (the scene settings form, 2026-11-13); each goes just above ``components:``.
_NEW_BLOCKS = ("checks",)


def _set(doc: Document, path: list[Any], literal: str) -> Op:
    """Set (or add) the value at ``path``; returns the inverse."""
    if len(path) == 2 and path[0] in _NEW_BLOCKS and not doc.has((path[0],)):
        block = f"{path[0]}:\n  {emit_key(path[1])}: {literal}\n"
        if doc.has(("components",)):
            key, _ = doc.find(("components",))
            assert key is not None
            at = doc._line_start(key.start_mark.index)
        else:
            at = len(doc.text)
        return _inserted(doc.splice(at, at, block))
    resolved = _resolve(doc, path[:-1]) + (path[-1],)
    if len(path) >= 4 and not doc.has(resolved[:-1]) and doc.has(resolved[:-2]):
        # the first key under a mapping the component does not have yet (a first `io` binding
        # on a type with no required port): the mapping is added in flow style
        doc.add_key(resolved[:-2], emit_key(path[-2]), f"{{{emit_key(path[-1])}: {literal}}}")
        return {"op": "unset", "path": path[:-1]}
    if doc.has(resolved):
        start, end, old = doc.set_node(resolved, literal)
        return {"op": "set_raw", "path": path, "literal": old}
    doc.add_key(resolved[:-1], emit_key(path[-1]), literal)
    return {"op": "unset", "path": path}


_LIBRARY_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


def _inserted(span: tuple[int, int, str]) -> Op:
    start, end, old = span
    return {"op": "splice", "at": start, "remove": end - start, "insert": old}


def _use_library(doc: Document, name: str) -> Op:
    """Add ``{library: name}`` to ``composites:`` unless it is there: after the last entry, or
    as a new ``composites:`` block just above ``components:`` (at the end if there is none)."""
    if not _LIBRARY_NAME.match(name):
        raise ValueError(f"{name!r} is not a library composite name")
    entry = f"{{library: {name}}}"
    if doc.has(("composites",)):
        _, node = doc.find(("composites",))
        if not isinstance(node, yaml.SequenceNode):
            raise ValueError("composites: not a list")
        for item in node.value:
            if isinstance(item, yaml.MappingNode) and any(
                k.value == "library" and v.value == name for k, v in item.value
            ):
                return {"op": "batch", "ops": []}  # already used
        if node.flow_style:
            if node.value:
                at = doc._leaf_end(node.value[-1])
                return _inserted(doc.splice(at, at, f", {entry}"))
            at = node.start_mark.index + 1
            return _inserted(doc.splice(at, at, entry))
        if node.value:
            return _inserted(doc.append_item(("composites",), [f"- {entry}"]))
        raise ValueError("composites: an empty block list")
    block = f"composites:\n  - {entry}\n"
    if doc.has(("components",)):
        key, _ = doc.find(("components",))
        assert key is not None
        at = doc._line_start(key.start_mark.index)
        return _inserted(doc.splice(at, at, block))
    at = len(doc.text)
    lead = "" if doc.text.endswith("\n") or not doc.text else "\n"
    return _inserted(doc.splice(at, at, lead + block))


_COMPONENT_ID = re.compile(r"^[A-Z][A-Z0-9_]{0,39}$")


def _connect(doc: Document, up: str, down: str) -> Op:
    """Add ``{from: up, to: down}`` to ``connections:`` unless it is there: after the last entry,
    or as a new ``connections:`` block at the end of the file."""
    for name in (up, down):
        if not _COMPONENT_ID.match(name):
            raise ValueError(f"{name!r} is not a component id")
    if up == down:
        raise ValueError(f"{up} cannot feed itself")
    entry = f"{{from: {up}, to: {down}}}"
    if doc.has(("connections",)):
        _, node = doc.find(("connections",))
        if not isinstance(node, yaml.SequenceNode):
            raise ValueError("connections: not a list")
        for item in node.value:
            if isinstance(item, yaml.MappingNode):
                pair = {k.value: v.value for k, v in item.value}
                if pair.get("from") == up and pair.get("to") == down:
                    return {"op": "batch", "ops": []}  # already joined
        if node.flow_style:
            if node.value:
                at = doc._leaf_end(node.value[-1])
                return _inserted(doc.splice(at, at, f", {entry}"))
            at = node.start_mark.index + 1
            return _inserted(doc.splice(at, at, entry))
        if node.value:
            return _inserted(doc.append_item(("connections",), [f"- {entry}"]))
        raise ValueError("connections: an empty block list")
    at = len(doc.text)
    lead = "" if doc.text.endswith("\n") or not doc.text else "\n"
    return _inserted(doc.splice(at, at, f"{lead}connections:\n  - {entry}\n"))


def _apply(doc: Document, op: Op) -> Op:
    kind = op.get("op")
    if kind == "set":
        return _set(doc, list(op["path"]), emit(op["value"]))
    if kind == "set_raw":
        return _set(doc, list(op["path"]), str(op["literal"]))
    if kind == "unset":
        path = list(op["path"])
        start, _, old = doc.delete_key(_resolve(doc, path[:-1]) + (path[-1],))
        return {"op": "splice", "at": start, "remove": 0, "insert": old}
    if kind == "move":
        comp = op["id"]
        inverses = [_set(doc, ["components", comp, "position"], emit(list(op["position"])))]
        if op.get("rotation_deg") is not None:
            rot = ["components", comp, "rotation_deg"]
            inverses.append(_set(doc, rot, emit(op["rotation_deg"])))
        return {"op": "batch", "ops": inverses[::-1]}
    if kind == "add":
        component = op["component"]
        if not isinstance(component, dict) or "id" not in component:
            raise ValueError("add: a component needs an id")
        doc.append_item(("components",), emit_item(component))
        return {"op": "delete", "id": component["id"]}
    if kind == "delete":
        start, _, old = doc.delete_item(("components",), _index_of(doc, op["id"]))
        return {"op": "splice", "at": start, "remove": 0, "insert": old}
    if kind == "unreference":
        from openfactory.core.references import remove_references

        ids = op["ids"]
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            raise ValueError("unreference: ids is a list of component ids")
        return {"op": "batch", "ops": [_inserted(s) for s in remove_references(doc, ids)]}
    if kind == "use_library":
        return _use_library(doc, str(op["name"]))
    if kind == "connect":
        return _connect(doc, str(op["from"]), str(op["to"]))
    if kind == "splice":
        at, remove = int(op["at"]), int(op.get("remove", 0))
        start, end, old = doc.splice(at, at + remove, str(op.get("insert", "")))
        return {"op": "splice", "at": start, "remove": end - start, "insert": old}
    if kind == "batch":
        return {"op": "batch", "ops": [_apply(doc, o) for o in op["ops"]][::-1]}
    raise ValueError(f"unknown op {kind!r}")


def apply_ops(text: str, ops: list[Op]) -> tuple[str, list[Op]]:
    """The text with ``ops`` applied in order, and each op's inverse (apply them in reverse
    order to undo)."""
    doc = Document(text)
    inverses = []
    for n, op in enumerate(ops):
        try:
            inverses.append(_apply(doc, op))
        except (KeyError, IndexError, ValueError, TypeError, yaml.YAMLError) as exc:
            detail = exc.args[0] if exc.args else exc
            raise ValueError(f"op {n + 1} ({op.get('op')}): {detail}") from None
    return doc.text, inverses


def undo(text: str, inverses: list[Op]) -> str:
    """Apply inverses from :func:`apply_ops` in reverse order."""
    return apply_ops(text, inverses[::-1])[0]
