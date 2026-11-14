"""PLAN 4.1i: the scene's checks while it runs. Pending until they are decided; failed as soon as
running longer cannot make one pass; at duration_s the graded result, as `openfactory test`
grades it, which then stays."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from openfactory.checks import evaluate
from openfactory.core.loader import load
from openfactory.pyplc import ScriptPlc
from openfactory.server.app import create_app
from openfactory.server.runtime import Runtime

REPO = Path(__file__).resolve().parents[2]
SORTER = REPO / "scenes" / "sorting-by-height.yaml"

#: A program that runs the sorter's belts and feeds boxes but never pushes: tall boxes ride on to
#: REM_01, the short boxes' remover.
NO_PUSH = """\
def step(io, t):
    io["CONV_01_RUN_DO"] = True
    io["CONV_02_RUN_DO"] = True
    io["CONV_01_SPD_AO"] = 100.0
    io["CONV_02_SPD_AO"] = 100.0
    io["EMT_01_EN_DO"] = True
"""


def runtime(script: Path) -> Runtime:
    loaded = load(SORTER)
    return Runtime(loaded, realtime=False, controller=ScriptPlc(script, loaded.io))


def run_to(rt: Runtime, seconds: float) -> None:
    while rt.loaded.time < seconds - 1e-9:
        rt._step(0.02)


def states(rt: Runtime) -> dict[str, str]:
    return {c["name"]: c["state"] for c in rt.check_status()["checks"]}


def test_pending_until_graded_then_the_graded_result_stays():
    rt = runtime(REPO / "examples" / "pyplc" / "sorting-by-height.py")
    run_to(rt, 5.0)
    status = rt.check_status()
    assert status["duration_s"] == 60 and not status["graded"]
    assert set(states(rt).values()) == {"pending"}
    run_to(rt, 60.0)
    status = rt.check_status()
    assert status["graded"]
    assert set(states(rt).values()) == {"passed"}
    want = {r.name: r.passed for r in evaluate(rt.loaded.scene.checks, rt.loaded)}
    assert {n: s == "passed" for n, s in states(rt).items()} == want  # what `test` grades
    graded = rt.check_status()["checks"]
    run_to(rt, 70.0)
    assert rt.check_status()["checks"] == graded  # frozen at duration_s


def test_a_failure_running_longer_cannot_undo_shows_at_once(tmp_path):
    script = tmp_path / "no_push.py"
    script.write_text(NO_PUSH, encoding="utf-8")
    rt = runtime(script)
    seen_failed_at = None
    while rt.loaded.time < 59.0:
        rt._step(0.02)
        if states(rt)["parts_reached REM_02: tall"] == "failed":
            seen_failed_at = rt.loaded.time
            break
    assert seen_failed_at is not None and seen_failed_at < 30.0, seen_failed_at
    now = states(rt)
    assert now["parts_reached REM_01: short"] == "pending"  # not decided yet
    assert now["none_lost"] == "pending"
    detail = {c["name"]: c["detail"] for c in rt.check_status()["checks"]}
    assert "to REM_01" in detail["parts_reached REM_02: tall"]


def test_api_checks_is_read_only_and_empty_without_checks(tmp_path):
    rt = runtime(REPO / "examples" / "pyplc" / "sorting-by-height.py")
    client = TestClient(create_app(rt, web_dist=None), base_url="http://127.0.0.1")
    body = client.get("/api/checks").json()
    assert [c["state"] for c in body["checks"]] == ["pending"] * 4
    assert client.post("/api/checks", json={}).status_code == 405
    plain = REPO / "tests" / "scenes" / "demo-roller.yaml"
    loaded = load(plain)
    assert loaded.scene.checks is None
    other = TestClient(
        create_app(Runtime(loaded, realtime=False), web_dist=None), base_url="http://127.0.0.1"
    )
    assert other.get("/api/checks").json()["checks"] == []
