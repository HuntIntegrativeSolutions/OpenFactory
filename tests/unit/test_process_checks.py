"""The process checks (PLAN 3.3, ADR-0005 decision 10): level_within, never_overflow, never_dry
and max_starts, each passing and failing, with their easy passes closed."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from openfactory.checks import evaluate
from openfactory.core.iotable import Writer
from openfactory.core.loader import build
from openfactory.core.schema import Scene

SUMP = {"id": "TK_01", "type": "tank", "description": "Sump", "position": [0, 0, 0],
        "diameter": 0.5, "height": 1.0, "initial_level": 0.6}  # fmt: skip
HEAD = {"id": "TK_02", "type": "tank", "description": "Head Tank", "position": [2, 1.0, 0],
        "diameter": 0.4, "height": 1.0, "initial_level": 0.3}  # fmt: skip
PUMP = {"id": "P_01", "type": "pump", "description": "Transfer Pump", "position": [1, 0.2, 0],
        "from": "TK_01.bottom", "to": "TK_02.top", "design_flow_m3h": 3, "design_head_m": 5,
        "io": {"run": "P_01_RUN_DO"}}  # fmt: skip


def scene(checks: dict) -> Scene:
    return Scene.model_validate({
        "openfactory": "0.1", "scene": {"name": "checks-test", "description": "Process checks."},
        "plc": {"protocol": "none"}, "checks": {"duration_s": 60, **checks},
        "components": [SUMP, HEAD, PUMP],
    })  # fmt: skip


def run(checks: dict, plan: dict[float, bool], seconds: float) -> dict[str, tuple[bool, str]]:
    """Step the scene, setting the pump's run command at the planned times; grade it."""
    loaded = build(scene(checks))
    for k in range(round(seconds / 0.02)):
        t = round(k * 0.02, 2)
        if t in plan:
            loaded.io.set("P_01_RUN_DO", plan[t], Writer.PLC)
        loaded.step(0.02)
    return {
        r.name.split(":")[0]: (r.passed, r.detail) for r in evaluate(loaded.scene.checks, loaded)
    }  # type: ignore[arg-type]


def test_level_within_holds_from_after_s_and_fails_outside_or_before_it():
    band = {"level_within": {"TK_02": {"band": [0.25, 0.35], "after_s": 1.0}}}
    assert run(band, {}, 5.0)["level_within TK_02"][0]  # the pump never runs: it stays at 0.3
    moved = run(band, {0.0: True}, 5.0)["level_within TK_02"]
    assert not moved[0] and "level 0.3" in moved[1]  # it rose out of the band
    early = run({"level_within": {"TK_02": {"band": [0.25, 0.35], "after_s": 10.0}}}, {}, 5.0)
    assert early["level_within TK_02"] == (False, "the run ended before 10 s")


def test_never_overflow_and_never_dry():
    # 3 m³/h for 60 s moves about 0.05 m³: the head tank (0.088 m³ of room) holds it
    calm = run({"never_overflow": ["TK_02"], "never_dry": ["P_01"]}, {0.0: True}, 60.0)
    assert calm["never_overflow TK_02"] == (True, "never overflowed")
    assert calm["never_dry P_01"] == (True, "never ran dry")
    spilled = run({"never_overflow": ["TK_02"]}, {0.0: True}, 120.0)["never_overflow TK_02"]
    assert spilled[0] is False and spilled[1].startswith("overflowed")
    dry = run({"never_dry": ["P_01"]}, {0.0: True}, 240.0)  # pumps the sump empty, keeps running
    assert dry["never_dry P_01"][0] is False and dry["never_dry P_01"][1].startswith("ran dry")


def test_max_starts_counts_run_turning_on_and_needs_at_least_one():
    plan = {0.0: True, 1.0: False, 2.0: True, 3.0: False, 4.0: True}
    assert run({"max_starts": {"P_01": 3}}, plan, 5.0)["max_starts P_01"] == (
        True,
        "3 starts; limit 3",
    )
    assert run({"max_starts": {"P_01": 2}}, plan, 5.0)["max_starts P_01"][0] is False
    assert run({"max_starts": {"P_01": 2}}, {}, 5.0)["max_starts P_01"] == (False, "never started")


@pytest.mark.parametrize(
    ("checks", "message"),
    [
        (
            {"level_within": {"P_01": {"band": [0, 1]}}},
            "checks.level_within: 'P_01' is a pump, not a tank",
        ),
        ({"never_overflow": ["TK_09"]}, "checks.never_overflow: 'TK_09' is not in the scene"),
        ({"never_dry": ["TK_01"]}, "checks.never_dry: 'TK_01' is a tank, not a pump"),
        ({"max_starts": {"P_01": 0}}, "greater than or equal to 1"),
        ({"level_within": {"TK_02": {"band": [1, 0]}}}, "band \\[lo, hi\\] needs lo < hi"),
    ],
)
def test_checks_name_real_tanks_and_pumps(checks, message):
    with pytest.raises(ValidationError, match=message):
        scene(checks)


def test_a_scene_without_process_checks_keeps_no_watch():
    loaded = build(scene({}))
    assert loaded.watch is None
