"""Plants in several files (PLAN 5.4a; design note §4.2): ``include:`` at load.

Behavior spec
-------------
A **plant file** is a scene file with ``include: [{area: NAME, file: PATH}, ...]``. Each included
file is an **area**: an ordinary scene, loadable alone. :func:`merge` turns a plant file's data
into one scene's data, which :class:`~openfactory.core.schema.Scene` then validates as any scene:

- the components are each area's, in ``include`` order, then the plant file's own; the
  connections each area's, then the plant file's (a link across areas is written there);
- the plant file's ``scene``, ``plc`` and ``checks`` apply, and an area's own are kept for
  running it alone;
- ``composites`` are merged by type name: the same definition in two files is one type, two
  different definitions of one name are a load error naming both files;
- ids and tags are global across the plant and never prefixed: an id or tag two files share is a
  load error naming both files;
- a component's ``mesh.file`` is made relative to the plant's folder, so it still names the file
  beside its area.

**What may be included** (ruled 2026-11-17). ``file`` is relative to the including file; an
absolute path, a ``..`` that leaves the plant's folder, and a path that resolves (symlinks
followed) outside the plant's folder are refused. Every file is read as a scene is
(:func:`openfactory.core.schema.read_scene_text`): at most ``MAX_SCENE_BYTES``, the scene's safe
YAML loader. ``include`` is one level deep: an area that includes is refused, and an include
that leads back to a file already being loaded (the plant itself, by any spelling or symlink) is
a cycle, a load error naming the files. An area is validated alone first, so an error in it is
reported as ``<file>: line <n>: <where>: <what>``.

**One area alone** (``area=``, PLAN 5.4b): only that area's components and its own connections,
the plant's connections whose two ends are both in it (the others are dropped, and
:attr:`Plant.notes` says so once), the area's own ``checks``, and the include's ``area`` as the
default ``scene.area``.

Not a safety-rated simulation. OpenFactory is a training and demo tool.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from pydantic import ValidationError

#: Keys of a plant file's data that the merge builds from every file.
_MERGED = ("components", "connections", "composites")


class PlantError(ValueError):
    """A plant that cannot load: the message names the file (and its line when known)."""


@dataclass
class Area:
    name: str
    file: str  # as written in include:
    path: Path  # resolved
    data: dict[str, Any]
    ids: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    #: The name each point reaches the controller by: its ``plc_tag``, else its scene tag.
    plc_names: list[str] = field(default_factory=list)
    #: Whether any point of the file has a ``plc_tag``.
    mapped: bool = False


@dataclass
class Plant:
    """What a loaded plant knows of its files (``Scene.plant``)."""

    path: Path
    areas: list[Area]
    #: Component id (as written, before composite expansion) -> the file it is in, as written.
    sources: dict[str, str]
    #: The area run alone, if any.
    area: str | None = None
    #: Things the load wants said once (a dropped cross-area connection).
    notes: list[str] = field(default_factory=list)

    def area_names(self) -> list[str]:
        return [a.name for a in self.areas]

    def files(self) -> list[Path]:
        return [a.path for a in self.areas]


def _rel(path: Path, folder: Path) -> str:
    try:
        return path.relative_to(folder).as_posix()
    except ValueError:
        return str(path)


def line_of(text: str, loc: tuple[Any, ...]) -> int | None:
    """The line (1-based) of the node a validation error's ``loc`` points at, as near as the
    text allows (a union's tag in ``loc`` that names no key is skipped)."""
    from openfactory.core.schema import _SceneYamlLoader

    try:
        node = yaml.compose(text, Loader=_SceneYamlLoader)  # noqa: S506 - a SafeLoader subclass
    except yaml.YAMLError:
        return None
    if node is None:
        return None
    line = node.start_mark.line + 1
    for key in loc:
        if isinstance(node, yaml.MappingNode):
            match = next((v for k, v in node.value if getattr(k, "value", None) == str(key)), None)
            if match is None:
                continue
            node = match
        elif isinstance(node, yaml.SequenceNode) and isinstance(key, int):
            if key >= len(node.value):
                break
            node = node.value[key]
        else:
            continue
        line = node.start_mark.line + 1
    return line


def _errors(rel: str, text: str, exc: ValidationError) -> str:
    out = []
    for err in exc.errors()[:10]:
        loc = tuple(err["loc"])
        where = ".".join(str(p) for p in loc) or "the file"
        line = line_of(text, loc)
        out.append(f"{rel}: line {line}: {where}: {err['msg']}" if line else
                   f"{rel}: {where}: {err['msg']}")  # fmt: skip
    more = len(exc.errors()) - len(out)
    return "; ".join(out) + (f"; and {more} more" if more > 0 else "")


def include_path(including: Path, file: str, folder: Path) -> Path:
    """The included file's resolved path; PlantError for a path the plant may not include."""
    rel = _rel(including, folder)
    if not isinstance(file, str) or not file:
        raise PlantError(f"{rel}: include file must be a relative path")
    pure = PurePosixPath(file.replace("\\", "/"))
    if pure.is_absolute() or (len(file) > 1 and file[1] == ":") or file.startswith(("/", "\\")):
        raise PlantError(f"{rel}: include {file!r} is an absolute path; give it relative to "
                         f"{rel}")  # fmt: skip
    lexical = Path(os.path.normpath(including.parent / Path(*pure.parts)))
    if not lexical.is_relative_to(folder):
        raise PlantError(f"{rel}: include {file!r} leaves the plant's folder ({folder.name}/)")
    resolved = lexical.resolve()
    if not resolved.is_relative_to(folder.resolve()):
        raise PlantError(
            f"{rel}: include {file!r} resolves to {resolved}, outside the plant's folder "
            f"({folder.name}/), through a symlink"
        )
    return resolved


def _composite_key(entry: Any) -> str | None:
    if not isinstance(entry, dict):
        return None
    return entry.get("type") or entry.get("library")


def merge(plant_path: Path, data: dict[str, Any], area: str | None = None) -> tuple[dict, Plant]:
    """One scene's data from a plant file's (see the module docstring); PlantError naming the
    file when the plant cannot load."""
    from openfactory.core.schema import Scene, read_scene_text

    folder = Path(os.path.abspath(plant_path)).parent
    plant_path = Path(os.path.abspath(plant_path))
    me = plant_path.name
    entries = data.get("include")
    if not isinstance(entries, list) or not entries:
        raise PlantError(f"{me}: include must be a list of {{area, file}}")
    areas: list[Area] = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"area", "file"}:
            raise PlantError(f"{me}: each include is {{area: NAME, file: PATH}}, not {entry!r}")
        path = include_path(plant_path, entry["file"], folder)
        if path == plant_path.resolve():
            raise PlantError(
                f"{me}: include cycle: {me} includes {entry['file']!r}, which is {me} itself"
            )
        rel = _rel(path, folder.resolve())
        if any(a.path == path for a in areas):
            raise PlantError(f"{me}: {rel} is included twice")
        if any(a.name == entry["area"] for a in areas):
            raise PlantError(f"{me}: area {entry['area']!r} is named twice")
        if not path.is_file():
            raise PlantError(f"{me}: include {entry['file']!r}: no such file")
        try:
            text = read_scene_text(path)
        except ValueError as exc:
            raise PlantError(f"{rel}: {exc}") from None
        try:
            area_data = yaml.load(text, Loader=_loader())  # noqa: S506 - a SafeLoader subclass
        except yaml.YAMLError as exc:
            raise PlantError(f"{rel}: {exc}") from None
        if not isinstance(area_data, dict):
            raise PlantError(f"{rel}: not a scene (a YAML mapping at the top level)")
        if "include" in area_data:
            for inner in area_data.get("include") or []:
                file = inner.get("file") if isinstance(inner, dict) else None
                try:
                    back = include_path(path, file, folder) if file else None
                except PlantError:
                    back = None
                if back is not None and back in (plant_path.resolve(), path):
                    raise PlantError(f"{me}: include cycle: {me} includes {rel}, which includes "
                                     f"{_rel(back, folder.resolve())}")  # fmt: skip
            raise PlantError(f"{rel}: an area cannot include (include is one level deep); "
                             f"{me} includes it")  # fmt: skip
        try:
            scene = Scene.model_validate(area_data, context={"scene_dir": path.parent})
        except ValidationError as exc:
            raise PlantError(_errors(rel, text, exc)) from None
        bindings = scene.tag_bindings()
        areas.append(Area(entry["area"], entry["file"], path, area_data,
                          [c.id for c in scene.components], [b.name for b in bindings],
                          [b.plc_tag or b.name for b in bindings],
                          any(b.plc_tag for b in bindings)))  # fmt: skip

    if area is not None and area not in [a.name for a in areas]:
        raise PlantError(f"{me}: no area {area!r}; its areas are "
                         f"{', '.join(a.name for a in areas)}")  # fmt: skip

    own = [c for c in data.get("components") or [] if isinstance(c, dict)]
    own_ids: list[str] = []
    own_tags: list[str] = []
    own_plc: list[str] = []
    own_mapped = False
    if own:
        mini = {"openfactory": data.get("openfactory"), "scene": data.get("scene"),
                "composites": data.get("composites") or [], "components": own}  # fmt: skip
        try:
            mini_scene = Scene.model_validate(mini, context={"scene_dir": folder})
        except ValidationError:
            mini_scene = None  # the merged scene reports it, with the others' context
        if mini_scene is not None:
            own_ids = [c.id for c in mini_scene.components]
            own_bindings = mini_scene.tag_bindings()
            own_tags = [b.name for b in own_bindings]
            own_plc = [b.plc_tag or b.name for b in own_bindings]
            own_mapped = any(b.plc_tag for b in own_bindings)

    # ids and tags are global: a clash names both files
    def clashes(what: str, lists: list[tuple[str, list[str]]]) -> None:
        first: dict[str, str] = {}
        for rel, names in lists:
            for n in names:
                if n in first and first[n] != rel:
                    raise PlantError(f"{me}: {what} {n!r} is in both {first[n]} and {rel}; ids "
                                     "and tags are global across a plant")  # fmt: skip
                first.setdefault(n, rel)

    rel_of = {a.name: _rel(a.path, folder.resolve()) for a in areas}
    clashes("id", [(rel_of[a.name], a.ids) for a in areas] + [(me, own_ids)])
    clashes("tag", [(rel_of[a.name], a.tags) for a in areas] + [(me, own_tags)])
    # a plant name two files share (PLAN 5.3a's rule, as the merged scene applies it: once any
    # point has a plc_tag, a point without one counts by its scene tag; names compared by
    # plc_tag_key, as tag names are not case sensitive)
    plc_block = data.get("plc") if isinstance(data.get("plc"), dict) else {}
    heartbeat = plc_block.get("heartbeat_plc_tag")
    if heartbeat or own_mapped or any(a.mapped for a in areas):
        from openfactory.core.heartbeat import HEARTBEAT_TAG

        own_plc = [*own_plc, heartbeat if isinstance(heartbeat, str) else HEARTBEAT_TAG]
        _plc_clashes(me, [(rel_of[a.name], a.plc_names) for a in areas] + [(me, own_plc)])

    # composites, merged by type name
    composites: list[Any] = []
    defined: dict[str, tuple[str, Any]] = {}
    sources_of = [(rel_of[a.name], a.data.get("composites") or []) for a in areas]
    sources_of.append((me, data.get("composites") or []))
    for rel, entries_ in sources_of:
        for entry in entries_:
            key = _composite_key(entry)
            if key is None:
                composites.append(entry)
                continue
            if key in defined:
                was_rel, was = defined[key]
                if was != entry:
                    raise PlantError(
                        f"{me}: composite type {key!r} is defined differently in {was_rel} and "
                        f"{rel}; one type has one definition"
                    )
                continue
            defined[key] = (rel, entry)
            composites.append(entry)

    sources: dict[str, str] = {}
    components: list[Any] = []
    connections: list[Any] = []
    chosen = [a for a in areas if area is None or a.name == area]
    for a in chosen:
        sub = a.path.parent.relative_to(folder.resolve())
        for comp in a.data.get("components") or []:
            if isinstance(comp, dict):
                comp = _rebase_mesh(comp, sub)
                sources[str(comp.get("id"))] = rel_of[a.name]
            components.append(comp)
        connections += a.data.get("connections") or []
    notes: list[str] = []
    if area is None:
        for comp in own:
            sources[str(comp.get("id"))] = me
        components += data.get("components") or []
        connections += data.get("connections") or []
    else:
        inside = set(sources)
        for conn in data.get("connections") or []:
            ends = (conn.get("from"), conn.get("to")) if isinstance(conn, dict) else (None, None)
            if all(e in inside for e in ends):
                connections.append(conn)
            else:
                notes.append(f"--area {area}: the plant's connection {ends[0]} -> {ends[1]} has an "
                             "end outside the area and is left out")  # fmt: skip

    merged = {k: v for k, v in data.items() if k not in _MERGED}
    merged["components"] = components
    if connections:
        merged["connections"] = connections
    if area is None:
        if composites:
            merged["composites"] = composites
    else:
        one = next(a for a in areas if a.name == area)
        if one.data.get("composites"):
            merged["composites"] = one.data["composites"]
        merged.pop("checks", None)
        if one.data.get("checks") is not None:
            merged["checks"] = one.data["checks"]
        meta = dict(merged.get("scene") or {})
        meta.setdefault("area", area)
        merged["scene"] = meta
    return merged, Plant(plant_path, areas, sources, area, notes)


def _plc_clashes(me: str, lists: list[tuple[str, list[str]]]) -> None:
    from openfactory.core.plctag import plc_tag_key

    first: dict[str, tuple[str, str]] = {}  # key -> (file, name as written)
    for rel, names in lists:
        for name in names:
            key = plc_tag_key(name)
            if key in first and first[key][0] != rel:
                was_rel, was = first[key]
                case = "" if was == name else (
                    f" ({was!r} and {name!r} differ only in case, and tag names are not case "
                    "sensitive)"
                )  # fmt: skip
                raise PlantError(
                    f"{me}: plc_tag {name!r} is in both {was_rel} and {rel}{case}; each point "
                    "in the plant's controller needs its own name"
                )
            first.setdefault(key, (rel, name))


def _rebase_mesh(comp: dict[str, Any], sub: Path) -> dict[str, Any]:
    mesh = comp.get("mesh")
    if sub == Path(".") or not isinstance(mesh, dict) or not isinstance(mesh.get("file"), str):
        return comp
    return {
        **comp,
        "mesh": {**mesh, "file": (PurePosixPath(sub.as_posix()) / mesh["file"]).as_posix()},
    }


def _loader() -> Any:
    from openfactory.core.schema import _SceneYamlLoader

    return _SceneYamlLoader
