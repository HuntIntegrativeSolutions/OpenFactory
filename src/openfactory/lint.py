"""Scene lint (PLAN 2.20): ``openfactory lint SCENE... [--script FILE.py]``.

Behavior spec
-------------
:func:`lint_scene` returns :class:`Finding` objects, each an error or a warning with a stable code:

Errors (``openfactory lint`` exits 1):

- ``E100`` the scene does not load. That covers YAML, schema, and component build errors, and an
  unbound required port, which the schema reports as ``Field required`` with the port's path.
- ``E101`` a Modbus address lockfile conflicts with the scene (ADR-0004).

Warnings (exit 0 unless there are errors):

- ``W201`` a DO/AO tag that no PLC side can write: it is in none of the scene's lockfiles (so no
  Modbus PLC has an address for it) and no ``--script`` writes it.
- ``W202`` a DI/AI tag that nothing reads: in no lockfile and read by no ``--script``.
- ``W203`` conveyors that touch end to end with no connection (the loader's Milestone 1 check).
- ``W204`` a component outside the floor, the viewer's 40 m × 40 m plane centred on the origin
  (``|x|`` or ``|z|`` over 20 m).
- ``W205`` two conveyors whose footprints overlap at the same belt height (within 5 cm). Conveyors
  joined end to end by a connection are allowed to touch.
- ``W206`` a pusher plate whose top is below the centre of mass of a part the scene emits, resting
  on the belt the plate sweeps: the plate hits the part low and tips it. The same check covers a
  ``stopper``'s raised blade top and a ``diverter_arm``'s arm top (their ``height``, above the belt
  they stand on).
- ``W207`` a pusher stroke shorter than the distance from the plate's retracted face to the far edge
  of the belt it sweeps: a pushed part is left straddling that edge (the seam to the next belt).

W206 and W207 use :func:`openfactory.components.pusher.push_path`, the first belt along the plate's
centre line, and the plate height after the loader's default.

Composite components (PLAN 2.16) do not exist yet; their "exposes no I/O" check arrives with them.

Scripts are scanned as text, not run: ``io["TAG"] = ...`` counts as a write and any other
``io["TAG"]`` as a read (the ``openfactory.pyplc`` convention).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import get_args

from pydantic import ValidationError

from openfactory.components.pusher import push_path
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
        loaded = build(scene)
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
        if tag.kind not in PLC_WRITES and tag.name not in locked | read:
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
                    Finding("warning", "W205", f"conveyors {a.id} and {b.id} overlap by "
                            f"{overlap_x:.3g} m x {overlap_z:.3g} m at the same height")
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
        if cfg.stroke < path.far_m - 1e-9:  # W207
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
    return findings
