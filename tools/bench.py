"""Tick-cost gate (PLAN 2.9): the mean step time of fixed scenes against a committed baseline.

Usage::

    uv run python tools/bench.py             # measure, compare, exit 1 on a regression over 25 %
    uv run python tools/bench.py --update    # measure and rewrite tools/bench_baseline.json
    uv run python tools/bench.py --update --only tank-level-pid   # one entry, the rest kept

Each scene in ``SCENES`` is loaded with every PLC output forced on, and each in ``PROCESS`` under
its stand-in script; each is run ``WARM_TICKS`` ticks to its steady state (200 parts, or a process
loop past its load step), then measured for ``BLOCKS`` blocks of ``BLOCK_TICKS`` ticks. Before each
block the process runs a fixed pure-Python calibration workload (``calibrate``), and the score is
the scene's mean step time divided by the calibration's mean time over the same blocks. A slower or
faster machine, or the same machine on a slower day (about 30 % apart on the development machine,
PLAN 2.9), scales both, so the score moves with the code, not the CPU; raw milliseconds are printed
but not compared. Interleaving the two block by block keeps drift during the run out of the score as
well.

The gate fails when a scene's score is more than ``LIMIT`` times its baseline score. The baseline
is regenerated with ``--update`` and committed with the change that moves it (ruled 2026-11-04),
so a deliberate cost shows up in the diff of ``tools/bench_baseline.json``.

On GitHub Actions (``GITHUB_ACTIONS=true``) each scene's result line and its measurement as JSON
also go out as a workflow annotation (``::notice``, or ``::error`` on a failure). Annotations are
readable through the public check-runs API without signing in, unlike the job log, so the score a
CI runner measured can be read back and, if the runner's score differs from this machine's,
committed as the baseline (PLAN 2.9).

The calibration did not carry between two CI runs (PLAN 2.9, "Bench drift", 2026-11-07): with
the same step code the step took 0.63 times as long on the second, but the calibration only
0.76 times, so the score fell 17-19 %; on one machine both move together. The runs differ in
runner CPU and possibly in Python build (setup-uv v6 to v7). The step is almost all
Python-level calls and attribute access; the calibration's time goes mostly into C builtins
(``insort``, ``sin``, ``hypot``). A candidate, ``calibrate_tick``, is a frozen
miniature of a tick's own operations (small objects, methods, ``getattr``, bisect, tuples). It
is timed in the same blocks and reported beside the gate (``candidate_ms``, ``candidate_score``)
with the CPU model and Python build, but not gated, until CI runs on more than one runner CPU
or Python build show which score holds.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import statistics
import sys
import time
from bisect import bisect_left, insort
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BASELINE = REPO / "tools" / "bench_baseline.json"
#: The fixed scenes: many lanes at the 200-part cap, and two lanes queued nose to tail.
SCENES = {
    "load-200": REPO / "tests" / "scenes" / "load-200.yaml",
    "queue-200": REPO / "tests" / "scenes" / "queue-200.yaml",
}
#: Process scenes (PLAN 3.1, 3.3), each run under its stand-in script for the same fixed tick
#: count (``WARM_TICKS`` then ``BLOCKS`` x ``BLOCK_TICKS``, 5000 ticks = 100 s), so the gate covers
#: a real process scene: the flow network solve, the process components, the I/O table and the
#: script's scan (ruled 2026-11-08; it replaces ``flow-net``, a network built in code).
#: ``tank-level-pid``: the load step at 20 s falls inside the warm-up, so the measured blocks are
#: the loop holding its level after it.
PROCESS = {
    "tank-level-pid": (
        REPO / "scenes" / "tank-level-pid.yaml",
        REPO / "examples" / "pyplc" / "tank-level-pid.py",
    ),
}
DT = 0.02
WARM_TICKS = 3000
BLOCKS = 40
BLOCK_TICKS = 50
#: A score above the baseline's times this fails (a regression over 25 %, ruled 2026-11-04).
LIMIT = 1.25
#: Calibration rounds, sized so one calibration takes about as long as one block of ticks.
CALIBRATION_ROUNDS = 600


def calibrate(rounds: int = CALIBRATION_ROUNDS) -> float:
    """A fixed pure-Python workload of the kind a tick does (tuples, dict lookups, float math,
    sorted inserts). Deterministic; it returns a checksum so the work cannot be skipped."""
    total = 0.0
    for r in range(rounds):
        boxes: dict[int, tuple[float, float, float]] = {}
        order: list[tuple[float, int]] = []
        for i in range(200):
            x = (i * 0.37 + r) % 11.0
            boxes[i] = (x, math.sin(x) * 0.25, 0.15)
            insort(order, (x, i))
        for x, i in order:
            bx, bz, half = boxes[i]
            total += math.hypot(bx - x, bz) + half
    return total


#: Ticks of the candidate calibration per block, about a third of a block of scene ticks.
CANDIDATE_TICKS = 25


class _CalConfig:
    def __init__(self, position: tuple[float, float, float], rotation_deg: float, length: float):
        self.position = position
        self.rotation_deg = rotation_deg
        self.length = length


class _CalPart:
    def __init__(
        self,
        part_id: str,
        position: tuple[float, float, float],
        size: tuple[float, float, float],
        carrier: str,
    ) -> None:
        self.id = part_id
        self.position = position
        self.size = size
        self.carrier = carrier


class _CalBelt:
    def __init__(self, belt_id: str, config: _CalConfig) -> None:
        self.id = belt_id
        self.config = config


def _cal_to_local(
    origin: tuple[float, float, float], rotation_deg: float, point: tuple[float, float, float]
) -> tuple[float, float, float]:
    a = math.radians(rotation_deg)
    c, s = math.cos(a), math.sin(a)
    dx, dy, dz = point[0] - origin[0], point[1] - origin[1], point[2] - origin[2]
    return (c * dx - s * dz, dy, s * dx + c * dz)


class _CalLine:
    """200 parts on four belts. Each tick every part finds the nearest box ahead of it in its
    belt's sorted order and moves up to it, re-sorting the order, the operations
    core/kinematics.py does (not its code, which must not move the calibration). Frozen."""

    def __init__(self) -> None:
        self.belts = {
            f"B{i}": _CalBelt(f"B{i}", _CalConfig((0.0, 0.0, i * 0.8), 0.0, 6.0)) for i in range(4)
        }
        self.parts = [
            _CalPart(
                f"P{j:04d}",
                ((j * 0.37) % 6.0, 0.075, (j % 4) * 0.8),
                (0.15, 0.15, 0.15),
                f"B{j % 4}",
            )
            for j in range(200)
        ]
        self.boxes: dict[str, dict[str, tuple[float, float, float, float, float, float]]] = {}
        self.order: dict[str, list[tuple[float, str]]] = {}

    @staticmethod
    def _frame(belt: _CalBelt) -> tuple[tuple[float, float, float], float]:
        frame = getattr(belt, "frame", None)
        if frame is not None:
            return frame()  # type: ignore[no-any-return]
        return belt.config.position, belt.config.rotation_deg

    @classmethod
    def _local(
        cls, belt: _CalBelt, point: tuple[float, float, float]
    ) -> tuple[float, float, float]:
        origin, rotation = cls._frame(belt)
        return _cal_to_local(origin, rotation, point)

    def _record(self, part: _CalPart) -> None:
        belt = self.belts[part.carrier]
        x, _, z = self._local(belt, part.position)
        box = (x, z, part.size[0] / 2, part.size[2] / 2, part.position[1], part.size[1])
        self.boxes[belt.id][part.id] = box
        insort(self.order[belt.id], (x, part.id))

    def _forget(self, belt_id: str, part_id: str) -> None:
        box = self.boxes[belt_id].pop(part_id, None)
        if box is not None:
            order = self.order[belt_id]
            del order[bisect_left(order, (box[0], part_id))]

    def _room_ahead(self, part: _CalPart, belt: _CalBelt, x: float, z: float, half: float) -> float:
        room = belt.config.length - half
        boxes, order = self.boxes[belt.id], self.order[belt.id]
        for i in range(bisect_left(order, (x,)), len(order)):
            ox, pid = order[i]
            if ox - 2 * half >= room:
                break
            _, oz, o_half_x, o_half_z, oy, oh = boxes[pid]
            if pid == part.id or abs(oy - part.position[1]) >= (oh + part.size[1]) / 2:
                continue
            if ox <= x or abs(oz - z) >= half + o_half_z:
                continue
            room = min(room, ox - o_half_x - half)
        return room

    def step(self, dt: float) -> float:
        for belt_id in self.belts:
            self.boxes[belt_id] = {}
            self.order[belt_id] = []
        for part in self.parts:
            self._record(part)
        moved = 0.0
        for part in self.parts:
            belt = self.belts[part.carrier]
            x, _, z = self._local(belt, part.position)
            half = part.size[0] / 2
            new_x = min(x + 0.5 * dt, max(x, self._room_ahead(part, belt, x, z, half)))
            if new_x >= belt.config.length - half:
                new_x = 0.0  # back to the infeed: the part set never changes
            self._forget(belt.id, part.id)
            part.position = (new_x, part.position[1], part.position[2])
            self._record(part)
            moved += new_x - x
        return moved


def calibrate_tick(ticks: int = CANDIDATE_TICKS) -> float:
    """The candidate calibration: ``ticks`` steps of the frozen miniature line from the same
    start. Deterministic; it returns a checksum so the work cannot be skipped."""
    line = _CalLine()
    total = 0.0
    for _ in range(ticks):
        total += line.step(DT)
    return total


def machine() -> dict[str, str]:
    """The CPU model and Python build, so a score can be put down to the runner it came from."""
    cpu = platform.processor() or platform.machine()
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as f:
            cpu = next(line.split(":", 1)[1].strip() for line in f if line.startswith("model name"))
    except (OSError, StopIteration):
        pass
    build = f"{platform.python_version()} ({platform.python_compiler()})"
    python = f"{platform.python_implementation()} {build}"
    return {"cpu": cpu, "python": python}


def measure(scene: Path, script: Path | None = None) -> dict[str, float]:
    """Mean step and calibration times (ms) over interleaved blocks, and their ratio. A conveyor
    scene runs with every PLC output forced on; a process scene (``script``) runs under its
    stand-in script, scanned before each step as ``openfactory run --script`` does."""
    from openfactory.core.iotable import Writer
    from openfactory.core.loader import load
    from openfactory.pyplc import ScriptPlc

    loaded = load(scene)
    if script is None:
        for tag in loaded.io.tags():
            if tag.kind == "DO":
                loaded.io.set(tag.name, True, Writer.PLC)
        step, count = loaded.step, lambda: len(loaded.world.parts)
    else:
        plc = ScriptPlc(script, loaded.io)

        def step(dt: float) -> None:
            plc.scan(loaded.time)
            loaded.step(dt)

        count = lambda: len(loaded.flownet.links)  # type: ignore[attr-defined]  # noqa: E731
    for _ in range(WARM_TICKS):
        step(DT)
    step_s, cal_s, cand_s = [], [], []
    for _ in range(BLOCKS):
        t0 = time.perf_counter()
        calibrate()
        cal_s.append(time.perf_counter() - t0)
        t0 = time.perf_counter()
        calibrate_tick()
        cand_s.append(time.perf_counter() - t0)
        t0 = time.perf_counter()
        for _ in range(BLOCK_TICKS):
            step(DT)
        step_s.append((time.perf_counter() - t0) / BLOCK_TICKS)
    step_ms, cal_ms = statistics.fmean(step_s) * 1000, statistics.fmean(cal_s) * 1000
    cand_ms = statistics.fmean(cand_s) * 1000
    return {
        "score": round(step_ms / cal_ms, 4),
        "step_ms": round(step_ms, 3),
        "calibration_ms": round(cal_ms, 3),
        "parts": count(),  # a process scene: its network's links
        "candidate_ms": round(cand_ms, 3),  # not gated (see the module docstring)
        "candidate_score": round(step_ms / cand_ms, 4),
        **machine(),
    }


def compare(name: str, got: dict[str, float], base: dict[str, float] | None) -> tuple[bool, str]:
    """(passed, report line) for one scene's measurement against its baseline entry."""
    line = (
        f"{name}: step {got['step_ms']:.3f} ms, calibration {got['calibration_ms']:.3f} ms, "
        f"score {got['score']:.4f}"
    )
    if base is None:
        return False, f"{line}; no baseline (run tools/bench.py --update and commit it)"
    change = got["score"] / base["score"] - 1
    ok = got["score"] <= base["score"] * LIMIT
    verdict = "ok" if ok else f"FAIL: more than {LIMIT - 1:.0%} slower"
    return ok, f"{line}; baseline {base['score']:.4f} ({change:+.1%}), {verdict}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tick-cost gate against a committed baseline.")
    parser.add_argument("--update", action="store_true", help=f"rewrite {BASELINE.name}")
    parser.add_argument(
        "--only",
        metavar="NAME",
        help="measure one scene only; with --update, rewrite its entry and keep the others",
    )
    args = parser.parse_args(argv)
    names = [*SCENES, *PROCESS]
    if args.only is not None and args.only not in names:
        parser.error(f"--only {args.only}: not a bench scene ({', '.join(names)})")
    wanted = [args.only] if args.only else names
    results = {
        name: measure(SCENES[name]) if name in SCENES else measure(*PROCESS[name])
        for name in wanted
    }
    if args.update and args.only:
        doc = json.loads(BASELINE.read_text(encoding="utf-8"))
        doc["scenes"] = {
            n: (results[n] if n == args.only else doc["scenes"][n])
            for n in names
            if n in doc["scenes"] or n == args.only
        }
        BASELINE.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
        got = results[args.only]
        print(f"{args.only}: score {got['score']:.4f} (step {got['step_ms']:.3f} ms)")
        print(f"wrote {BASELINE.relative_to(REPO)} ({args.only} only)")
        return 0
    if args.update:
        doc = {
            "comment": "tools/bench.py --update; commit with the change that moves it (PLAN 2.9)",
            "measured_on": "{python}, {cpu}".format(**machine()),
            "warm_ticks": WARM_TICKS,
            "blocks": BLOCKS,
            "block_ticks": BLOCK_TICKS,
            "scenes": results,
        }
        BASELINE.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
        for name, got in results.items():
            print(f"{name}: score {got['score']:.4f} (step {got['step_ms']:.3f} ms)")
        print(f"wrote {BASELINE.relative_to(REPO)}")
        return 0
    if not BASELINE.is_file():
        print(f"no {BASELINE.relative_to(REPO)}; run tools/bench.py --update", file=sys.stderr)
        return 2
    base = json.loads(BASELINE.read_text(encoding="utf-8"))["scenes"]
    passed = True
    for name, got in results.items():
        ok, line = compare(name, got, base.get(name))
        print(line)
        if os.environ.get("GITHUB_ACTIONS") == "true":
            level = "notice" if ok else "error"
            print(f"::{level} title=tick-cost gate {name}::{line} {json.dumps(got)}")
        passed &= ok
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
