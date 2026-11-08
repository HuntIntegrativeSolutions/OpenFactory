"""vfd_motor (PLAN 3.2): driving a pump or a conveyor in place of its run and speed ports, the
ramp, the overload trip and its reset, faults, and validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from openfactory.core.iotable import Writer
from openfactory.core.loader import build
from openfactory.core.schema import Scene

SUMP = {"id": "TK_01", "type": "tank", "description": "Sump", "position": [0, 0, 0],
        "diameter": 2.0, "height": 2.0, "initial_level": 1.5}  # fmt: skip
UPPER = {"id": "TK_02", "type": "tank", "description": "Head Tank", "position": [3, 1.0, 0],
         "diameter": 2.0, "height": 2.0, "initial_level": 0.2}  # fmt: skip
PUMP = {"id": "P_01", "type": "pump", "description": "Transfer Pump", "position": [1, 0.2, 0],
        "from": "TK_01.bottom", "to": "TK_02.top", "design_flow_m3h": 6, "design_head_m": 8,
        "ramp_s": 0.1, "io": {"flow": "P_01_FT_AI"}}  # fmt: skip
VFD = {"id": "VFD_01", "type": "vfd_motor", "description": "Transfer Pump Drive",
       "position": [1, 0, 1], "drives": "P_01", "rated_kw": 2.2, "trip_s": 5.0, "accel_s": 2.0,
       "io": {"run": "VFD_01_RUN_DO", "speed": "VFD_01_SPD_AO", "reset": "VFD_01_RST_DO",
              "running": "VFD_01_RUN_DI", "speed_fb": "VFD_01_SPD_AI",
              "fault": "VFD_01_FLT_DI"}}  # fmt: skip
BELT = {"id": "CONV_01", "type": "belt_conveyor", "description": "Infeed Belt",
        "position": [0, 0, 3], "length": 3, "width": 0.5, "speed_mps": 0.5, "io": {}}  # fmt: skip


def scene(*components: dict) -> dict:
    return {"openfactory": "0.1", "scene": {"name": "vfd-test", "description": "Drive test."},
            "plc": {"protocol": "none"}, "components": list(components)}  # fmt: skip


def sim(*components: dict):  # type: ignore[no-untyped-def]
    return build(Scene.model_validate(scene(*components)))


def run(s, seconds: float) -> None:  # type: ignore[no-untyped-def]
    for _ in range(round(seconds / 0.02)):
        s.step(0.02)


def command(s, run_on: bool, speed: float) -> None:  # type: ignore[no-untyped-def]
    s.io.set("VFD_01_RUN_DO", run_on, Writer.PLC)
    s.io.set("VFD_01_SPD_AO", speed, Writer.PLC)


def test_it_drives_a_pump_whose_run_and_speed_are_unbound():
    s = sim(SUMP, UPPER, PUMP, VFD)
    pump, vfd = s.component("P_01"), s.component("VFD_01")
    assert "run" not in pump.bindings and "speed" not in pump.bindings
    command(s, True, 80.0)
    run(s, 1.0)
    assert s.io.get("VFD_01_SPD_AI") == pytest.approx(50.0)  # 100 % per 2 s
    run(s, 2.0)
    assert vfd.output == 80.0 and s.io.get("VFD_01_RUN_DI") is True
    assert pump.speed == pytest.approx(0.8) and pump.link.flow > 0
    command(s, False, 80.0)
    run(s, 3.0)
    assert vfd.output == 0.0 and pump.link.flow == 0.0 and s.io.get("VFD_01_RUN_DI") is False


def test_it_drives_a_belt_conveyor():
    s = sim(BELT, {**VFD, "drives": "CONV_01", "accel_s": 0.02})
    command(s, True, 60.0)
    run(s, 2.0)
    assert s.component("CONV_01").surface_speed == pytest.approx(0.3)  # 60 % of 0.5 m/s


def test_overload_trips_after_trip_s_and_reset_clears_it():
    s = sim(SUMP, UPPER, PUMP, {**VFD, "rated_kw": 0.05})  # the pump draws about 0.2 kW
    vfd = s.component("VFD_01")
    command(s, True, 100.0)
    run(s, 4.0)
    assert vfd.load_kw() > 0.05 and s.io.get("VFD_01_FLT_DI") is False
    run(s, 4.0)  # past 5 s of overload
    assert s.io.get("VFD_01_FLT_DI") is True and vfd.output == 0.0
    assert s.io.get("VFD_01_RUN_DI") is False
    s.io.set("VFD_01_RST_DO", True, Writer.PLC)
    run(s, 0.04)
    assert s.io.get("VFD_01_FLT_DI") is False


def test_without_a_reset_port_turning_run_off_clears_the_fault():
    io = {k: v for k, v in VFD["io"].items() if k != "reset"}
    s = sim(SUMP, UPPER, PUMP, {**VFD, "rated_kw": 0.05, "trip_s": 1.0, "io": io})
    command(s, True, 100.0)
    run(s, 4.0)
    assert s.io.get("VFD_01_FLT_DI") is True
    command(s, False, 100.0)
    run(s, 0.04)
    assert s.io.get("VFD_01_FLT_DI") is False


def test_a_jammed_motor_trips_the_drive():
    s = sim(BELT, {**VFD, "drives": "CONV_01", "trip_s": 1.0})
    command(s, True, 50.0)
    s.component("VFD_01").fault_inject("jammed")
    run(s, 1.1)
    assert s.io.get("VFD_01_FLT_DI") is True


@pytest.mark.parametrize(
    ("components", "message"),
    [
        (
            (SUMP, UPPER, {**PUMP, "io": {"run": "P_01_RUN_DO"}}, VFD),
            "P_01: a vfd_motor drives it, so leave io.run unbound",
        ),
        (
            (SUMP, UPPER, {**PUMP, "io": {"run": "P_01_RUN_DO"}}, {**VFD, "drives": "TK_01"}),
            "VFD_01.drives: 'TK_01' is a tank",
        ),
        (
            (
                SUMP,
                UPPER,
                PUMP,
                VFD,
                {**VFD, "id": "VFD_02", "io": {"run": "VFD_02_RUN_DO", "speed": "VFD_02_SPD_AO"}},
            ),
            "P_01 is driven by both VFD_01 and VFD_02",
        ),
        ((SUMP, UPPER, PUMP), "Field required"),  # undriven, a pump still binds run
    ],
)
def test_bad_drives_fail_at_load(components, message):
    with pytest.raises(ValidationError, match=message):
        Scene.model_validate(scene(*components))
