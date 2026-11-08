"""pump (PLAN 3.2, ADR-0005): the curve against the system, run and speed with the ramp, the
running and flow inputs, running dry, faults, three-point curves, and lint W210."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from openfactory.core import flownet
from openfactory.core.iotable import Writer
from openfactory.core.loader import build
from openfactory.core.schema import Scene
from openfactory.lint import lint_scene

SUMP = {"id": "TK_01", "type": "tank", "description": "Sump", "position": [0, 0, 0],
        "diameter": 2.0, "height": 2.0, "initial_level": 1.0}  # fmt: skip
UPPER = {"id": "TK_02", "type": "tank", "description": "Head Tank", "position": [3, 2.0, 0],
         "diameter": 1.0, "height": 2.0, "initial_level": 0.5}  # fmt: skip
P = {
    "id": "P_01", "type": "pump", "description": "Transfer Pump", "position": [1, 0.3, 0],
    "from": "TK_01.bottom", "to": "TK_02.top", "design_flow_m3h": 6, "design_head_m": 8,
    "ramp_s": 1.0, "io": {"run": "P_01_RUN_DO", "speed": "P_01_SPD_AO", "running": "P_01_RUN_DI",
                         "flow": "P_01_FT_AI"},
}  # fmt: skip


def scene(*components: dict) -> dict:
    return {"openfactory": "0.1", "scene": {"name": "pump-test", "description": "Pump test."},
            "plc": {"protocol": "none"}, "components": list(components)}  # fmt: skip


def sim(*components: dict, **overrides: object):  # type: ignore[no-untyped-def]
    return build(Scene.model_validate(scene(*(components or (SUMP, UPPER)), {**P, **overrides})))


def run(s, seconds: float) -> None:  # type: ignore[no-untyped-def]
    for _ in range(round(seconds / 0.02)):
        s.step(0.02)


def start(s, speed: float = 100.0) -> None:  # type: ignore[no-untyped-def]
    s.io.set("P_01_RUN_DO", True, Writer.PLC)
    s.io.set("P_01_SPD_AO", speed, Writer.PLC)


def test_running_follows_run_and_the_speed_ramps():
    s = sim()
    pump = s.component("P_01")
    run(s, 0.1)
    assert s.io.get("P_01_RUN_DI") is False and pump.state()["flow_m3h"] == 0.0
    start(s)
    s.step(0.02)
    assert s.io.get("P_01_RUN_DI") is True  # the contactor's auxiliary, same tick
    run(s, 0.48)
    assert pump.speed == pytest.approx(0.5)
    run(s, 1.0)
    assert pump.speed == 1.0
    s.io.set("P_01_RUN_DO", False, Writer.PLC)
    run(s, 0.5)
    assert pump.speed == pytest.approx(0.5) and s.io.get("P_01_RUN_DI") is False


def test_it_delivers_where_its_curve_meets_the_lift_into_a_top_inlet():
    s = sim()
    pump = s.component("P_01")
    start(s)
    run(s, 2.0)
    # into the top nozzle: the lift is to the nozzle (2 m up + 2 m tank) from the sump surface
    lift = (2.0 + 2.0) - (0.0 + s.component("TK_01").node.level)
    q = pump.link.flow
    assert pump.link.gain(q) == pytest.approx(lift, abs=1e-3)
    assert s.io.get("P_01_FT_AI") == pytest.approx(q * 3600)
    assert s.io.tag("P_01_FT_AI").range == (0.0, 12.0)


def test_half_speed_follows_the_affinity_law_at_shutoff():
    s = sim()
    pump = s.component("P_01")
    start(s, 50.0)
    run(s, 2.0)
    assert pump.link.gain(0.0) == pytest.approx(0.25 * 1.33 * 8)  # head scales as speed squared
    assert pump.link.flow == 0.0  # 2.66 m at shutoff cannot reach the 3 m lift: it delivers nothing


def test_running_its_sump_dry_it_delivers_nothing_and_says_so():
    small = {**SUMP, "diameter": 0.3, "initial_level": 0.3}
    s = sim(small, UPPER)
    pump = s.component("P_01")
    start(s)
    run(s, 30.0)
    assert s.component("TK_01").node.level == 0.0 and pump.link.flow == 0.0
    assert pump.state()["dry"] is True and s.io.get("P_01_RUN_DI") is True


def test_faults_jammed_stops_the_flow_with_running_honest_and_slow_wears_the_head():
    s = sim()
    pump = s.component("P_01")
    start(s)
    run(s, 2.0)
    healthy = pump.link.flow
    pump.fault_inject("slow", factor=0.8)
    run(s, 0.1)
    assert 0 < pump.link.flow < healthy and pump.link.shutoff == pytest.approx(0.8 * pump.shutoff)
    pump.fault_inject("jammed")
    run(s, 0.1)
    assert pump.link.flow == 0.0 and s.io.get("P_01_RUN_DI") is True


def test_a_three_point_curve_is_fitted_through_its_points():
    s = sim(curve=[[0, 12.0], [5, 10.0], [10, 4.0]])
    pump = s.component("P_01")
    for q, h in ((0, 12.0), (5, 10.0), (10, 4.0)):
        assert pump.shutoff - pump.coefficient * (q / 3600) ** pump.exponent == pytest.approx(h)
    assert s.io.tag("P_01_FT_AI").range == (0.0, 10.0)
    with pytest.raises(ValidationError, match="curve needs three points"):
        Scene.model_validate(scene(SUMP, UPPER, {**P, "curve": [[1, 12.0], [5, 10.0], [10, 4.0]]}))


def test_shaft_power_is_hydraulic_power_over_efficiency():
    s = sim()
    pump = s.component("P_01")
    start(s)
    run(s, 2.0)
    q, h = pump.link.flow, pump.head_m()
    assert pump.power_kw() == pytest.approx(flownet.DENSITY * 9.81 * q * h / 0.6 / 1000)


def test_lint_warns_when_the_design_head_is_below_the_static_lift(tmp_path: Path):
    def w210(upper: dict) -> list[str]:
        path = tmp_path / "pump.yaml"
        path.write_text(yaml.safe_dump(scene(SUMP, upper, P), sort_keys=False), encoding="utf-8")
        return [f.message for f in lint_scene(path) if f.code == "W210"]

    high = {**UPPER, "position": [3, 9.0, 0]}  # top inlet 11 m up, sump surface 1 m: 10 m lift
    assert w210(high) == [
        "P_01: design head 8 m is below the static lift it faces, 10 m (TK_01 to TK_02); it "
        "cannot deliver its design flow"
    ]
    assert w210(UPPER) == []  # 3 m of lift
