"""PLAN 1.12 / §2.1: browser physics (Rapier) and headless kinematics agree within 2 ticks.

1. The committed traces in web/src/fixtures/ must match what core/kinematics.py does today
   (tools/parity_trace.py --check), so the replay always tests the current model.
2. web/src/physics.parity.test.ts replays each trace's inputs (belt speeds, plate strokes, spawns,
   removals, tick by tick) through web/src/physics.ts under node and compares every photo-eye edge
   and belt checkpoint with the kinematic ticks.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
WEB = REPO / "web"
spec = importlib.util.spec_from_file_location("parity_trace", REPO / "tools" / "parity_trace.py")
parity_trace = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parity_trace)

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None or not (WEB / "node_modules" / "vitest").is_dir(),
    reason="node and web/node_modules are needed for the Rapier replay",
)


def test_committed_traces_match_the_kinematic_model():
    assert parity_trace.main(["--check"]) == 0


def test_rapier_replay_within_2_ticks_at_scene_friction():
    result = subprocess.run(
        [
            "node",
            "./node_modules/vitest/vitest.mjs",
            "run",
            "src/physics.parity.test.ts",
            "--silent=false",  # keep the PARITY_REPORT lines of passing tests
        ],
        cwd=WEB,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-2000:]
    reports = {
        r["trace"]: r
        for r in (
            json.loads(line.split("PARITY_REPORT ", 1)[1])
            for line in result.stdout.splitlines()
            if "PARITY_REPORT " in line
        )
    }
    assert set(reports) == set(parity_trace.TRACES)
    for name in parity_trace.TRACES:
        data = reports[name]
        deltas = [row["delta"] for row in data["rows"]]
        edges = [
            (r["what"], r["kinematic"], r["delta"]) for r in data["rows"] if "PE_" in r["what"]
        ]
        print(
            f"{name}: mu {data['friction']}, {len(deltas)} comparisons, "
            f"worst |delta| {data['worstDeltaTicks']} ticks; photo-eye edges {edges}"
        )
        assert None not in deltas
        assert max(abs(d) for d in deltas) <= 2
    scene = json.loads((WEB / "src" / "fixtures" / "parity-sorter.json").read_text())
    assert scene["friction"] == pytest.approx(0.8)  # the shipped scene's own friction
