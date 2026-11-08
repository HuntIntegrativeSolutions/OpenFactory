"""PLAN 3.3: the process scenes under their stand-in scripts for 300 s (the 60 s run is in
test_shipped_scenes.py), with the solver inside its caps; and each scene's checks closing the easy
pass the guides describe: a P-only loop fails tank-level-pid, a single PI loop fails
two-tank-cascade, and a pump with no seal-in fails tank-level-onoff."""

from __future__ import annotations

from pathlib import Path

import pytest

from openfactory.checks import evaluate
from openfactory.core.loader import load
from openfactory.pyplc import ScriptPlc

REPO = Path(__file__).resolve().parents[2]
DT = 0.02
PROCESS = ["tank-level-onoff", "tank-level-pid", "two-tank-cascade"]


def run(scene: str, script: Path, seconds: float) -> tuple[dict[str, tuple[bool, str]], object]:
    loaded = load(REPO / "scenes" / f"{scene}.yaml")
    plc = ScriptPlc(script, loaded.io)
    t = 0.0
    for _ in range(round(seconds / DT)):
        plc.scan(t)
        loaded.step(DT)
        t += DT
    results = evaluate(loaded.scene.checks, loaded)  # type: ignore[arg-type]
    return {r.name.split(":")[0]: (r.passed, r.detail) for r in results}, loaded


@pytest.mark.parametrize("scene", PROCESS)
def test_three_hundred_seconds_under_the_script_inside_the_solver_caps(scene):
    results, loaded = run(scene, REPO / "examples" / "pyplc" / f"{scene}.py", 300.0)
    failed = {name: detail for name, (ok, detail) in results.items() if not ok}
    assert not failed, failed
    stats = loaded.flownet.stats  # type: ignore[attr-defined]
    assert stats.cap_hits == 0 and stats.split_cap_hits == 0
    assert stats.max_iterations <= 20


def script(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / f"{name}.py"
    path.write_text(body, encoding="utf-8")
    return path


def test_a_p_only_loop_fails_the_pid_scene_band(tmp_path):
    p_only = script(tmp_path, "p_only", (
        "def step(io, t):\n"
        "    e = io['TK_01_LT_AI'] - 1.0\n"
        "    io['FV_01_POS_AO'] = 66.0 if t < 1 else min(max(66.0 - 400.0 * e, 0.0), 100.0)\n"
    ))  # fmt: skip
    passed, detail = run("tank-level-pid", p_only, 300.0)[0]["level_within TK_01"]
    assert not passed, detail  # it settles below the setpoint: the offset an integral removes


def test_a_single_pi_loop_fails_the_cascade_band(tmp_path):
    single = script(tmp_path, "single", (
        "iterm = 0.0\n"
        "def step(io, t):\n"
        "    global iterm\n"
        "    e = io['TK_02_LT_AI'] - 0.8\n"
        "    if t < 1.0:\n"
        "        iterm = 30.0 * (63.0 / -300.0 - e)\n"
        "    else:\n"
        "        iterm += e * 0.02\n"
        "    io['FV_01_POS_AO'] = min(max(-300.0 * (e + iterm / 30.0), 0.0), 100.0)\n"
    ))  # fmt: skip
    passed, detail = run("two-tank-cascade", single, 300.0)[0]["level_within TK_02"]
    assert not passed, detail


def test_a_pump_without_a_seal_in_fails_max_starts(tmp_path):
    no_seal = script(
        tmp_path, "no_seal", "def step(io, t):\n    io['P_01_RUN_DO'] = io['TK_02_LSL_DI']\n"
    )
    results = run("tank-level-onoff", no_seal, 60.0)[0]
    assert not results["max_starts P_01"][0], results["max_starts P_01"][1]
    assert results["level_within TK_02"][0]  # the level itself stays in its band
