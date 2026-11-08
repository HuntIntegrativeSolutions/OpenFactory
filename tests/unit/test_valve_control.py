"""valve_control (PLAN 3.2, ADR-0005 decision 6): characteristics, the actuator's rate limit and
lag, position feedback, the fail position (float32 only), the hand valve, and faults."""

from __future__ import annotations

import pytest

from openfactory.components.valve_control import capacity
from openfactory.core.iotable import Writer
from openfactory.core.loader import build
from openfactory.core.schema import Scene
from openfactory.protocols.modbus import decode

SUP = {
    "id": "SUP_01",
    "type": "supply",
    "description": "Supply",
    "position": [0, 0, 0],
    "pressure_bar": 1.0,
}
DR = {"id": "DR_01", "type": "drain", "description": "Open Drain", "position": [2, 0, 0]}
FV = {
    "id": "FV_01", "type": "valve_control", "description": "Tank Inlet Flow Control Valve",
    "position": [1, 0, 0], "from": "SUP_01", "to": "DR_01", "kv": 10.0,
    "io": {"position": "FV_01_POS_AO", "position_fb": "FV_01_ZT_AI"},
}  # fmt: skip


def scene(*components: dict, encoding: str = "float32") -> dict:
    return {"openfactory": "0.1", "scene": {"name": "fv-test", "description": "Valve test."},
            "plc": {"protocol": "modbus_tcp", "analog_encoding": encoding},
            "components": list(components)}  # fmt: skip


def sim(**overrides: object):  # type: ignore[no-untyped-def]
    return build(Scene.model_validate(scene(SUP, DR, {**FV, **overrides})))


def run(s, seconds: float) -> None:  # type: ignore[no-untyped-def]
    for _ in range(round(seconds / 0.02)):
        s.step(0.02)


def test_the_characteristics():
    assert capacity("linear", 50, 0.5) == 0.5
    assert capacity("equal_percentage", 50, 0.5) == pytest.approx(50**-0.5)
    assert capacity("equal_percentage", 50, 1.0) == 1.0
    assert capacity("equal_percentage", 50, 0.0) == 0.0  # closes tight
    assert capacity("linear", 50, 1.2) == 1.0


def test_flow_follows_kv_and_the_characteristic_at_the_stem_position():
    s = sim(lag_s=0, stroke_s=0.02)  # as fast as a tick: the stem is at the command next tick
    fv = s.component("FV_01")
    s.io.set("FV_01_POS_AO", 100.0, Writer.PLC)
    run(s, 0.1)
    assert fv.state()["flow_m3h"] == pytest.approx(10.0, rel=1e-3)  # Kv at 1 bar
    s.io.set("FV_01_POS_AO", 50.0, Writer.PLC)
    run(s, 0.1)
    assert fv.state()["flow_m3h"] == pytest.approx(10.0 * 50**-0.5, rel=1e-3)
    s2 = sim(lag_s=0, stroke_s=0.02, characteristic="linear")
    s2.io.set("FV_01_POS_AO", 50.0, Writer.PLC)
    run(s2, 0.1)
    assert s2.component("FV_01").state()["flow_m3h"] == pytest.approx(5.0, rel=1e-3)


def test_the_stem_is_rate_limited_and_lags_and_the_feedback_reports_it():
    s = sim(lag_s=0, stroke_s=10.0)
    s.io.set("FV_01_POS_AO", 100.0, Writer.PLC)
    run(s, 2.0)
    assert s.component("FV_01").stem == pytest.approx(20.0)  # 10 %/s
    assert s.io.get("FV_01_ZT_AI") == pytest.approx(20.0)
    s = sim(lag_s=1.0, stroke_s=0.1)  # the lag, not the rate limit, sets the pace
    s.io.set("FV_01_POS_AO", 100.0, Writer.PLC)
    run(s, 1.0)
    assert s.component("FV_01").stem == pytest.approx(100 * (1 - 0.98**50), rel=1e-6)  # ~63 %


@pytest.mark.parametrize(("fail", "expected"), [("closed", 0.0), ("open", 100.0), ("last", 40.0)])
def test_a_command_below_zero_sends_it_to_its_fail_position_under_float32(fail, expected):
    s = sim(lag_s=0, stroke_s=0.02, fail=fail)
    s.io.set("FV_01_POS_AO", 40.0, Writer.PLC)
    run(s, 0.1)
    s.io.set("FV_01_POS_AO", -5.0, Writer.PLC)  # a float32 AO can carry it
    run(s, 0.1)
    assert s.component("FV_01").stem == pytest.approx(expected)


def test_under_int16_scaled_a_command_never_reads_below_zero_so_fail_never_acts():
    s = build(Scene.model_validate(scene(SUP, DR, {**FV, "fail": "open"}, encoding="int16_scaled")))
    tag = s.io.tag("FV_01_POS_AO")
    assert decode(tag, [0], "int16_scaled") == 0.0  # the lowest raw count is 0 %, not below
    assert decode(tag, [0xFFFF], "int16_scaled") == 100.0  # an unsigned write above 32767 is 100 %


def test_unbound_it_is_a_hand_valve_held_at_its_opening():
    s = sim(io={}, opening=30.0, characteristic="linear")
    run(s, 0.1)
    fv = s.component("FV_01")
    assert fv.stem == 30.0 and fv.state()["flow_m3h"] == pytest.approx(3.0, rel=1e-3)


def test_the_stem_starts_at_its_command():
    s = sim(io={"position": {"tag": "FV_01_POS_AO", "initial": 60.0}})
    assert s.component("FV_01").stem == 60.0


def test_faults_jammed_sticks_the_stem_and_drift_moves_the_feedback():
    s = sim(lag_s=0, stroke_s=10.0)
    fv = s.component("FV_01")
    s.io.set("FV_01_POS_AO", 100.0, Writer.PLC)
    run(s, 1.0)
    fv.fault_inject("jammed")
    run(s, 1.0)
    assert fv.stem == pytest.approx(10.0)
    fv.fault_inject("drift", rate=1.0)
    run(s, 2.0)
    assert s.io.get("FV_01_ZT_AI") == pytest.approx(fv.stem + 2.0, abs=0.05)
