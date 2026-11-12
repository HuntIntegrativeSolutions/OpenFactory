"""New components for the scene editor's palette (PLAN 4.1c).

Behavior spec
-------------
:func:`palette` lists what the editor can place in a scene: every built-in component type, the
composite types the scene's own ``composites:`` block declares, and every library composite in a
``_library/`` folder beside the scene or in a folder above it (the loader's own search, PLAN
2.5b). Each entry names the fields a new one still needs filled in by hand (``needs``: a pipe's
``from`` and ``to``, a heater's ``tank`` when the scene has no tank to propose).

:func:`new_component` builds one new component as a mapping the ``add`` edit operation writes
(core/sceneedit.py), ruled 2026-11-10 (decision 5):

- the id is the type's prefix and the next free number (``PE_03`` after ``PE_01`` and ``PE_02``);
- the description is a placeholder, ``New photo eye``, so the plant redraws with it at once; the
  editor will not save (or download) until the user has written each new component's own
  description (decision 5: a description required before Save);
- the required parameters take starter values (a 2 m belt, a 0.6 m beam), and a reference to
  another component is proposed from the scene when there is an obvious one (a heater's tank,
  a conveyor with no ``run`` binding for a VFD to drive, the tank level a transmitter reads),
  else left empty; ``note`` says what else a type needs (a junction, a pipe connected to it);
- every required I/O port gets a tag proposed from the id and the port (``PE_03_DI``,
  ``CONV_03_RUN_DO``) for the user to accept or rename; optional ports stay unbound.

A library composite placed in a scene that does not use it yet also needs ``{library: name}`` in
its ``composites:`` block; :func:`new_component` says so (``library``) and the editor sends the
``use_library`` operation before the ``add``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

#: The id prefix of each built-in type: the shipped scenes' own where they have one.
PREFIX: dict[str, str] = {
    "belt_conveyor": "CONV",
    "box_emitter": "EMT",
    "color_sensor": "CS",
    "counter_display": "CNT",
    "distance_sensor": "DS",
    "diverter_arm": "DIV",
    "drain": "DR",
    "heater": "HTR",
    "junction": "J",
    "lift": "LIFT",
    "part_remover": "REM",
    "photo_eye": "PE",
    "pick_and_place": "PP",
    "pilot_light": "PL",
    "pipe": "PIPE",
    "pump": "P",
    "pushbutton": "PB",
    "pusher": "CYL",
    "roller_conveyor": "RCONV",
    "selector_switch": "SS",
    "stopper": "STP",
    "supply": "SUP",
    "tank": "TK",
    "transmitter": "LT",
    "turntable": "TRN",
    "valve_control": "FV",
    "valve_onoff": "XV",
    "vfd_motor": "VFD",
}

#: Starter values for required parameters that have no default, and the height (y) a new one
#: is placed at (a photo eye above the belt, an emitter's drop height).
STARTERS: dict[str, dict[str, Any]] = {
    "belt_conveyor": {"length": 2.0, "width": 0.5, "speed_mps": 0.5},
    "roller_conveyor": {"length": 2.0, "width": 0.5, "speed_mps": 0.5},
    "turntable": {"length": 1.0, "width": 0.5, "speed_mps": 0.5},
    "lift": {"length": 1.0, "width": 0.5, "speed_mps": 0.5, "lift_height": 0.5},
    "box_emitter": {
        "interval_s": 3.0,
        "parts": [{"name": "box", "size": [0.3, 0.3, 0.3], "weight": 1.0}],
    },
    "color_sensor": {"teach": "red"},
    "diverter_arm": {"length": 0.6},
    "part_remover": {"size": [0.6, 0.6, 0.6]},
    "photo_eye": {"beam_length": 0.6},
    "pick_and_place": {"travel": 1.0, "stroke": 0.3},
    "pusher": {"stroke": 0.6, "extend_time_s": 0.5},
    "transmitter": {"range": [0.0, 2.0]},
}
HEIGHT: dict[str, float] = {"photo_eye": 0.1, "box_emitter": 0.3, "pusher": 0.175}

#: Fields that name another component: (type field, the types it may name, a suffix appended).
REFERENCES: dict[str, list[tuple[str, tuple[str, ...], str]]] = {
    "heater": [("tank", ("tank",), "")],
    "vfd_motor": [("drives", ("belt_conveyor", "roller_conveyor", "pump"), "")],
    "transmitter": [("measures", ("tank",), ".level")],
    "pipe": [("from", (), ""), ("to", (), "")],
    "pump": [("from", (), ""), ("to", (), "")],
    "valve_control": [("from", (), ""), ("to", (), "")],
    "valve_onoff": [("from", (), ""), ("to", (), "")],
}

#: What a new one of these types needs before the scene loads, beyond fields to fill in.
NOTES: dict[str, str] = {"junction": "connect a pipe to it before it loads"}

_KIND_SUFFIX = ("DI", "DO", "AI", "AO")


def _free(type_name: str, target: dict[str, Any]) -> bool:
    """Whether ``target`` can be proposed for a new ``type_name``'s reference: a VFD drives a
    conveyor or pump whose own ``run`` is unbound (the PLC commands the drive instead)."""
    if type_name == "vfd_motor":
        io = target.get("io")
        return not (isinstance(io, dict) and io.get("run"))
    return True


_ID_NUMBER = re.compile(r"^(?P<prefix>[A-Z][A-Z0-9_]*?)_(?P<n>\d+)$")


def _components(data: Any) -> list[dict[str, Any]]:
    comps = data.get("components") if isinstance(data, dict) else None
    return [c for c in comps or [] if isinstance(c, dict)]


def next_id(prefix: str, data: Any) -> str:
    """``PREFIX_NN``, one more than the highest number in use with that prefix (01 if none)."""
    used = 0
    for comp in _components(data):
        m = _ID_NUMBER.match(str(comp.get("id", "")))
        if m and m["prefix"] == prefix:
            used = max(used, int(m["n"]))
    return f"{prefix}_{used + 1:02d}"


def composite_prefix(type_name: str) -> str:
    """A composite type's id prefix: its first three letters (``accumulation_conveyor`` ACC)."""
    letters = re.sub(r"[^a-z0-9]", "", type_name)
    return (letters[:3] or "CMP").upper()


def propose_tag(comp_id: str, port: str, kind: str, single: bool) -> str:
    """``ID_KIND`` for a type with one port, else ``ID_PORT_KIND``; at most 40 characters."""
    tag = f"{comp_id}_{kind}" if single else f"{comp_id}_{port.upper()}_{kind}"
    if len(tag) > 40:
        tag = f"{tag[: 40 - len(kind) - 1].rstrip('_')}_{kind}"
    return tag


def _library_files(scene_dir: Path) -> dict[str, Path]:
    """Library composite name -> file: ``_library/*.yaml`` beside the scene or above it, the
    nearest one winning, as :func:`openfactory.core.composite.find_library` resolves a name."""
    from openfactory.core.composite import LIBRARY_DIR

    found: dict[str, Path] = {}
    folder = Path(scene_dir).resolve()
    for candidate in (folder, *folder.parents):
        lib = candidate / LIBRARY_DIR
        if lib.is_dir():
            for path in sorted(lib.glob("*.yaml")):
                found.setdefault(path.stem, path)
    return found


def _composite_defs(data: Any, scene_dir: Path) -> dict[str, tuple[Any, str | None]]:
    """Composite type -> (definition, library name or None for the scene's own)."""
    from openfactory.core.composite import _library
    from openfactory.core.schema import CompositeDef

    defs: dict[str, tuple[Any, str | None]] = {}
    entries = data.get("composites") if isinstance(data, dict) else None
    for entry in entries or []:
        if isinstance(entry, dict) and "parts" in entry:
            try:
                d = CompositeDef.model_validate(entry)
            except Exception:  # noqa: BLE001 - a broken definition is the lint's to report
                continue
            defs[d.type] = (d, None)
    for name in _library_files(scene_dir):
        if name in defs:
            continue
        try:
            d, _ = _library(name, scene_dir)
        except ValueError:
            continue  # a broken library file is the loader's to report when it is used
        defs[name] = (d, name)
    return defs


def _uses_library(data: Any, name: str) -> bool:
    entries = data.get("composites") if isinstance(data, dict) else None
    return any(isinstance(e, dict) and e.get("library") == name for e in entries or [])


def palette(data: Any, scene_dir: Path) -> list[dict[str, Any]]:
    """Everything the editor can place: built-in types, then the scene's and the libraries'
    composite types. ``needs`` lists fields a new one leaves for the user to fill in."""
    from openfactory.core.schema import COMPONENT_MODELS

    out: list[dict[str, Any]] = []
    for type_name, model in COMPONENT_MODELS.items():
        doc = (model.__doc__ or "").strip().splitlines()
        needs = [f for f, _, _ in REFERENCES.get(type_name, [])]
        out.append({
            "type": type_name,
            "kind": "primitive",
            "description": doc[0] if doc else type_name,
            "needs": needs,
            "note": NOTES.get(type_name, ""),
        })  # fmt: skip
    for type_name, (d, library) in sorted(_composite_defs(data, scene_dir).items()):
        out.append({
            "type": type_name,
            "kind": "library" if library else "composite",
            "description": (d.description or type_name).strip().split(". ")[0],
            "needs": [],
            "note": "",
            "library": library,
            "in_scene": library is None or _uses_library(data, library),
        })  # fmt: skip
    return out


def placeholder(type_name: str) -> str:
    """The description a new component starts with, until the user writes its own."""
    return "New " + type_name.replace("_", " ")


def _round(position: Any) -> list[float]:
    return [round(float(v), 3) for v in position]


def new_component(
    type_name: str, data: Any, scene_dir: Path, position: Any = (0.0, 0.0, 0.0)
) -> dict[str, Any]:
    """A new component of ``type_name`` for ``data`` (the scene as parsed YAML), as
    ``{"component": {...}, "library": name or None}``; ``library`` is set when the scene must
    first add ``{library: name}`` to its ``composites:`` block. ValueError for an unknown type."""
    from openfactory.core.composite import ports_of, required_ports
    from openfactory.core.schema import COMPONENT_MODELS, port_specs

    x, y, z = _round(position)
    if type_name in COMPONENT_MODELS:
        comp_id = next_id(PREFIX.get(type_name, type_name.upper()[:4]), data)
        y = round(y + HEIGHT.get(type_name, 0.0), 3)
        comp: dict[str, Any] = {
            "id": comp_id,
            "type": type_name,
            "description": placeholder(type_name),
            "position": [x, y, z],
        }
        comp.update(yaml.safe_load(yaml.safe_dump(STARTERS.get(type_name, {}))))
        for field, targets, suffix in REFERENCES.get(type_name, []):
            match = next(
                (c for c in _components(data) if c.get("type") in targets and _free(type_name, c)),
                None,
            )
            comp[field] = f"{match['id']}{suffix}" if match else ""
        specs = port_specs(COMPONENT_MODELS[type_name].model_fields["io"].annotation)
        required = [s for s in specs.values() if s.required]
        if required:
            single = len(specs) == 1
            comp["io"] = {s.name: propose_tag(comp_id, s.name, s.kind, single) for s in required}
        return {"component": comp, "library": None}
    defs = _composite_defs(data, scene_dir)
    if type_name not in defs:
        raise ValueError(f"unknown component type {type_name!r}")
    d, library = defs[type_name]
    plain = {name: entry[0] for name, entry in defs.items()}
    comp_id = next_id(composite_prefix(type_name), data)
    comp = {
        "id": comp_id,
        "type": type_name,
        "description": placeholder(type_name),
        "position": [x, y, z],
    }
    kinds = ports_of(type_name, plain)
    needed = required_ports(type_name, plain)
    io = {
        name: propose_tag(comp_id, name, kind, len(kinds) == 1)
        for name, kind in kinds.items()
        if name in needed and kind in _KIND_SUFFIX
    }
    if io:
        comp["io"] = io
    use = library if library and not _uses_library(data, library) else None
    return {"component": comp, "library": use}


def mesh_files(scene_dir: Path, limit: int = 200) -> list[str]:
    """``.glb`` files a scene can name in ``mesh.file`` (relative, no ``..``): in the scene's
    folder and up to two folders below it, sorted, at most ``limit``."""
    base = Path(scene_dir).resolve()
    found: list[str] = []
    for pattern in ("*.glb", "*/*.glb", "*/*/*.glb"):
        for path in sorted(base.glob(pattern)):
            if any(part.startswith(".") or part == "node_modules" for part in path.parts):
                continue
            found.append(path.relative_to(base).as_posix())
            if len(found) >= limit:
                return found
    return found
