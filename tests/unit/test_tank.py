"""tank (PLAN 3.2, ADR-0005): area by shape, level and temperature inputs, level switches with
their deadband, overflow state, faults, and validation."""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from openfactory.core.loader import build
from openfactory.core.schema import Scene

TK = {
    "id": "TK_01",
    "type": "tank",
    "description": "Process Water Tank",
    "position": [0.0, 0.0, 0.0],
    "diameter": 1.0,
    "height": 2.0,
    "initial_level": 0.5,
    "io": {
        "level": "TK_01_LT_AI",
        "hi_sw": "TK_01_LSH_DI",
        "lo_sw": "TK_01_LSL_DI",
        "temp": "TK_01_TT_AI",
    },
}


def scene(*components: dict) -> dict:
    return {
        "openfactory": "0.1",
        "scene": {"name": "tank-test", "description": "Tank test."},
        "plc": {"protocol": "none"},
        "components": list(components),
    }


def loaded(**overrides: object):  # type: ignore[no-untyped-def]
    return build(Scene.model_validate(scene({**TK, **overrides})))


def set_level(sim, level: float) -> None:  # type: ignore[no-untyped-def]
    tank = sim.component("TK_01")
    tank.node.volume = tank.node.area * level
    sim.step(0.02)


def test_a_cylinder_and_a_box_have_their_areas_and_the_level_is_an_ai_over_the_height():
    sim = loaded()
    tank = sim.component("TK_01")
    assert tank.node.area == pytest.approx(math.pi / 4)
    assert sim.io.tag("TK_01_LT_AI").range == (0.0, 2.0) and sim.io.tag("TK_01_LT_AI").units == "m"
    sim.step(0.02)
    assert sim.io.get("TK_01_LT_AI") == pytest.approx(0.5)
    assert sim.io.get("TK_01_TT_AI") == pytest.approx(20.0)
    box = loaded(shape="box", length=2.0, width=0.5, diameter=1.0).component("TK_01")
    assert box.node.area == pytest.approx(1.0)
    assert sim.flownet is not None and sim.flownet.tanks == [tank.node]


def test_the_switches_turn_on_at_their_levels_and_off_past_the_deadband():
    sim = loaded()  # hi 1.8 m, lo 0.2 m by default
    sw = lambda: (sim.io.get("TK_01_LSH_DI"), sim.io.get("TK_01_LSL_DI"))  # noqa: E731
    set_level(sim, 1.0)
    assert sw() == (False, False)
    set_level(sim, 1.8)
    assert sw() == (True, False)
    set_level(sim, 1.79)  # within 0.02 m: still on
    assert sw() == (True, False)
    set_level(sim, 1.77)
    assert sw() == (False, False)
    set_level(sim, 0.2)
    assert sw() == (False, True)
    set_level(sim, 0.21)
    assert sw() == (False, True)
    set_level(sim, 0.23)
    assert sw() == (False, False)


def test_switch_levels_can_be_set():
    sim = loaded(hi_level=1.5, lo_level=0.6)
    set_level(sim, 1.5)
    assert sim.io.get("TK_01_LSH_DI")
    set_level(sim, 0.6)
    assert sim.io.get("TK_01_LSL_DI")


def test_overflowing_is_true_only_on_ticks_when_the_tank_overflows():
    sim = loaded()
    tank = sim.component("TK_01")
    sim.step(0.02)
    assert tank.state()["overflowing"] is False
    tank.node.overflow += 0.001  # what the solver does when liquid arrives at a full tank
    sim.step(0.02)
    assert tank.state()["overflowing"] is True
    sim.step(0.02)
    assert tank.state()["overflowing"] is False
    assert set(tank.state()) == {"level_m", "volume_m3", "temperature_c", "overflowing"}


def test_faults_misaligned_shifts_the_level_and_the_switches_and_stuck_holds_a_switch():
    sim = loaded()
    tank = sim.component("TK_01")
    set_level(sim, 1.7)
    tank.fault_inject("misaligned", offset_m=0.12)
    sim.step(0.02)
    assert sim.io.get("TK_01_LT_AI") == pytest.approx(1.82)
    assert sim.io.get("TK_01_LSH_DI") is True
    tank.fault_inject("stuck_off", port="hi_sw")
    sim.step(0.02)
    assert sim.io.get("TK_01_LSH_DI") is False
    assert tank.FAULTS == ("stuck_on", "stuck_off", "drift", "noisy", "misaligned")


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"shape": "box"}, "a box tank needs length and width"),
        ({"length": 1.0}, "a vertical_cylinder takes diameter"),
        ({"initial_level": 2.5}, "initial_level 2.5 m is above height"),
        ({"nozzles": {"side": 2.5}}, "nozzle side at 2.5 m is outside 0 to height"),
        ({"hi_level": 0.5, "lo_level": 0.5}, "needs 0 <= lo_level < hi_level <= height"),
        ({"nozzles": {"Side": 1.0}}, "String should match pattern"),
    ],
)
def test_a_bad_tank_fails_at_load(overrides, message):
    with pytest.raises(ValidationError, match=message):
        Scene.model_validate(scene({**TK, **overrides}))


def test_a_tank_with_no_ports_is_valid_and_its_geometry_names_its_nozzles():
    sim = build(
        Scene.model_validate(
            scene({**{k: v for k, v in TK.items() if k != "io"}, "nozzles": {"side": 1.2}})
        )
    )
    geo = sim.component("TK_01").geometry()
    assert geo["nozzles"] == {"bottom": 0.0, "top": 2.0, "side": 1.2}
    assert (geo["shape"], geo["diameter"], geo["height"]) == ("vertical_cylinder", 1.0, 2.0)
