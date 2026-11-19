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
- ``POST /api/edit/mount`` ``{base, ops, id, conveyor, rail, along}`` (PLAN 4.1g1): where a
  device dropped on a belt's ``rail_left`` or ``rail_right``, ``along`` metres from its infeed
  end, goes: ``position`` and ``rotation_deg`` by :func:`openfactory.core.mounts.place_on_rail`,
  the one copy of the drop rules (ruled 2026-11-14), and ``swing_deg`` when a diverter's must
  change sign to swing over the belt. The editor sends them as ``set`` operations.
- ``GET /models/<id>.glb`` serves the mesh of the scene as last previewed, so a mesh set in the
  form is drawn before Save.

**A plant** (PLAN 5.4e, design note §4.3): ``EditSession(PLANT, area=NAME)`` edits that area's
file; without ``area`` it edits the plant file itself (its own components and its connections
across areas). Either way the session edits **one file**: the text, base, operations, ``source``
and save are that file's. What it shows is the whole plant: the preview document is the plant
merged with the edited file's unsaved text in place of the file on disk
(:func:`openfactory.core.plant.merge`'s ``overrides``), each component carrying ``source`` (its
file, relative to the plant's folder) and ``editable`` (whether it is in the edited file); the
document's ``edit`` says which file is edited and, in ``areas``, each area's file and name, so a
dimmed component names the command that edits it (ruled 2026-11-18). Preview and save lint the
**whole plant** with that text, so a clash with another file (an id, a scene tag or a ``plc_tag``)
is an error naming the other file, and a text that would make the plant fail to load is refused
(**422**) with the loader's message (``load_error``), nothing written. Save writes the edited file
only; no other file of the plant, its lockfile or its ``CONTEXT.md`` is written. A new component's
id is numbered past the ids of the whole plant.

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


def is_plant(path: Path) -> bool:
    """Whether the file at ``path`` is a plant file (``include:`` at its top level)."""
    data = _data(path.read_text(encoding="utf-8"))
    return isinstance(data, dict) and data.get("include") is not None


class EditSession:
    def __init__(self, path: str | Path, area: str | None = None) -> None:
        #: The plant file, when the file edited is a plant's or one of its areas (PLAN 5.4e).
        self.plant: Path | None = None
        self.area = area
        path = Path(path)
        if is_plant(path):
            from openfactory.core.schema import load_scene

            self.plant = path
            areas = load_scene(path).plant.areas  # type: ignore[union-attr]
            if area is not None:
                match = next((a for a in areas if a.name == area), None)
                if match is None:
                    raise ValueError(f"{path.name}: no area {area!r}; its areas are "
                                     f"{', '.join(a.name for a in areas)}")  # fmt: skip
                path = match.path
        elif area is not None:
            raise ValueError(f"{path.name} has no include:, so it is not a plant and has no areas")
        self.path = path
        self.text = self.path.read_text(encoding="utf-8")
        self.base = text_hash(self.text)
        self._document: dict[str, Any] | None = None
        #: Component id -> mesh file of the scene as last loaded (saved or previewed).
        self.meshes: dict[str, Path] = {}

    @property
    def name(self) -> str:
        """The edited file as the user knows it: relative to the plant's folder in a plant."""
        if self.plant is None:
            return self.path.name
        return self.path.resolve().relative_to(self.plant.resolve().parent).as_posix()

    def _scene_of(self, text: str) -> tuple[Any, Path]:
        """The scene a text of the edited file makes, and the folder it is built from: the plant
        merged with that text in a plant, else the file alone."""
        from openfactory.core.schema import parse_scene

        if self.plant is None:
            return parse_scene(text, self.path.parent, source=self.path), self.path.parent
        plant_text, overrides = self._plant_inputs(text)
        scene = parse_scene(plant_text, self.plant.parent, source=self.plant, overrides=overrides)
        return scene, self.plant.parent

    def _plant_inputs(self, text: str) -> tuple[str, dict[Path, str] | None]:
        """The plant file's text and the overrides that put ``text`` in the edited file's place."""
        assert self.plant is not None
        if self.area is None:
            return text, None
        return self.plant.read_text(encoding="utf-8"), {self.path.resolve(): text}

    def _document_of(self, text: str, path: Path) -> dict[str, Any]:
        from openfactory.core.loader import build
        from openfactory.server.runtime import Runtime

        scene, folder = self._scene_of(text)
        loaded = build(scene, base_dir=folder)
        self.meshes = dict(getattr(loaded, "meshes", {}) or {})
        doc = {**Runtime(loaded, realtime=False).scene_document(), "mode": "edit"}
        for comp in doc.get("components", []):
            mesh = comp.get("mesh")
            if isinstance(mesh, dict) and comp.get("id") in self.meshes:
                # another file for the same id must not come from the browser's cache (PLAN 4.1c)
                mesh["url"] += "?file=" + quote(self.meshes[comp["id"]].name)
        if scene.plant is not None:  # PLAN 5.4e: each component's file, and whether it is ours
            mine = self.name
            for comp in doc.get("components", []):
                owner = (comp.get("composite") or {}).get("id") or comp.get("id")
                comp["source"] = scene.plant.sources.get(str(owner), self.plant.name)
                comp["editable"] = comp["source"] == mine
            # each area's file -> its name, so a dimmed component says which command edits it
            folder = self.plant.resolve().parent
            areas = {a.path.resolve().relative_to(folder).as_posix(): a.name
                     for a in scene.plant.areas}  # fmt: skip
            doc["edit"] = {"file": mine, "plant": self.plant.name, "area": self.area,
                           "areas": areas}  # fmt: skip
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
        if self.plant is not None and isinstance(data, dict):
            # number past every id of the plant, so a new id is not another file's (PLAN 5.4e)
            mine = {c.get("id") for c in data.get("components") or [] if isinstance(c, dict)}
            others = [{"id": i} for i in self._plant_ids() if i not in mine]
            data = {**data, "components": [*(data.get("components") or []), *others]}
        return new_component(type_name, data, self.path.parent, position)

    def _plant_ids(self) -> list[str]:
        from openfactory.core.schema import load_scene

        assert self.plant is not None
        try:
            return list(load_scene(self.plant).plant.sources)  # type: ignore[union-attr]
        except (OSError, ValueError):
            return []

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

    def mount(
        self,
        base: str,
        ops: list[dict[str, Any]],
        comp_id: str,
        conveyor_id: str,
        rail: str,
        along: float,
    ) -> dict[str, Any]:
        """Where ``comp_id`` goes dropped on ``conveyor_id``'s ``rail``, ``along`` metres from its
        infeed end (PLAN 4.1g1): :func:`openfactory.core.mounts.place_on_rail` on the text with
        the operations applied. ``swing_deg`` is in the answer only when a diverter's must change
        sign. ValueError for a component not in the file, a type that does not mount, or a
        conveyor without rails."""
        from openfactory.core.loader import build
        from openfactory.core.mounts import place_on_rail
        from openfactory.core.schema import parse_scene
        from openfactory.core.sizing import tallest_part

        text = self._applied(base, ops)
        data = _data(text) or {}
        raw = next(
            (
                c
                for c in data.get("components") or []
                if isinstance(c, dict) and c.get("id") == comp_id
            ),
            None,
        )
        if raw is None:
            raise ValueError(f"no component {comp_id!r} in the scene")
        scene = parse_scene(text, self.path.parent, source=self.path)
        loaded = build(scene, base_dir=self.path.parent)
        conveyor = next((c.config for c in loaded.components if c.id == conveyor_id), None)
        if conveyor is None:
            raise ValueError(f"no conveyor {conveyor_id!r} in the scene")
        placed = place_on_rail(
            str(raw.get("type")), raw, conveyor, rail, along, tallest_part(scene)
        )
        out: dict[str, Any] = {"id": comp_id, **placed.document()}
        if placed.swing_sign is not None:
            swing = float(raw.get("swing_deg", 45.0))
            if swing * placed.swing_sign < 0:
                out["swing_deg"] = -swing
        return out

    def source(self) -> dict[str, Any]:
        """The text, its base, and ``data``: the file's values as written (YAML parsed, no
        defaults filled in), which the editor's forms show."""
        out = {
            "name": self.name,
            "text": self.text,
            "base": self.base,
            "data": _data(self.text),
        }
        if self.plant is not None:
            out["plant"] = self.plant.name
            out["area"] = self.area
        return out

    def _check_base(self, base: str) -> None:
        on_disk = text_hash(self.path.read_text(encoding="utf-8"))
        if base != on_disk:
            raise EditConflict(f"{self.name} changed on disk since this edit began; reload it")
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
        if self.plant is None:
            findings = lint_scene(self.path, text=text)
        else:  # the whole plant, with this text in the edited file's place (PLAN 5.4e)
            plant_text, overrides = self._plant_inputs(text)
            findings = lint_scene(self.plant, text=plant_text, overrides=overrides)
        load_error = None
        try:
            scene = self._document_of(text, self.path)
        except Exception as exc:  # noqa: BLE001 - the E100 finding already says why
            scene, load_error = None, str(exc)
        return {
            "load_error": load_error,
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
        return {"base": self.base, "findings": result["findings"], "saved": self.name}
