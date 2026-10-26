"""Kinematic parity trace (PLAN 1.12): what the headless model saw, for viewer physics to replay.

Usage::

    uv run python tools/parity_trace.py            # write web/src/fixtures/parity-*.json
    uv run python tools/parity_trace.py --check    # exit 1 if a committed trace is stale

A trace runs a scene headless (``core/kinematics.py``) with its belts forced on at full speed and
its emitters enabled, and records, per tick k (1-based):

- ``inputs``: what the Rapier replay must apply *before* stepping tick k, so both models see the
  same inputs at the same time. The kinematic model moves parts in tick k at the belt speeds and
  plate positions the components reached at the end of tick k-1, and parts spawned or removed in
  tick k-1 first appear or disappear in tick k. Only ticks where something changes are listed.
- ``edges``: raw (undebounced) photo-eye beam edges ``[tick, blocked]`` after tick k.
- ``checkpoints``: for each part, the first tick its center passed each x checkpoint on a belt.
- ``ramp_ticks`` and ``tolerance_ticks``: the parity tolerance for this trace (below).

``web/src/physics.parity.test.ts`` replays the trace through ``web/src/physics.ts`` and requires
every edge and checkpoint to agree within the trace's ``tolerance_ticks``.
``tests/integration/test_kinematic_parity.py`` checks the committed traces are current and runs that
replay.

Tolerance (PLAN 1.12): ``max(2 ticks, 25 % of the computed ramp time)``. The ramp time is how long a
part takes to reach belt speed from rest under friction alone, ``v / (μ·g)``, for the fastest belt
in the trace (belts forced to 100 %), in ticks of ``dt``. Lower friction stretches the ramp, and the
two models' different contact handling (an impulse solver against a per-tick μ·g cap) spreads over
more ticks, so the allowance grows with it. It is not rounded: deltas are whole ticks, so at μ 0.3
(ramp 8.5 ticks, 25 % = 2.12) a delta of 2 passes and 3 fails.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "web" / "src" / "fixtures"
SCENE = REPO / "scenes" / "sorting-by-height.yaml"
SECONDS = 20.0
CHECKPOINTS_X = (1.0, 2.0, 3.0)
GRAVITY = 9.81
MIN_TOLERANCE_TICKS = 2.0
RAMP_FRACTION = 0.25
#: name -> scene friction override (None = the scene's own value)
TRACES: dict[str, float | None] = {"parity-sorter": None, "parity-sorter-mu0.3": 0.3}


def _r(v: float) -> float:
    return round(v, 6)


def ramp_ticks(belt_speed_mps: float, friction: float, dt: float) -> float:
    """Ticks for a part at rest to reach ``belt_speed_mps`` at μ·g: ``v / (μ·g) / dt``."""
    return belt_speed_mps / (friction * GRAVITY) / dt


def tolerance_ticks(ramp: float) -> float:
    """Parity tolerance: ``max(2 ticks, 25 % of the ramp time)`` (PLAN 1.12), not rounded."""
    return max(MIN_TOLERANCE_TICKS, RAMP_FRACTION * ramp)


def trace(
    scene_path: Path = SCENE, seconds: float = SECONDS, friction: float | None = None
) -> dict[str, Any]:
    from openfactory.core.iotable import Writer
    from openfactory.core.loader import build
    from openfactory.core.schema import load_scene
    from openfactory.server.runtime import Runtime

    scene = load_scene(scene_path)
    if friction is not None:
        scene = scene.model_copy(
            update={"scene": scene.scene.model_copy(update={"friction": friction})}
        )
    loaded = build(scene)
    doc = Runtime(loaded).scene_document()  # same geometry the viewer gets from /api/scene
    for tag in loaded.io.tags():
        if (
            tag.kind == "DO"
            and tag.owner
            and loaded.component(tag.owner).type
            in (
                "belt_conveyor",
                "box_emitter",
            )
        ):
            loaded.io.set(tag.name, True, Writer.PLC)
        elif tag.kind == "AO":
            loaded.io.set(tag.name, 100.0, Writer.PLC)
    dt = doc["dt"]
    belts = [c for c in loaded.components if c.type == "belt_conveyor"]
    pushers = [c for c in loaded.components if c.type == "pusher"]
    eyes = [c for c in loaded.components if c.type == "photo_eye"]

    inputs: list[dict[str, Any]] = []
    edges: dict[str, list[list[Any]]] = {e.id: [] for e in eyes}
    checkpoints: dict[str, dict[str, int]] = {}
    last_belts: dict[str, float] = {}
    last_plates: dict[str, float] = {}
    known: dict[str, Any] = {}
    blocked = {e.id: False for e in eyes}
    ticks = round(seconds / dt)
    for k in range(1, ticks + 1):
        entry: dict[str, Any] = {"tick": k}
        speeds = {
            b.id: _r(b.surface_speed) for b in belts if _r(b.surface_speed) != last_belts.get(b.id)
        }
        plates = {p.id: _r(p.position) for p in pushers if _r(p.position) != last_plates.get(p.id)}
        spawned = [p for pid, p in loaded.world.parts.items() if pid not in known]
        removed = [pid for pid in known if pid not in loaded.world.parts]
        if speeds:
            entry["belts"] = speeds
            last_belts.update(speeds)
        if plates:
            entry["pushers"] = plates
            last_plates.update(plates)
        if spawned:
            entry["spawn"] = [
                {
                    "id": p.id,
                    "name": p.name,
                    "size": list(p.size),
                    "position": [_r(v) for v in p.position],
                }
                for p in spawned
            ]
        if removed:
            entry["remove"] = removed
        if len(entry) > 1:
            inputs.append(entry)
        known = dict(loaded.world.parts)
        loaded.step(dt)
        for eye in eyes:
            if eye.blocked != blocked[eye.id]:
                blocked[eye.id] = eye.blocked
                edges[eye.id].append([k, eye.blocked])
        for part in loaded.world.parts.values():
            if part.carrier is None:
                continue
            marks = checkpoints.setdefault(part.id, {})
            for x in CHECKPOINTS_X:
                if f"{x:g}" not in marks and part.position[0] >= x:
                    marks[f"{x:g}"] = k
    mu = loaded.kinematics.max_accel / GRAVITY
    ramp = ramp_ticks(max(b.config.speed_mps for b in belts), mu, dt)
    return {
        "generator": "tools/parity_trace.py",
        "scene": doc,
        "dt": dt,
        "ticks": ticks,
        "friction": mu,
        "ramp_ticks": _r(ramp),
        "tolerance_ticks": _r(tolerance_ticks(ramp)),
        "inputs": inputs,
        "edges": edges,
        "checkpoints": checkpoints,
    }


def trace_text(friction: float | None) -> str:
    return json.dumps(trace(friction=friction), indent=1, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write or check the kinematic parity traces.")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    stale = 0
    for name, friction in TRACES.items():
        path = FIXTURES / f"{name}.json"
        text = trace_text(friction)
        if args.check:
            if not path.is_file() or path.read_text(encoding="utf-8") != text:
                print(f"{path} is stale; run: uv run python tools/parity_trace.py", file=sys.stderr)
                stale += 1
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        data = json.loads(text)
        print(
            f"wrote {path.relative_to(REPO)}: {data['ticks']} ticks, μ {data['friction']:g}, "
            f"ramp {data['ramp_ticks']:g} ticks, tolerance {data['tolerance_ticks']:g} ticks, "
            f"edges {data['edges']}"
        )
    return 1 if stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
