"""Kinematic parity trace (PLAN 1.12): what the headless model saw, for viewer physics to replay.

Usage::

    uv run python tools/parity_trace.py            # write web/src/fixtures/parity-*.json
    uv run python tools/parity_trace.py --check    # exit 1 if a committed trace is stale
    uv run python tools/parity_trace.py --live [--scene S] [--seconds N]   # informational

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

Live mode (``--live``) compares the running viewer with the core instead of a replay. It starts
``openfactory run`` with the scene's belts and emitters forced on, opens the built viewer in
headless Chromium (``tools/screenshot.py``'s launcher), lets it run ``--seconds`` of sim time, and
reads ``GET /api/parity``: the core's raw beam edges with their ticks, and the browser's sensor
events, each carrying the core tick of the browser step that saw the edge (``tick``; the viewer
steps on core ticks, PLAN 2.0a) and the tick the core received it (``core_tick``). Each browser edge
is paired with the nearest core edge of the same eye and direction; the table shows the
browser-minus- core tick and the delivery latency. The live viewer adds a part only when a frame
first shows it (up to 2-3 ticks after the core spawned it), so live deltas can be looser than the
replay's. The output is informational, not a gate: the exit code is 0 whenever the check ran, and 1
only if it could not run (no ``web/dist``, the viewer failed, no core edges)."""

from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
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


LIVE_PAIR_WINDOW_TICKS = 50


def live_forces(scene_path: Path) -> list[str]:
    """``--force`` arguments that run every belt and enable every emitter, like :func:`trace`."""
    from openfactory.core.loader import load

    loaded = load(scene_path)
    return sorted(
        f"{tag.name}=1"
        for tag in loaded.io.tags()
        if tag.kind == "DO"
        and tag.owner
        and loaded.component(tag.owner).type in ("belt_conveyor", "box_emitter")
    )


def pair_edges(
    core: list[dict[str, Any]], viewer: list[dict[str, Any]], window: int = LIVE_PAIR_WINDOW_TICKS
) -> list[dict[str, Any]]:
    """Pair each browser sensor event with the nearest unused core edge (same eye and direction,
    within ``window`` ticks). Unpaired browser events get ``core_tick=None``."""
    used: set[int] = set()
    rows = []
    for event in viewer:
        tick = event.get("tick")
        best = None
        if isinstance(tick, int | float):
            for i, edge in enumerate(core):
                if i in used or edge["id"] != event["id"] or edge["blocked"] != event["value"]:
                    continue
                gap = abs(tick - edge["tick"])
                if gap <= window and (best is None or gap < abs(tick - core[best]["tick"])):
                    best = i
        if best is not None:
            used.add(best)
        rows.append(
            {
                "id": event["id"],
                "blocked": event["value"],
                "core": core[best]["tick"] if best is not None else None,
                "browser": tick,
                "arrived": event.get("core_tick"),
            }
        )
    return rows


def live(scene_path: Path, seconds: float, timeout: float = 120.0) -> int:
    """Run the viewer against the core and print browser-vs-core sensor ticks (informational)."""
    spec = importlib.util.spec_from_file_location("screenshot", REPO / "tools" / "screenshot.py")
    screenshot = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(screenshot)  # type: ignore[union-attr]
    forces = live_forces(scene_path)
    print(f"live parity: {scene_path.name}, {seconds:g} s, forcing {' '.join(forces)}")
    try:
        with screenshot.running_viewer(scene_path, forces=forces) as (page, port, console):
            status = screenshot.wait_for_viewer(page, console, seconds=seconds, timeout=timeout)
            data = screenshot.get_json(port, "/api/parity")
    except (OSError, RuntimeError, TimeoutError) as exc:
        print(f"live parity could not run: {exc}", file=sys.stderr)
        return 1
    core_ticks = status["tick"] - (status.get("firstTick") or 0)
    print(
        f"viewer physics: {status.get('physics')}, {status.get('frames')} frames; browser stepped "
        f"{status.get('physicsSteps')} physics ticks while the core advanced {core_ticks} "
        f"(dropped by the 10-per-frame cap: {status.get('droppedTicks', 'n/a')})"
    )
    if not data["core"]:
        print("no core beam edges: nothing reached a photo eye", file=sys.stderr)
        return 1
    first_frame_tick = min((e.get("tick") or 0 for e in data["viewer"]), default=0)
    rows = pair_edges(data["core"], data["viewer"])
    print(f"{'eye':<8}{'edge':<9}{'core':>7}{'browser':>9}{'Δ':>6}{'arrived':>9}{'latency':>9}")
    for r in rows:
        delta = r["browser"] - r["core"] if r["core"] is not None else None
        latency = r["arrived"] - r["browser"] if r["arrived"] is not None else None
        print(
            f"{r['id']:<8}{'blocked' if r['blocked'] else 'clear':<9}"
            f"{r['core'] if r['core'] is not None else '-':>7}{r['browser']:>9}"
            f"{delta if delta is not None else '-':>6}{r['arrived']:>9}"
            f"{latency if latency is not None else '-':>9}"
        )
    deltas = [abs(r["browser"] - r["core"]) for r in rows if r["core"] is not None]
    missed = [
        e for e in data["core"] if e["tick"] >= first_frame_tick
        and not any(r["core"] == e["tick"] and r["id"] == e["id"] for r in rows)
    ]  # fmt: skip
    tolerance = trace(scene_path, seconds=0.0)["tolerance_ticks"]
    print(
        f"paired {len(deltas)} of {len(rows)} browser edges; core edges while the viewer ran "
        f"without a browser match: {len(missed)}; "
        + (
            f"median |Δ| {statistics.median(deltas):g}, worst |Δ| {max(deltas):g} ticks "
            f"(replay tolerance {tolerance:g}; informational, not a gate)"
            if deltas
            else "no pairs"
        )
    )
    return 0


def trace_text(friction: float | None) -> str:
    return json.dumps(trace(friction=friction), indent=1, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write or check the kinematic parity traces.")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--live", action="store_true", help="compare a running viewer (info only)")
    parser.add_argument("--scene", type=Path, default=SCENE, help="scene for --live")
    parser.add_argument("--seconds", type=float, default=SECONDS, help="sim seconds for --live")
    args = parser.parse_args(argv)
    if args.live:
        return live(args.scene.resolve(), args.seconds)
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
