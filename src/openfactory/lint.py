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

from openfactory.core.loader import build
from openfactory.core.loader import lint as loader_lint
from openfactory.core.schema import CARRIER_TYPES, PLC_WRITES, load_scene
from openfactory.protocols.modbus import (
    AddressError,
    Encoding,
    lockfile_path,
    read_lock,
    scene_register_map,
)

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
    for lx in (0.0, cfg.length):
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
    return findings
