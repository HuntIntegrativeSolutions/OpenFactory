"""Per-scene assistant context, ``<scene>.CONTEXT.md`` (PLAN 2.19).

Behavior spec
-------------
:func:`context_md` turns a scene into one Markdown file an AI assistant (or a person) can read
instead of reverse-engineering the YAML and the lockfiles:

- the scene's name, description, schema version, PLC protocol, analog encoding and seed;
- the commands to validate, lint, test, run, export and bundle it, written to be run from the
  folder that holds the scene, with the stand-in script found the way ``bundle`` finds it
  (``<scene>.py`` beside the scene, else ``examples/pyplc/<scene>.py`` above it);
- every tag, in Modbus register-map order: kind, component and port, description, transform
  (PLAN 2.17), units and range, and its Modbus address in both analog encodings (ADR-0004), read
  from each lockfile when it exists and otherwise computed as ``run`` would first write it; then
  any address a lockfile reserves for a removed tag;
- every component after composite expansion: type, description, position, every parameter that
  is set (defaults included), each port with its kind and tag (or unbound), the composite it is
  part of, and the fault kinds it supports (PLAN 2.6), with one line on what each kind does;
- the ``checks:`` block, if any.

The text depends only on the scene, its lockfiles and where its script is, never on the time,
the machine or ``openfactory.toml``, so regenerating it gives the same bytes and it can be
committed with the scene. ``openfactory run`` and ``export`` rewrite it when it changed
(:func:`write_context`); a ``.ofz`` bundle input is unpacked to a temporary folder, so nothing is
written for it (PLAN 2.18).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import get_args

from openfactory.core.component import REGISTRY
from openfactory.core.loader import build_iotable
from openfactory.core.schema import Scene, port_specs
from openfactory.protocols.modbus import Encoding, lockfile_path, scene_register_map

CONTEXT_SUFFIX = ".CONTEXT.md"

#: One line per fault kind (``openfactory.core.component``), for the faults legend.
FAULT_HELP = {
    "stuck_on": "a DI reads true whatever the device senses (`port=NAME` for one port)",
    "stuck_off": "a DI reads false whatever the device senses (`port=NAME` for one port)",
    "jammed": "the belt or actuator stops where it is; its feedback stays honest",
    "slow": "the belt or actuator moves at `factor` of its speed (default 0.5)",
    "misaligned": "the sensor sees `offset_m` late (positive) or early",
    "noisy": "random dropouts, `rate` per second (0.5), each `duration_s` long (0.2)",
    "drift": "an AI reads `rate` units per second more since the fault (default 0.01)",
    "signal_fail": "a transmitter reads its NE 43 failure level, 3.6 mA, or 21 mA with `high=1`",
}
#: Kinds every legend lists (PLAN 2.6); a later kind is listed only in a scene with a component
#: that supports it, so adding one leaves every other scene's CONTEXT.md as it was.
BASE_FAULTS = ("stuck_on", "stuck_off", "jammed", "slow", "misaligned", "noisy", "drift")


def _fault_kinds(scene: Scene) -> set[str]:
    from openfactory.core.component import REGISTRY

    used = {
        k
        for c in scene.components
        if c.type in REGISTRY.types()
        for k in REGISTRY.resolve(c.type).FAULTS
    }
    return set(BASE_FAULTS) | used


TABLE_NAME = {
    "DI": "discrete input",
    "DO": "coil",
    "AI": "input register",
    "AO": "holding register",
}
_SKIP = {"id", "type", "description", "position", "io"}


def context_path(scene_path: str | Path) -> Path:
    path = Path(scene_path)
    return path.with_name(f"{path.stem}{CONTEXT_SUFFIX}")


def _value(value: object) -> str:
    return json.dumps(value, separators=(", ", ": "), ensure_ascii=False)


def _cell(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _script(scene_path: Path) -> str | None:
    from openfactory.bundle import find_script  # the same search `bundle` uses

    found = find_script(scene_path)
    if found is None:
        return None
    return Path(os.path.relpath(found.resolve(), scene_path.parent.resolve())).as_posix()


def _commands(scene: Scene, scene_path: Path) -> list[str]:
    name = scene_path.name
    stem = scene_path.stem
    script = _script(scene_path)
    modbus = scene.plc.protocol == "modbus_tcp"
    lines = [
        "## Commands",
        "",
        "Run these from the folder that holds this file. In the OpenFactory source repository,",
        "put `uv run` in front of each.",
        "",
        "```bash",
        f"openfactory validate {name}",
        f"openfactory lint {name}" + (f" --script {script}" if script else ""),
    ]
    if scene.checks is not None:
        if script:
            lines.append(f"openfactory test {name} --script {script}   # grade the stand-in script")
        lines.append(f"openfactory test {name} --plc   # grade a real PLC, in real time")
    lines.append(f"openfactory run {name}   # protocol server and viewer; a PLC drives it")
    if script:
        lines.append(f"openfactory run {name} --script {script}   # the stand-in script drives it")
    lines.append(f"openfactory run {name} --as-fast-as-possible --duration 60   # headless")
    if modbus:
        lines += [
            f"openfactory export {name} -o {stem}-io.csv --analog-encoding float32",
            f"openfactory export {name} -o {stem}-io-int16.csv --analog-encoding int16_scaled",
        ]
    else:
        lines.append(f"openfactory export {name} -o {stem}-io.csv")
    lines += [
        f"openfactory export {name} --format l5x -o {stem}-tags.L5X",
        f"openfactory export {name} --format ignition -o {stem}-ignition-tags.json",
        f"openfactory bundle {name}   # one .ofz with meshes, lockfiles and script",
        "```",
        "",
    ]
    if not script:
        lines += ["No stand-in PLC script was found beside the scene or in `examples/pyplc/`.", ""]
    if scene.checks is None:
        lines += ["The scene has no `checks:` block, so `openfactory test` cannot grade it.", ""]
    if modbus:
        port = scene.plc.port or 5020
        lines += [
            f"Modbus TCP listens on 127.0.0.1 port {port} and the viewer on http://127.0.0.1:8000/",
            "unless `openfactory.toml` or `--bind`, `--port` and `--web-port` say otherwise.",
            "",
        ]
    return lines


def _tags(scene: Scene, scene_path: Path) -> list[str]:
    table = build_iotable(scene)
    bindings = {b.name: b for b in scene.tag_bindings()}
    encodings: tuple[Encoding, ...] = get_args(Encoding)
    maps = {e: scene_register_map(scene, e, scene_path=scene_path, write=False) for e in encodings}
    labels = {e: {a.tag: a.label for a in m.assignments} for e, m in maps.items()}
    order = [a.tag for a in maps[scene.plc.analog_encoding].assignments]
    lines = ["## Tags", ""]
    for e in encodings:
        lock = lockfile_path(scene_path, e)
        source = f"`{lock.name}`" if lock.is_file() else "no lockfile yet; as `run` would write it"
        lines.append(f"- {e} addresses: {source}")
    lines += [
        "- Modbus tables: DI discrete input, CO coil, IR input register, HR holding register;",
        "  zero-based addresses. Analogs: float32 takes two registers, high word first;",
        "  int16_scaled takes one, 0 to 32767 across the range.",
        "- DO and AO are written by the PLC; DI and AI are written by the simulation.",
        "",
        "| Tag | Kind | Component.port | Description | Transform | Units, range | "
        + " | ".join(encodings)
        + " |",
        "|---|---|---|---|---|---|" + "---|" * len(encodings),
    ]
    for name in order:
        tag = table.tag(name)
        binding = bindings.get(name)
        where = f"{binding.component_id}.{binding.port.name}" if binding else "(simulator)"
        transform = binding.transform.describe(binding.port.units) if binding else ""
        span = ""
        if tag.kind in ("AI", "AO") and tag.range:
            span = f"{tag.units or ''} {tag.range[0]:g} to {tag.range[1]:g}".strip()
        cells = [f"`{name}`", tag.kind, where, _cell(tag.description), _cell(transform), span]
        cells += [labels[e].get(name, "") for e in encodings]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    reserved = [
        (e, name, address) for e, m in maps.items() for name, address in sorted(m.retired.items())
    ]
    if reserved:
        lines += [
            "Reserved addresses (a removed tag's line in a lockfile, kept so no new tag reuses",
            "the address; delete the line to free it):",
            "",
        ]
        lines += [
            f"- {e}: `{name}` {TABLE_NAME[name.rsplit('_', 1)[-1]]} {address}"
            for e, name, address in reserved
        ]
        lines.append("")
    return lines


def _components(scene: Scene) -> list[str]:
    bound: dict[str, dict[str, str]] = {}
    for b in scene.tag_bindings():
        bound.setdefault(b.component_id, {})[b.port.name] = b.name
    driven = {(d.part_id, d.port): d.local for d in scene.logic_drives()}
    lines = ["## Components", ""]
    for comp in scene.components:
        lines.append(f"### {comp.id} ({comp.type})")
        lines.append("")
        lines.append(f"{comp.description}. Position {_value(list(comp.position))}.")
        instance = scene.composite_of(comp.id)
        if instance:
            lines.append(
                f"Part of `{instance}`, a `{scene.instance_type(instance)}` composite (PLAN 2.16)."
            )
        lines.append("")
        params = {
            k: v
            for k, v in comp.model_dump(mode="json", by_alias=True).items()
            if k not in _SKIP and v is not None
        }
        if params:
            shown = ", ".join(f"`{k}` {_value(v)}" for k, v in params.items())
            lines.append(f"Parameters: {shown}")
            lines.append("")
        specs = port_specs(type(comp).model_fields["io"].annotation)
        lines += ["| Port | Kind | Tag | What it is |", "|---|---|---|---|"]
        for port, spec in specs.items():
            tag = bound.get(comp.id, {}).get(port)
            shown = f"`{tag}`" if tag else ("unbound (required)" if spec.required else "unbound")
            if (comp.id, port) in driven:
                shown = f"driven by the composite's logic: `{_cell(driven[comp.id, port])}`"
            lines.append(f"| {port} | {spec.kind} | {shown} | {_cell(spec.description)} |")
        for port, tag in bound.get(comp.id, {}).items():
            if port not in specs:  # a composite's computed DI or command, after its component
                kind = next(b.kind for b in scene.tag_bindings() if b.name == tag)
                what = (
                    "command read by the composite's logic"
                    if kind == "DO"
                    else "computed from the parts' ports"
                )
                lines.append(f"| {port} | {kind} | `{tag}` | {what} |")
        faults = REGISTRY.resolve(comp.type).FAULTS
        lines += ["", "Faults: " + (", ".join(f"`{k}`" for k in faults) if faults else "none"), ""]
    return lines


def context_md(scene: Scene, scene_path: str | Path) -> str:
    """The CONTEXT.md text for ``scene`` loaded from ``scene_path``."""
    scene_path = Path(scene_path)
    plc = scene.plc
    lines = [
        f"# {scene.scene.name}: scene context",
        "",
        f"Generated by OpenFactory from `{scene_path.name}`; do not edit. `openfactory run` and",
        "`openfactory export` rewrite it when the scene or a lockfile changes (PLAN 2.19).",
        "OpenFactory is a training and demo tool, not a safety-rated simulation.",
        "",
        "## Scene",
        "",
        f"- {scene.scene.description}",
        f'- Schema `openfactory: "{scene.openfactory}"`, seed {scene.scene.seed}.',
        f"- PLC: protocol `{plc.protocol}`, role `{plc.role}`, analog encoding "
        f"`{plc.analog_encoding}`.",
        f"- {len(scene.components)} components, {len(scene.tag_bindings())} bound tags, and the "
        "simulator's `SIM_HEARTBEAT_DI`.",
        "",
    ]
    lines += _commands(scene, scene_path)
    lines += _tags(scene, scene_path)
    lines += _components(scene)
    lines += ["## Fault kinds", ""]
    lines += [f"- `{k}`: {v}" for k, v in FAULT_HELP.items() if k in _fault_kinds(scene)]
    lines += [
        "- Inject one with `--fault ID=KIND[:key=value,...] --fault-at SECONDS` on `run` or",
        "  `test`, or from the viewer's context menu. `--fault comms=drop` and",
        "  `--fault comms=latency:ms=N` stall or delay the protocol server instead.",
        "",
        "## Checks",
        "",
    ]
    if scene.checks is None:
        lines += ["None.", ""]
    else:
        checks = scene.checks.model_dump(mode="json", exclude_none=True)
        # a check a scene does not use is an empty mapping or list: leave it out
        lines += [f"- `{k}`: {_value(v)}" for k, v in checks.items() if v not in ({}, [])]
        lines.append("")
    return "\n".join(lines)


def write_context(scene: Scene, scene_path: str | Path) -> Path | None:
    """Write ``<scene>.CONTEXT.md`` beside the scene if it changed; returns the path written."""
    path = context_path(scene_path)
    text = context_md(scene, scene_path)
    if path.is_file() and path.read_text(encoding="utf-8") == text:
        return None
    path.write_text(text, encoding="utf-8", newline="\n")
    return path
