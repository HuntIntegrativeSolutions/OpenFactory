"""The scene editor's server side (PLAN 4.1a): ``openfactory edit SCENE``.

Behavior spec
-------------
:class:`EditSession` holds one scene file's text and its hash (SHA-256 of the UTF-8 bytes, the
*base* every request names). The browser sends operations (:mod:`openfactory.core.sceneedit`);
the session applies them to the text, never the browser (ruled 2026-11-10):

- ``GET /api/edit/source``: ``{name, text, base, data}``, ``data`` the file's values as written
  (parsed YAML), which the editor's forms show; a preview carries the previewed ``data`` too.
- ``POST /api/edit/preview`` ``{base, ops}``: the text with the operations applied, the lint's
  findings on it (``openfactory lint``, run on the text as if it were the file, so its lockfiles,
  library and meshes are found beside it), and the scene document the viewer draws (``null`` when
  the text does not load; the E100 finding says why). Nothing is written.
- ``POST /api/edit/save`` ``{base, ops}``: the same, then writes the file. Refused with **409**
  when ``base`` is not the hash of the file as it is on disk now (someone edited it since: the
  editor never overwrites a hand edit), and with **422** when the lint reports an error; the
  response carries the findings either way. A save returns the new ``base``. Lockfiles are never
  written (ruled 2026-11-10): new tags get addresses on the next ``run`` or ``export``.
- ``GET /api/scene`` serves the session's current document with ``"mode": "edit"``.
- ``POST /api/edit/palette`` ``{base, ops}`` (PLAN 4.1c): what can be placed
  (:func:`openfactory.core.templates.palette`, against the text with the operations applied, so a
  library composite placed but not yet saved shows as in use) and the ``.glb`` files beside the
  scene a ``mesh.file`` can name.
- ``POST /api/edit/new`` ``{base, ops, type, position}``: a new component of that type
  (:func:`openfactory.core.templates.new_component`): the editor adds it with an ``add``
  operation, after a ``use_library`` one when ``library`` is set.
- ``POST /api/edit/references`` ``{base, ops, id}`` (PLAN 4.1f): what deleting ``id`` takes
  with it, against the text with the operations applied: ``ids`` (the component and, for a
  composite instance, its parts), ``remove`` (the connections and checks an ``unreference``
  operation removes) and ``fix`` (references left for the user to change), each a list of
  ``{path, action, text}`` (:mod:`openfactory.core.references`). The editor lists them and
  sends ``delete`` and ``unreference`` only when the user confirms.
- ``GET /models/<id>.glb`` serves the mesh of the scene as last previewed, so a mesh set in the
  form is drawn before Save.

A bad operation (an unknown id, a path that is not there) is **400** naming the operation.
``openfactory edit`` serves this on loopback only, with the simulation never started and no
protocol server (ruled 2026-11-10: paused, loopback only, never with a PLC).
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml

from openfactory.core.sceneedit import apply_ops


class EditConflict(Exception):
    """The file changed on disk since the base the request names (409)."""


class EditRejected(Exception):
    """The edited scene has lint errors; nothing was written (422)."""

    def __init__(self, result: dict[str, Any]) -> None:
        super().__init__("the edited scene has lint errors")
        self.result = result


def _data(text: str) -> Any:
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError:
        return None


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class EditSession:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.text = self.path.read_text(encoding="utf-8")
        self.base = text_hash(self.text)
        self._document: dict[str, Any] | None = None
        #: Component id -> mesh file of the scene as last loaded (saved or previewed).
        self.meshes: dict[str, Path] = {}

    def _document_of(self, text: str, path: Path) -> dict[str, Any]:
        from openfactory.core.loader import build
        from openfactory.core.schema import parse_scene
        from openfactory.server.runtime import Runtime

        loaded = build(parse_scene(text, path.parent), base_dir=path.parent)
        self.meshes = dict(getattr(loaded, "meshes", {}) or {})
        doc = {**Runtime(loaded, realtime=False).scene_document(), "mode": "edit"}
        for comp in doc.get("components", []):
            mesh = comp.get("mesh")
            if isinstance(mesh, dict) and comp.get("id") in self.meshes:
                # another file for the same id must not come from the browser's cache (PLAN 4.1c)
                mesh["url"] += "?file=" + quote(self.meshes[comp["id"]].name)
        return doc

    def document(self) -> dict[str, Any]:
        if self._document is None:
            self._document = self._document_of(self.text, self.path)
        return self._document

    def _applied(self, base: str, ops: list[dict[str, Any]]) -> str:
        self._check_base(base)
        return apply_ops(self.text, ops)[0]

    def palette(self, base: str, ops: list[dict[str, Any]]) -> dict[str, Any]:
        from openfactory.core.templates import mesh_files, palette

        data = _data(self._applied(base, ops))
        return {
            "items": palette(data, self.path.parent),
            "meshes": mesh_files(self.path.parent),
        }

    def new(
        self, base: str, ops: list[dict[str, Any]], type_name: str, position: Any
    ) -> dict[str, Any]:
        from openfactory.core.templates import new_component

        data = _data(self._applied(base, ops))
        return new_component(type_name, data, self.path.parent, position)

    def references(self, base: str, ops: list[dict[str, Any]], comp_id: str) -> dict[str, Any]:
        from openfactory.core.references import names_of, references

        text = self._applied(base, ops)
        data = _data(text)
        if not any(
            isinstance(c, dict) and c.get("id") == comp_id
            for c in (data or {}).get("components") or []
        ):
            raise ValueError(f"no component {comp_id!r} in the scene")
        ids = names_of(text, self.path.parent, comp_id)
        found = [r.document() for r in references(data, ids)]
        return {
            "id": comp_id,
            "ids": ids,
            "remove": [r for r in found if r["action"] == "remove"],
            "fix": [r for r in found if r["action"] == "fix"],
        }

    def source(self) -> dict[str, Any]:
        """The text, its base, and ``data``: the file's values as written (YAML parsed, no
        defaults filled in), which the editor's forms show."""
        return {
            "name": self.path.name,
            "text": self.text,
            "base": self.base,
            "data": _data(self.text),
        }

    def _check_base(self, base: str) -> None:
        on_disk = text_hash(self.path.read_text(encoding="utf-8"))
        if base != on_disk:
            raise EditConflict(f"{self.path.name} changed on disk since this edit began; reload it")
        if on_disk != self.base:  # changed on disk and the request knows it: start from disk
            self.text, self.base, self._document = (
                self.path.read_text(encoding="utf-8"),
                on_disk,
                None,
            )

    def preview(self, base: str, ops: list[dict[str, Any]]) -> dict[str, Any]:
        """Apply ``ops`` to the text at ``base``; ValueError for a bad op."""
        from openfactory.lint import lint_scene

        text = self._applied(base, ops)
        findings = lint_scene(self.path, text=text)
        try:
            scene = self._document_of(text, self.path)
        except Exception:  # noqa: BLE001 - the E100 finding already says why
            scene = None
        return {
            "base": base,
            "text": text,
            "changed": text != self.text,
            "findings": [asdict(f) for f in findings],
            "errors": sum(f.level == "error" for f in findings),
            "scene": scene,
            "data": _data(text),
        }

    def save(self, base: str, ops: list[dict[str, Any]]) -> dict[str, Any]:
        result = self.preview(base, ops)
        if result["errors"]:
            raise EditRejected(result)
        self._check_base(base)  # once more, right before the write
        self.path.write_text(result["text"], encoding="utf-8")
        self.text, self.base = result["text"], text_hash(result["text"])
        self._document = result["scene"]
        return {"base": self.base, "findings": result["findings"], "saved": self.path.name}
