"""Scene lint (PLAN 2.20): ``openfactory lint SCENE... [--script FILE.py]``.

Behavior spec
-------------
:func:`lint_scene` returns :class:`Finding` objects, each an error or a warning with a stable code:

Errors (``openfactory lint`` exits 1):

- ``E100`` the scene does not load. That covers YAML, schema, and component build errors, and an
  unbound required port, which the schema reports as ``Field required`` with the port's path.
- ``E101`` a Modbus address lockfile conflicts with the scene (ADR-0004).
- ``E102`` a tank nozzle declared in ``nozzles:`` that no link uses (PLAN 3.2, ADR-0005 decision
  9; the default ``bottom`` and ``top`` may stay unused).
- ``E103`` a part of the flow network with no node of known head (no tank, supply or open drain):
  its heads are undefined and nothing can flow in it.

Warnings (exit 0 unless there are errors):

- ``W201`` a DO/AO tag that no PLC side can write: it is in none of the scene's lockfiles (so no
  Modbus PLC has an address for it) and no ``--script`` writes it.
- ``W202`` a DI/AI tag that nothing reads: in no lockfile and read by no ``--script``.
- ``W203`` conveyors that touch end to end with no connection (the loader's Milestone 1 check).
- ``W204`` a component outside the floor, the viewer's 40 m × 40 m plane centred on the origin
  (``|x|`` or ``|z|`` over 20 m).
- ``W205`` two conveyors whose footprints overlap at the same belt height (within 5 cm). Conveyors
  joined end to end by a connection are allowed to touch. A conveyor expanded from a composite
  (PLAN 2.16) is checked like any other and named with its instance and type, so two instances
  placed over each other, or a composite whose own parts overlap, are caught.
- ``W206`` a pusher plate whose top is below the centre of mass of a part the scene emits, resting
  on the belt the plate sweeps: the plate hits the part low and tips it. The same check covers a
  ``stopper``'s raised blade top and a ``diverter_arm``'s arm top (their ``height``, above the belt
  they stand on).
- ``W207`` a pusher stroke that leaves a pushed part straddling a seam. With no belt beyond, or one
  that starts right at the swept belt's far edge, the stroke must reach that far edge. When a gap
  separates the swept belt from the belt beyond (PLAN 2.5b), it must reach the beyond belt's near
  edge plus half the deepest emitted part's depth along the stroke: the stroke that puts the
  whole part on the belt beyond, not balanced on the seam.

W206 and W207 use :func:`openfactory.components.pusher.push_path`, the first belt along the plate's
centre line, and the plate height after the loader's default.

- ``W209`` a component ``mesh`` (PLAN 2.15) whose file has no license record: neither a sidecar
  ``<file>.license.txt`` beside it nor an entry in a ``THIRD_PARTY.md`` in the mesh's folder or
  any folder above it that names the mesh's path relative to that file. A mesh in a scene is a
  third-party asset until someone says whose it is and under what terms.

- ``W211`` a tank whose level would move faster than 10 ticks can follow (PLAN 3.2, ADR-0005
  decision 9): its time constant, area over the slope of its flow against level through the
  resistances (pipes, valves) that join it directly to a node of known head, at its initial
  level, is under 10 ticks of 50 Hz. The explicit level update stays stable, but the level jumps
  tick to tick; make the tank larger or its connection tighter.

- ``W208`` a composite type (PLAN 2.16) that exposes no I/O: its ``io:`` block is empty, so no
  PLC can command or read any instance of it.

Scripts are scanned as text, not run: ``io["TAG"] = ...`` counts as a write and any other
``io["TAG"]`` as a read (the ``openfactory.pyplc`` convention).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, get_args

from pydantic import ValidationError

from openfactory.components.pusher import beyond_path, push_depth, push_path
from openfactory.core.heartbeat import HEARTBEAT_TAG
from openfactory.core.loader import build
from openfactory.core.loader import lint as loader_lint
from openfactory.core.schema import CARRIER_TYPES, PLC_WRITES, load_scene
from openfactory.core.sizing import belt_under, emitted_parts
from openfactory.protocols.modbus import (
    AddressError,
    Encoding,
    lockfile_path,
    read_lock,
    scene_register_map,
)

#: Types whose ``height`` is a top above the belt they stand on (W206), and what it is called.
HEIGHT_TOPS = {"stopper": "raised blade top", "diverter_arm": "arm top"}
#: How far beside a belt a component's origin may sit and still stand on it (a diverter's pivot).
BESIDE_BELT_M = 0.1
FLOOR_HALF_M = 20.0  # web/src/main.ts draws a 40 m x 40 m floor centred on the origin
SAME_HEIGHT_M = 0.05
_WRITE = re.compile(r"""io\[\s*["']([A-Z][A-Z0-9_]*)["']\s*\]\s*=(?!=)""")
_ACCESS = re.compile(r"""io\[\s*["']([A-Z][A-Z0-9_]*)["']\s*\]""")


@dataclass(frozen=True)
class Finding:
    level: str  # "error" | "warning"
    code: str
    message: str

    def __str__(self) -> str:
        return f"{'ERROR' if self.level == 'error' else 'WARN '} {self.code} {self.message}"


def script_tags(paths: list[Path]) -> tuple[set[str], set[str]]:
    """``(written, read)`` tag names found in stand-in PLC scripts."""
    written: set[str] = set()
    read: set[str] = set()
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for m in _ACCESS.finditer(text):
            (written if _WRITE.match(text, m.start()) else read).add(m.group(1))
    return written, read


def _footprint(comp) -> tuple[float, float, float, float]:
    """World-axis XZ bounds of a conveyor's belt: (x0, x1, z0, z1)."""
    cfg = comp.config
    r = math.radians(cfg.rotation_deg)
    corners = []
    start = -cfg.length / 2 if comp.type == "turntable" else 0.0  # a deck's origin is its centre
    for lx in (start, start + cfg.length):
        for lz in (-cfg.width / 2, cfg.width / 2):
            corners.append(
                (cfg.position[0] + lx * math.cos(r) + lz * math.sin(r),
                 cfg.position[2] - lx * math.sin(r) + lz * math.cos(r))
            )  # fmt: skip
    xs, zs = [c[0] for c in corners], [c[1] for c in corners]
    return min(xs), max(xs), min(zs), max(zs)


def lint_scene(path: Path, scripts: list[Path] | None = None) -> list[Finding]:
    scripts = scripts or []
    try:
        scene = load_scene(path)
        loaded = build(scene, base_dir=path.parent)
        scene_dir = path.parent  # `path` is reused below for a pusher's push path
    except (OSError, ValueError, ValidationError) as exc:
        return [Finding("error", "E100", f"{path}: does not load:\n{exc}")]
    findings: list[Finding] = []
    locked: set[str] = set()
    encodings = [e for e in get_args(Encoding) if lockfile_path(path, e).is_file()]
    for enc in encodings:
        try:
            scene_register_map(scene, enc, scene_path=path, write=False)
        except AddressError as exc:
            findings.append(Finding("error", "E101", f"{lockfile_path(path, enc).name}: {exc}"))
        lock = read_lock(lockfile_path(path, enc))
        locked |= set(lock.addresses) if lock else set()
    written, read = script_tags(scripts)
    for tag in loaded.io.tags():
        where = f"{tag.name} ({tag.owner}, {tag.kind})"
        if tag.kind in PLC_WRITES and tag.name not in locked | written:
            findings.append(
                Finding("warning", "W201", f"{where}: no PLC side writes it (in no lockfile, "
                        "written by no --script); the component only ever sees its initial value")
            )  # fmt: skip
        if tag.kind not in PLC_WRITES and tag.name not in locked | read | {HEARTBEAT_TAG}:
            findings.append(
                Finding("warning", "W202", f"{where}: nothing reads it (in no lockfile, read by "
                        "no --script)")
            )  # fmt: skip
    findings += [Finding("warning", "W203", w) for w in loader_lint(scene)]
    for comp in scene.components:
        x, _, z = comp.position
        if abs(x) > FLOOR_HALF_M or abs(z) > FLOOR_HALF_M:
            findings.append(
                Finding("warning", "W204", f"{comp.id} at x {x:g}, z {z:g} is outside the "
                        f"{2 * FLOOR_HALF_M:g} m x {2 * FLOOR_HALF_M:g} m floor")
            )  # fmt: skip

    def named(cid: str) -> str:
        inst = scene.composite_of(cid)
        return f"{cid} (part of {inst}, a {scene.instance_type(inst)})" if inst else cid

    carriers = [c for c in loaded.components if c.type in CARRIER_TYPES]
    joined = {frozenset((c.from_, c.to)) for c in scene.connections}
    for i, a in enumerate(carriers):
        for b in carriers[i + 1 :]:
            if abs(a.config.position[1] - b.config.position[1]) > SAME_HEIGHT_M:
                continue
            ax0, ax1, az0, az1 = _footprint(a)
            bx0, bx1, bz0, bz1 = _footprint(b)
            overlap_x = min(ax1, bx1) - max(ax0, bx0)
            overlap_z = min(az1, bz1) - max(az0, bz0)
            if overlap_x > 1e-6 and overlap_z > 1e-6 and frozenset((a.id, b.id)) not in joined:
                findings.append(
                    Finding("warning", "W205", f"conveyors {named(a.id)} and {named(b.id)} "
                            f"overlap by {overlap_x:.3g} m x {overlap_z:.3g} m at the same height")
                )  # fmt: skip
    parts = emitted_parts(scene)
    for pusher in (c for c in loaded.components if c.type == "pusher"):
        cfg = pusher.config
        path = push_path(cfg, scene)
        if path is None:
            continue
        top = cfg.position[1] + cfg.plate_height / 2
        for part in parts:
            com = path.belt_y + part.size[1] / 2
            if top < com - 1e-9:
                findings.append(
                    Finding("warning", "W206", f"{pusher.id}: plate top y {top:.3g} m is below "
                            f"the centre of mass of part '{part.name}' (y {com:.3g} m on "
                            f"{path.belt_id}); the plate hits it low and can tip it. Raise the "
                            "pusher's position or plate_height, or leave plate_height unset to "
                            "size it from the parts")
                )  # fmt: skip
        beyond = beyond_path(cfg, scene, path)
        if beyond is not None and beyond.near_m > path.far_m + 1e-6:  # W207, a gap (PLAN 2.5b)
            depth = max((push_depth(cfg, part) for part in parts), default=0.0)
            need = beyond.near_m + depth / 2
            if cfg.stroke < need - 1e-9:
                findings.append(
                    Finding("warning", "W207", f"{pusher.id}: stroke {cfg.stroke:.3g} m leaves "
                            f"the part straddling the seam; {need:.3g} m puts it fully on "
                            f"{beyond.belt_id} ({beyond.near_m:.3g} m to its near edge across "
                            f"the gap after {path.belt_id}, plus half the part depth "
                            f"{depth / 2:.3g} m)")
                )  # fmt: skip
        elif cfg.stroke < path.far_m - 1e-9:  # W207
            findings.append(
                Finding("warning", "W207", f"{pusher.id}: stroke {cfg.stroke:.3g} m stops "
                        f"{path.far_m - cfg.stroke:.3g} m short of {path.belt_id}'s far edge "
                        f"({path.far_m:.3g} m from the retracted face); a pushed part is left "
                        "straddling that edge")
            )  # fmt: skip
    for comp in (c for c in loaded.components if c.type in HEIGHT_TOPS):
        belt = belt_under(comp.config.position, scene, reach=BESIDE_BELT_M)
        if belt is None:
            continue
        for part in parts:
            if comp.config.height < part.size[1] / 2 - 1e-9:
                findings.append(
                    Finding("warning", "W206", f"{comp.id}: {HEIGHT_TOPS[comp.type]} "
                            f"{comp.config.height:.3g} m above {belt.id} is below the centre of "
                            f"mass of part '{part.name}' ({part.size[1] / 2:.3g} m); it meets the "
                            "part low and can tip it. Raise height, or leave it unset to size it "
                            "from the parts")
                )  # fmt: skip
    for d in scene.composites:
        if hasattr(d, "io") and not d.io:  # a {library: name} entry was checked in its file
            findings.append(
                Finding("warning", "W208", f"composite {d.type} exposes no I/O (its io: block is "
                        "empty): no PLC can command or read an instance of it; add io entries "
                        "such as `run: {to: belt.run}`")
            )  # fmt: skip
    findings += _flow_findings(loaded)
    for comp in (c for c in scene.components if c.mesh is not None):
        mesh = scene_dir / comp.mesh.file
        if not has_license_record(mesh):
            findings.append(
                Finding("warning", "W209", f"{comp.id}: mesh {comp.mesh.file} has no license "
                        f"record; add {mesh.name}.license.txt beside it (who made it, the license, "
                        "the source) or list it in THIRD_PARTY.md")
            )  # fmt: skip
    return findings


#: Ticks a tank's time constant must span (W211), at the default 50 Hz.
MIN_TANK_TICKS = 10
DEFAULT_DT = 0.02


def _flow_findings(loaded: Any) -> list[Finding]:
    """E102, E103 and W211 on the scene's flow network (PLAN 3.2, ADR-0005 decision 9)."""
    from openfactory.core import flownet as fn

    net = loaded.flownet
    if net is None:
        return []
    out: list[Finding] = []
    used = {(id(e[0]), e[1]) for lk in net.links for e in (lk.start, lk.end)}
    for comp in loaded.components:
        if comp.type == "tank":
            for name in comp.config.nozzles:
                if (id(comp.node), name) not in used:
                    out.append(Finding("error", "E102", f"{comp.id}: nozzle {name!r} is declared "
                                       "but no link uses it; connect it or remove it"))  # fmt: skip
    # parts of the network (nodes joined by any link, open or shut) with no node of known head
    parent = {id(n): id(n) for n in net.nodes}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for lk in net.links:
        parent[find(id(lk.start[0]))] = find(id(lk.end[0]))
    groups: dict[int, list[Any]] = {}
    for node in net.nodes:
        groups.setdefault(find(id(node)), []).append(node)
    for members in groups.values():
        if not any(node.known_head for node in members):
            ids = ", ".join(n.id for n in members)
            text = f"flow network part {ids}: no tank, supply or open drain, so its heads are "
            out.append(Finding("error", "E103", text + "undefined and nothing flows"))
    for tank in net.tanks:
        slope = 0.0
        for lk in net.links:
            if not isinstance(lk, fn.Resistance) or not math.isfinite(lk.r) or lk.r <= 0:
                continue
            for mine, other in ((lk.start, lk.end), (lk.end, lk.start)):
                if mine[0] is tank and other[0].known_head:
                    drop = abs(tank.nozzle_head(mine[1]) - net._end_head(other))
                    if drop > 0:
                        slope += 1 / (2 * math.sqrt(lk.r * drop))
        if slope > 0:
            tau = tank.area / slope
            if tau < MIN_TANK_TICKS * DEFAULT_DT:
                out.append(Finding("warning", "W211", f"{tank.id}: level time constant "
                                   f"{tau:.3g} s is under {MIN_TANK_TICKS} ticks; the level jumps "
                                   "tick to tick. Make the tank larger or its connection "
                                   "tighter"))  # fmt: skip
    return out


def has_license_record(mesh: Path) -> bool:
    """A ``<file>.license.txt`` sidecar, or a ``THIRD_PARTY.md`` at or above the mesh's folder
    that names the mesh's path relative to it (W209)."""
    if mesh.with_name(mesh.name + ".license.txt").is_file():
        return True
    mesh = mesh.resolve()
    for folder in mesh.parents:
        record = folder / "THIRD_PARTY.md"
        if record.is_file():
            rel = mesh.relative_to(folder).as_posix()
            if rel in record.read_text(encoding="utf-8"):
                return True
    return False
