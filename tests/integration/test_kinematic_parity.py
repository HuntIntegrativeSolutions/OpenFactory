"""PLAN 1.12 / §2.1: browser physics (Rapier) and headless kinematics agree within tolerance.

Tolerance per trace: max(2 ticks, 25 % of the computed ramp time) (``tools/parity_trace.py``).

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


@pytest.fixture(autouse=True)
def _node_and_vitest(prerequisite):
    prerequisite(
        shutil.which("node") is not None and (WEB / "node_modules" / "vitest").is_dir(),
        "node and web/node_modules are needed for the Rapier replay (npm ci in web/)",
    )


def test_committed_traces_match_the_kinematic_model():
    assert parity_trace.main(["--check"]) == 0


def test_rapier_replay_within_tolerance():
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
        trace = json.loads((WEB / "src" / "fixtures" / f"{name}.json").read_text())
        tolerance = trace["tolerance_ticks"]
        assert data["toleranceTicks"] == tolerance
        assert tolerance == pytest.approx(
            parity_trace.tolerance_ticks(parity_trace.ramp_ticks(0.5, data["friction"], 0.02)),
            abs=1e-6,
        )
        print(
            f"{name}: mu {data['friction']}, ramp {trace['ramp_ticks']} ticks, tolerance "
            f"{tolerance} ticks, {len(deltas)} comparisons, worst |delta| "
            f"{data['worstDeltaTicks']} ticks; photo-eye edges {edges}"
        )
        assert None not in deltas
        assert max(abs(d) for d in deltas) <= tolerance
    scene = json.loads((WEB / "src" / "fixtures" / "parity-sorter.json").read_text())
    assert scene["friction"] == pytest.approx(0.8)  # the shipped scene's own friction
