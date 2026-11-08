"""heater (PLAN 3.2, ADR-0005 decision 8): heat into a well-mixed tank at the exact rate, the
power command, the uncovered element, heat loss, faults, and validation."""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from openfactory.core.flownet import DENSITY, SPECIFIC_HEAT
from openfactory.core.iotable import Writer
from openfactory.core.loader import build
from openfactory.core.schema import Scene

TK = {"id": "TK_01", "type": "tank", "description": "Batch Tank", "position": [0, 0, 0],
      "shape": "box", "length": 1.0, "width": 1.0, "height": 2.0, "initial_level": 1.0,
      "initial_temperature_c": 20.0}  # fmt: skip
EH = {"id": "EH_01", "type": "heater", "description": "Batch Tank Immersion Heater",
      "position": [0, 0.2, 0], "tank": "TK_01", "rated_kw": 6.0, "min_level": 0.3,
      "ua_w_per_k": 0.0,
      "io": {"on": "EH_01_ON_DO", "power": "EH_01_PWR_AO", "temp": "TK_01_TT_AI"}}  # fmt: skip


def scene(*components: dict) -> dict:
    return {"openfactory": "0.1", "scene": {"name": "heater-test", "description": "Heater."},
            "plc": {"protocol": "none"}, "components": list(components)}  # fmt: skip


def sim(tank: dict = TK, **overrides: object):  # type: ignore[no-untyped-def]
    return build(Scene.model_validate(scene(tank, {**EH, **overrides})))


def run(s, seconds: float) -> None:  # type: ignore[no-untyped-def]
    for _ in range(round(seconds / 0.02)):
        s.step(0.02)


def test_it_heats_the_tank_at_p_over_rho_cp_v():
    s = sim()
    s.io.set("EH_01_ON_DO", True, Writer.PLC)
    s.io.set("EH_01_PWR_AO", 100.0, Writer.PLC)
    run(s, 0.02)  # the command acts from the next tick
    start = s.component("TK_01").node.temperature
    run(s, 100.0)
    rise = 6000 * 100.0 / (DENSITY * SPECIFIC_HEAT * 1.0)
    assert s.component("TK_01").node.temperature - start == pytest.approx(rise, rel=1e-9)
    assert s.io.get("TK_01_TT_AI") == pytest.approx(s.component("TK_01").node.temperature)
    assert s.component("EH_01").state() == {"on": True, "power_kw": 6.0, "uncovered": False}


def test_the_power_command_scales_it_and_off_or_uncovered_adds_nothing():
    s = sim()
    s.io.set("EH_01_ON_DO", True, Writer.PLC)
    s.io.set("EH_01_PWR_AO", 25.0, Writer.PLC)
    run(s, 0.04)
    assert s.component("EH_01").power_kw == pytest.approx(1.5)
    s.io.set("EH_01_ON_DO", False, Writer.PLC)
    run(s, 0.04)
    assert s.component("EH_01").power_kw == 0.0
    low = sim({**TK, "initial_level": 0.2})
    low.io.set("EH_01_ON_DO", True, Writer.PLC)
    run(low, 1.0)
    assert low.component("EH_01").state()["uncovered"] is True
    assert low.component("TK_01").node.temperature == 20.0


def test_the_tank_loses_heat_to_ambient():
    hot = {**TK, "initial_temperature_c": 60.0}
    s = sim(hot, ua_w_per_k=100.0)
    run(s, 10.0)
    t = s.component("TK_01").node.temperature
    # dT/dt = -UA (T - Ta) / (rho cp V): an exponential decay toward ambient, from the second
    # tick (the heater sets its loss in a tick and the network applies it from the next)
    expected = 20.0 + 40.0 * math.exp(-100 * 9.98 / (DENSITY * SPECIFIC_HEAT * 1.0))
    assert t == pytest.approx(expected, abs=1e-7)


def test_a_stuck_off_element_adds_no_heat():
    s = sim()
    s.io.set("EH_01_ON_DO", True, Writer.PLC)
    s.component("EH_01").fault_inject("stuck_off")
    run(s, 1.0)
    assert s.component("EH_01").power_kw == 0.0 and s.component("TK_01").node.temperature == 20.0


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"tank": "TK_09"}, "EH_01.tank: 'TK_09' is no component, not a tank"),
        ({"min_level": 2.5}, "EH_01: min_level is above TK_01's height"),
    ],
)
def test_a_bad_heater_fails_at_load(overrides, message):
    with pytest.raises(ValidationError, match=message):
        Scene.model_validate(scene(TK, {**EH, **overrides}))
