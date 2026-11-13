"""Composite components (PLAN 2.16): new equipment declared in a scene from existing parts.

Behavior spec
-------------
A scene's ``composites:`` block defines types; a component whose ``type`` names one is an
instance. :func:`expand` (called first by the scene validator) replaces every instance with its
parts, as ordinary components, so the loader, protocols, exporters, viewer and parity code see
only built-in types.

A definition has ``type`` (lowercase, not a built-in name), ``params`` (name -> default number),
``parts`` (components without ``io``), and ``io`` (the instance's ports):

- ``name: {to: part.port}``: a command (DO/AO) the instance passes to that part's port. With
  ``invert: true`` (DO only) the part acts on the opposite of the tag.
- ``name: {from: part.port}``: a DI or AI the instance passes through from that part's port.
- ``name: {from: "<expression>"}``: a DI computed from parts' DI/AI ports with the safe grammar of
  :mod:`openfactory.core.expr`, after the components each tick, like a 2.17 binding transform;
  the instance's binding may add ``invert`` and delays.

Expansion of an instance ``ID``:

- each part becomes ``ID_<PART>`` (part id upper-cased), at the instance's position plus the
  part's ``position`` turned by the instance's ``rotation_deg``, turned by both rotations, with the
  description ``"<instance description> <part description>"``;
- numeric part fields may be expressions over the parameters, ``"{length} - 0.2"``; the
  instance sets a parameter by giving it as a field (``length: 3.0``);
- the instance's ``io`` bindings move to the parts' ports, so tag names are the instance's own.

Additions of PLAN 2.5b:

- ``name: {command: DO}``: a PLC command the instance binds to a tag like any io, but that no
  part port receives: only the ``logic:`` block reads it, by its io name. Required on an instance.
- ``logic:`` maps a built-in part's DO port (``z1.run``) to a boolean expression over parts'
  DI/AI ports and the composite's ``command`` names, in the grammar of computed DIs. Each tick,
  before the parts step, the expression is evaluated with the parts' DI/AI values from the end
  of the previous tick (one tick of delay) and the part reads the result on that port instead
  of a tag; the port has no tag, so no PLC can see or write it. A port driven by logic cannot
  also be exposed in ``io`` (a load error naming both), and counts as wired for the
  unexposed-port check.
- ``connections:`` joins the composite's own conveyor parts by part id, expanded into the
  scene's connections (``z1 -> z2`` becomes ``ACC_Z1 -> ACC_Z2``).
- ``composites: [{library: name}]`` loads ``_library/<name>.yaml`` (``openfactory: "0.1"`` and a
  ``composite:`` definition whose type is ``name``) from the scene's folder or the nearest folder
  above it that has one, no higher than the project root (the nearest folder holding
  ``openfactory.toml`` or ``.git``; with none, the scene's folder only; :func:`library_folders`,
  PLAN 4.1c fix 5); the files used are listed for bundles (PLAN 2.18).

Load errors, each naming what is wrong: an unknown or duplicate type, a type that shadows a
built-in, a recursive definition (the cycle is named, ``a -> b -> a``), nesting more than one
level, a part ``io``, an ``io`` entry naming an unknown part or port or a port of the wrong
kind, two ``io`` entries driving one port, a part's required port not exposed in ``io``
(``unexposed``), an instance leaving a required ``io`` unbound, an unknown parameter, and any
expression error. A composite may be a part of another composite one level deep.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import TypeAdapter, ValidationError

from openfactory.core.expr import ExprError, number, parse
from openfactory.core.geometry import to_world

if TYPE_CHECKING:
    from openfactory.core.schema import CompositeDef, Scene

#: ``part.port`` with nothing else: a pass-through, not an expression.
SINGLE_REF = re.compile(r"^\s*([a-z][a-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\s*$")
_REF_IN_TEXT = re.compile(r"(?<![\w.])([a-z][a-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)")
_PLACEMENT = ("id", "type", "description", "position", "rotation_deg")
#: Stands in for a logic-driven port's binding while the part is validated, then is removed.
_LOGIC_PLACEHOLDER = "LOGIC_PLACEHOLDER_DO"
LIBRARY_DIR = "_library"


@dataclass(frozen=True)
class LogicDrive:
    """One part port driven by a composite's logic: block (PLAN 2.5b), after expansion."""

    part_id: str  # expanded part id, e.g. ACC_Z1
    port: str  # its DO port, e.g. run
    text: str  # the expression with expanded refs, e.g. "not (ACC_Z1_EYE.detect and ...)"
    local: str  # the expression as the composite wrote it
    commands: dict[str, str]  # command io name -> the instance's tag


@dataclass
class Expansion:
    """What :func:`expand` adds to a scene besides its flat component list."""

    #: Expanded component id -> the top-level instance it came from.
    composite_of: dict[str, str] = field(default_factory=dict)
    #: Top-level instance id -> its composite type.
    instance_type: dict[str, str] = field(default_factory=dict)
    #: Computed DIs (expression ``from:``): (after component id, io name, binding, expanded
    #: expression, the expression as the composite wrote it).
    computed: list[tuple[str, str, Any, str, str]] = field(default_factory=list)
    #: Command io (PLAN 2.5b): (owner part id, io name, the instance's binding, the instance's
    #: description).
    commands: list[tuple[str, str, Any, str]] = field(default_factory=list)
    #: Part ports driven by logic: blocks (PLAN 2.5b).
    logic: list[LogicDrive] = field(default_factory=list)
    #: Connections between composites' own parts, expanded ids (PLAN 2.5b).
    connections: list[tuple[str, str]] = field(default_factory=list)
    #: Library files the scene's composites came from (PLAN 2.5b).
    libraries: list[Path] = field(default_factory=list)


def ports_of(type_name: str, defs: dict[str, CompositeDef]) -> dict[str, str]:
    """Port name -> kind for a built-in type or a composite (its io: to -> command kind)."""
    from openfactory.core.schema import COMPONENT_MODELS, port_specs

    if type_name in COMPONENT_MODELS:
        model = COMPONENT_MODELS[type_name]
        return {n: p.kind for n, p in port_specs(model.model_fields["io"].annotation).items()}
    d = defs[type_name]
    kinds = {}
    for name, spec in d.io.items():
        if spec.command:
            kinds[name] = spec.command
            continue
        part, _, port = (spec.to or spec.from_ or "").partition(".")
        inner = next((p for p in d.parts if p["id"] == part), None)
        sub = ports_of(inner["type"], defs).get(port) if inner and SINGLE_REF.match(
            spec.to or spec.from_ or ""
        ) else None  # fmt: skip
        kinds[name] = sub or "DI"  # an expression is a DI
    return kinds


def required_ports(type_name: str, defs: dict[str, CompositeDef]) -> set[str]:
    """Ports an instance or a parent composite must bind for ``type_name``."""
    from openfactory.core.schema import COMPONENT_MODELS, port_specs

    if type_name in COMPONENT_MODELS:
        model = COMPONENT_MODELS[type_name]
        return {n for n, p in port_specs(model.model_fields["io"].annotation).items() if p.required}
    d = defs[type_name]
    out = set()
    for name, spec in d.io.items():
        if spec.command:
            out.add(name)  # the logic reads it, so an instance must bind it
            continue
        single = SINGLE_REF.match(spec.to or spec.from_ or "")
        if single:
            part, port = single.groups()
            inner = next(p for p in d.parts if p["id"] == part)
            if port in required_ports(inner["type"], defs):
                out.add(name)
    return out


def check_definitions(defs_list: list[CompositeDef]) -> dict[str, CompositeDef]:
    """Validate the ``composites:`` block as a whole; return type -> definition."""
    from openfactory.core.schema import COMPONENT_MODELS

    defs: dict[str, CompositeDef] = {}
    for d in defs_list:
        if d.type in COMPONENT_MODELS:
            raise ValueError(f"composite type {d.type!r} has a built-in component's name")
        if d.type in defs:
            raise ValueError(f"composite type {d.type!r} is defined twice")
        defs[d.type] = d
    known = set(COMPONENT_MODELS) | set(defs)
    for d in defs.values():
        for part in d.parts:
            if part["type"] not in known:
                raise ValueError(
                    f"composite {d.type}: part {part['id']} has unknown type {part['type']!r}"
                )
    _check_cycles(defs)
    for d in defs.values():
        _check_io(d, defs)
    return defs


def _check_cycles(defs: dict[str, CompositeDef]) -> None:
    def walk(type_name: str, path: list[str]) -> None:
        for part in defs[type_name].parts:
            sub = part["type"]
            if sub not in defs:
                continue
            if sub in path:
                cycle = " -> ".join([*path[path.index(sub) :], sub])
                raise ValueError(f"recursive composite: {cycle}")
            if len(path) >= 2:
                raise ValueError(
                    f"composite nesting deeper than one level: {' -> '.join([*path, sub])}"
                )
            walk(sub, [*path, sub])

    for type_name in defs:
        walk(type_name, [type_name])


def _check_io(d: CompositeDef, defs: dict[str, CompositeDef]) -> None:
    parts = {p["id"]: p for p in d.parts}
    driven: dict[str, str] = {}
    for name, spec in d.io.items():
        where = f"composite {d.type}: io.{name}"
        if spec.command:
            continue  # read by logic: only; no part port
        text = spec.to or spec.from_ or ""
        single = SINGLE_REF.match(text)
        if spec.to and not single:
            raise ValueError(f"{where}: `to` must name one part port, like belt.run")
        refs = [single.groups()] if single else []
        expr = None
        if spec.from_ and not single:
            try:
                expr = parse(text, refs=True)
            except ExprError as exc:
                raise ValueError(f"{where}: {exc}") from None
            if expr.names:
                raise ValueError(f"{where}: {expr.names[0]!r} is not a part.port reference")
            refs = [r.split(".") for r in expr.refs]
        for part_id, port in refs:
            if part_id not in parts:
                raise ValueError(f"{where}: no part {part_id!r} (parts: {', '.join(parts)})")
            kinds = ports_of(parts[part_id]["type"], defs)
            if port not in kinds:
                raise ValueError(
                    f"{where}: {parts[part_id]['type']} part {part_id} has no port {port!r} "
                    f"(ports: {', '.join(kinds)})"
                )
            want = ("DO", "AO") if spec.to else ("DI", "AI")
            if kinds[port] not in want:
                raise ValueError(
                    f"{where}: {part_id}.{port} is a {kinds[port]} port; "
                    f"`{'to' if spec.to else 'from'}` takes {' or '.join(want)} ports"
                )
            if spec.from_ and not single and parts[part_id]["type"] in defs:
                raise ValueError(f"{where}: an expression reads built-in parts' ports only")
        if expr is not None:
            kinds = {f"{p}.{port}": ports_of(parts[p]["type"], defs)[port] for p, port in refs}
            _probe(expr, kinds, where)
        if spec.invert and (
            not spec.to or ports_of(parts[refs[0][0]]["type"], defs)[refs[0][1]] != "DO"
        ):
            raise ValueError(f"{where}: `invert` is for a `to` on a DO port")
        if single:  # binds the part's port to the instance's tag
            target = f"{single[1]}.{single[2]}"
            if target in driven:
                raise ValueError(f"{where}: {target} is already bound by io.{driven[target]}")
            driven[target] = name
    for target in _check_logic(d, defs, driven):
        driven[target] = f"logic {target}"
    _check_connections(d)
    for part_id, part in parts.items():
        kinds = ports_of(part["type"], defs)
        for port in sorted(required_ports(part["type"], defs)):
            if f"{part_id}.{port}" not in driven:
                way = "to" if kinds[port] in ("DO", "AO") else "from"
                raise ValueError(
                    f"composite {d.type}: part {part_id}'s required port {port!r} is unexposed; "
                    f"add it to io, for example `{part_id}_{port}: {{{way}: {part_id}.{port}}}`"
                )


def _check_logic(
    d: CompositeDef, defs: dict[str, CompositeDef], driven: dict[str, str]
) -> list[str]:
    """Check a composite's logic: block; return the part ports it drives."""
    from openfactory.core.schema import COMPONENT_MODELS

    parts = {p["id"]: p for p in d.parts}
    commands = {n for n, spec in d.io.items() if spec.command}
    targets = []
    for target, text in d.logic.items():
        where = f"composite {d.type}: logic {target}"
        part_id, port = target.split(".")
        if part_id not in parts:
            raise ValueError(f"{where}: no part {part_id!r} (parts: {', '.join(parts)})")
        if parts[part_id]["type"] not in COMPONENT_MODELS:
            raise ValueError(f"{where}: logic drives built-in parts' ports only")
        kinds = ports_of(parts[part_id]["type"], defs)
        if port not in kinds:
            raise ValueError(
                f"{where}: {parts[part_id]['type']} part {part_id} has no port {port!r} "
                f"(ports: {', '.join(kinds)})"
            )
        if kinds[port] != "DO":
            raise ValueError(f"{where}: {target} is a {kinds[port]} port; logic drives DO ports")
        if target in driven:
            raise ValueError(
                f"{where}: io.{driven[target]} also drives {target}; a part command driven by "
                "logic cannot also be exposed in io"
            )
        try:
            expr = parse(text, refs=True)
        except ExprError as exc:
            raise ValueError(f"{where}: {exc}") from None
        unknown = [n for n in expr.names if n not in commands]
        if unknown:
            known = ", ".join(sorted(commands)) or "none"
            raise ValueError(
                f"{where}: {unknown[0]!r} is not a command io of {d.type} (commands: {known}); "
                "use part.port for a part's DI/AI"
            )
        kinds_of_refs: dict[str, str] = dict.fromkeys(expr.names, "DI")
        for ref in expr.refs:
            ref_part, ref_port = ref.split(".")
            if ref_part not in parts or parts[ref_part]["type"] not in COMPONENT_MODELS:
                raise ValueError(f"{where}: {ref!r} is not a port of a built-in part")
            ref_kinds = ports_of(parts[ref_part]["type"], defs)
            if ref_kinds.get(ref_port) not in ("DI", "AI"):
                raise ValueError(f"{where}: {ref!r} is not a DI or AI port")
            kinds_of_refs[ref] = ref_kinds[ref_port]
        _probe(expr, kinds_of_refs, where)
        targets.append(target)
    return targets


def _check_connections(d: CompositeDef) -> None:
    from openfactory.core.schema import CARRIER_TYPES

    parts = {p["id"]: p for p in d.parts}
    for conn in d.connections:
        for end in (conn.from_, conn.to):
            if end not in parts:
                raise ValueError(
                    f"composite {d.type}: connection names no part {end!r} "
                    f"(parts: {', '.join(parts)})"
                )
            if parts[end]["type"] not in CARRIER_TYPES:
                raise ValueError(
                    f"composite {d.type}: connection end {end} is a {parts[end]['type']}, "
                    "not a built-in conveyor"
                )


#: A folder holding one of these is a project root (PLAN 4.1c fix 5, ruled 2026-11-12).
PROJECT_MARKERS = ("openfactory.toml", ".git")


def project_root(scene_dir: Path) -> Path | None:
    """The nearest folder at or above ``scene_dir`` that holds ``openfactory.toml`` or ``.git``,
    or None. Only those two names are checked in each folder; nothing is listed."""
    folder = Path(scene_dir).resolve()
    for candidate in (folder, *folder.parents):
        if any((candidate / marker).exists() for marker in PROJECT_MARKERS):
            return candidate
    return None


def library_folders(scene_dir: Path) -> list[Path]:
    """The folders whose ``_library/`` a scene in ``scene_dir`` may use, nearest first: from
    ``scene_dir`` up to its project root (:func:`project_root`) and no further, or ``scene_dir``
    alone when there is no project root (PLAN 4.1c fix 5). The loader (:func:`find_library`) and
    the editor's palette (``templates._library_files``) both search exactly these."""
    folder = Path(scene_dir).resolve()
    root = project_root(folder)
    if root is None:
        return [folder]
    chain = [folder, *folder.parents]
    return chain[: chain.index(root) + 1]


def find_library(name: str, scene_dir: Path) -> Path | None:
    """``_library/<name>.yaml`` in the nearest of :func:`library_folders`, or None."""
    for candidate in library_folders(scene_dir):
        path = candidate / LIBRARY_DIR / f"{name}.yaml"
        if path.is_file():
            return path
    return None


def _library(name: str, scene_dir: Path | None) -> tuple[CompositeDef, Path]:
    from openfactory.core.schema import SCHEMA_VERSION, CompositeDef, _SceneYamlLoader

    if scene_dir is None:
        raise ValueError(
            f"library composite {name!r}: the scene was not loaded from a file, so there is no "
            f"folder to look for {LIBRARY_DIR}/{name}.yaml in"
        )
    path = find_library(name, scene_dir)
    if path is None:
        raise ValueError(
            f"library composite {name!r}: no {LIBRARY_DIR}/{name}.yaml in {scene_dir} or a folder "
            "above it within its project (the nearest folder holding openfactory.toml or .git)"
        )
    data = yaml.load(path.read_text(encoding="utf-8"), Loader=_SceneYamlLoader)  # noqa: S506
    if not isinstance(data, dict) or set(data) != {"openfactory", "composite"}:
        raise ValueError(f"{path}: a library file holds `openfactory` and `composite` only")
    if str(data["openfactory"]) != SCHEMA_VERSION:
        raise ValueError(
            f"{path}: openfactory {data['openfactory']!r}, expected {SCHEMA_VERSION!r}"
        )
    try:
        d = CompositeDef.model_validate(data["composite"])
    except ValidationError as exc:
        raise ValueError(f"{path}: {exc}") from None
    if d.type != name:
        raise ValueError(f"{path}: defines composite type {d.type!r}, not {name!r}")
    return d, path


#: AI readings to evaluate a computed DI with at load; two, so a denominator that is zero for one
#: reading does not reject a sound expression.
_PROBES = (0.5371, 1.9213)


def _probe(expr: Any, kinds: dict[str, str], where: str) -> None:
    """Evaluate a computed DI once with typed readings (DI true, AI a number): a type error or a
    result that is not true/false is a load error, not a DI that silently reads false."""
    for reading in _PROBES:
        env = {ref: (True if kind == "DI" else reading) for ref, kind in kinds.items()}
        try:
            value = expr.evaluate(env)
        except ExprError as exc:
            if "division by zero" in str(exc):
                continue
            raise ValueError(f"{where}: {exc}") from None
        if not isinstance(value, bool):
            raise ValueError(
                f"{where}: {expr.text!r} gives a number; a DI needs true/false "
                "(compare it, e.g. `dist.distance < 0.3`)"
            )
        return
    raise ValueError(f"{where}: {expr.text!r} divides by zero for every reading")


def _resolve(value: Any, params: dict[str, float], where: str) -> Any:
    if isinstance(value, str) and "{" in value:
        try:
            return number(value, params)
        except ExprError as exc:
            raise ValueError(f"{where}: {exc}") from None
    if isinstance(value, list):
        return [_resolve(v, params, where) for v in value]
    return value


def _invert(binding: Any) -> Any:
    from openfactory.core.schema import DoTagSpec

    if isinstance(binding, DoTagSpec):
        return binding.model_copy(update={"invert": not binding.invert})
    if isinstance(binding, dict):  # the instance's own mapping, {tag, initial, invert, ...}
        return {**binding, "invert": not binding.get("invert", False)}
    return {"tag": binding, "invert": True}


def expand(scene: Scene, scene_dir: Path | None = None) -> Expansion:
    """Replace every composite instance in ``scene.components`` by its parts (in place).
    ``scene_dir`` is where library composites are looked up (PLAN 2.5b)."""
    from openfactory.core.schema import CompositeInstance, Connection, LibraryComposite

    out = Expansion()
    if not scene.composites and not any(isinstance(c, CompositeInstance) for c in scene.components):
        return out
    resolved = []
    for entry in scene.composites:
        if isinstance(entry, LibraryComposite):
            d, path = _library(entry.library, scene_dir)
            resolved.append(d)
            out.libraries.append(path)
        else:
            resolved.append(entry)
    defs = check_definitions(resolved)
    flat: list[Any] = []
    for comp in scene.components:
        if not isinstance(comp, CompositeInstance):
            flat.append(comp)
            continue
        if comp.type not in defs:
            from openfactory.core.schema import COMPONENT_MODELS

            raise ValueError(
                f"{comp.id}: unknown component type {comp.type!r} (built-in: "
                f"{', '.join(sorted(COMPONENT_MODELS))}; composites in this scene: "
                f"{', '.join(sorted(defs)) or 'none'})"
            )
        parts = _instance(comp.id, comp.type, comp.description, comp.position, comp.rotation_deg,
                          comp.params(), dict(comp.io), defs, out)  # fmt: skip
        for part in parts:
            out.composite_of[part.id] = comp.id
        out.instance_type[comp.id] = comp.type
        flat.extend(parts)
    scene.components = flat
    if out.connections:
        scene.connections = [
            *scene.connections,
            *(Connection.model_validate({"from": a, "to": b}) for a, b in out.connections),
        ]
    return out


def _instance(
    inst_id: str,
    type_name: str,
    description: str,
    position: tuple[float, float, float],
    rotation_deg: float,
    given: dict[str, Any],
    io: dict[str, Any],
    defs: dict[str, CompositeDef],
    out: Expansion,
) -> list[Any]:
    from openfactory.core.schema import Component, DiTag

    d = defs[type_name]
    where = f"{inst_id} ({type_name})"
    unknown = sorted(set(given) - set(d.params))
    if unknown:
        known = ", ".join(sorted(d.params)) or "none"
        raise ValueError(f"{where}: unknown parameter {unknown[0]!r} (parameters: {known})")
    for name, value in given.items():
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError(f"{where}: parameter {name} must be a number, not {value!r}")
    params = {**d.params, **{k: float(v) for k, v in given.items()}}
    unknown_io = sorted(set(io) - set(d.io))
    if unknown_io:
        raise ValueError(f"{where}: io.{unknown_io[0]} is not a port of {type_name} "
                         f"(ports: {', '.join(d.io)})")  # fmt: skip
    for name in sorted(required_ports(type_name, defs)):
        if io.get(name) is None:
            target = d.io[name].to or d.io[name].from_
            raise ValueError(f"{where}: io.{name} is required (it binds {target}, a required port)")
    to_part: dict[str, dict[str, Any]] = {}
    logic_ports: dict[str, list[str]] = {}
    for target in d.logic:
        part_id, port = target.split(".")
        logic_ports.setdefault(part_id, []).append(port)
    for name, spec in d.io.items():
        binding = io.get(name)
        if spec.command:
            continue
        single = SINGLE_REF.match(spec.to or spec.from_ or "")
        if binding is None or not single:
            continue
        part_id, port = single.groups()
        to_part.setdefault(part_id, {})[port] = _invert(binding) if spec.invert else binding
    built: list[Any] = []
    adapter = TypeAdapter(Component)
    for part in d.parts:
        pid = f"{inst_id}_{part['id'].upper()}"
        pwhere = f"{where}, part {part['id']}"
        rel = _resolve(part.get("position", [0.0, 0.0, 0.0]), params, pwhere)
        turn = _resolve(part.get("rotation_deg", 0.0), params, pwhere)
        fields = {
            k: _resolve(v, params, f"{pwhere}.{k}") for k, v in part.items() if k not in _PLACEMENT
        }
        try:
            world = to_world(position, rotation_deg, tuple(rel))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            raise ValueError(f"{pwhere}: position must be [x, y, z]") from None
        placed = {
            "id": pid,
            "description": f"{description} {part.get('description', part['id'])}",
            "position": list(world),
            "rotation_deg": rotation_deg + float(turn),
        }
        if part["type"] in defs:
            built += _instance(pid, part["type"], placed["description"], tuple(placed["position"]),
                               placed["rotation_deg"], fields, to_part.get(part["id"], {}), defs,
                               out)  # fmt: skip
            continue
        part_io = dict(to_part.get(part["id"], {}))
        driven = logic_ports.get(part["id"], [])
        for port in driven:  # satisfies a required port while validating; removed below
            part_io[port] = _LOGIC_PLACEHOLDER
        data = {**placed, "type": part["type"], **fields, "io": part_io}
        try:
            model = adapter.validate_python(data)
        except ValidationError as exc:
            raise ValueError(f"{pwhere}: {exc}") from None
        if driven:
            from openfactory.core.schema import port_specs

            specs = port_specs(type(model.io))
            for port in driven:  # the port has no tag: the logic stage drives it
                object.__setattr__(model.io, specs[port].attr or port, None)
        built.append(model)
    commands = {name: io[name] for name, spec in d.io.items() if spec.command}
    if commands and built:
        for name, binding in commands.items():
            out.commands.append((built[0].id, name, binding, description))
    from openfactory.core.schema import binding_tag

    command_tags = {name: binding_tag(binding) for name, binding in commands.items()}
    for target, text in d.logic.items():
        part_id, port = target.split(".")
        expanded = _REF_IN_TEXT.sub(lambda m: f"{inst_id}_{m[1].upper()}.{m[2]}", text)
        out.logic.append(
            LogicDrive(f"{inst_id}_{part_id.upper()}", port, expanded, text, command_tags)
        )
    for conn in d.connections:
        out.connections.append((f"{inst_id}_{conn.from_.upper()}", f"{inst_id}_{conn.to.upper()}"))
    for name, spec in d.io.items():
        binding = io.get(name)
        if binding is None or not spec.from_ or SINGLE_REF.match(spec.from_):
            continue
        try:
            binding = TypeAdapter(DiTag).validate_python(binding)
        except ValidationError as exc:
            raise ValueError(f"{where}: io.{name} is a computed DI: {exc}") from None
        text = _REF_IN_TEXT.sub(lambda m: f"{inst_id}_{m[1].upper()}.{m[2]}", spec.from_)
        out.computed.append((built[-1].id, name, binding, text, spec.from_))
    return built
