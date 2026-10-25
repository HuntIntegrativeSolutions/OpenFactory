"""pusher: full extend/retract cycle with limit switches, mid-stroke reversal."""

from __future__ import annotations

import pytest

CYL = {
    "id": "CYL_01",
    "type": "pusher",
    "description": "Tall Box Divert Pusher",
    "position": [2.5, 0.1, -0.3],
    "stroke": 0.5,
    "extend_time_s": 0.4,  # 20 ticks at 50 Hz
    "io": {"extend": "CYL_01_EXT_DO", "extended": "CYL_01_EXT_DI", "retracted": "CYL_01_RET_DI"},
}


@pytest.fixture
def cyl(rig):
    return rig(CYL)


def limits(r):
    return r.io.get("CYL_01_EXT_DI"), r.io.get("CYL_01_RET_DI")


def test_full_cycle_limit_switches(cyl):
    pusher = cyl["CYL_01"]
    cyl.step()
    assert limits(cyl) == (False, True)  # starts retracted

    cyl.plc("CYL_01_EXT_DO", True)
    cyl.step()
    assert limits(cyl) == (False, False)  # left the retracted switch
    cyl.step(18)  # 19 ticks = 0.38 s
    assert limits(cyl) == (False, False)  # mid-stroke: both false
    assert pusher.position == pytest.approx(0.475)
    cyl.step()  # 20 ticks = 0.40 s
    assert limits(cyl) == (True, False)
    assert pusher.position == 0.5
    cyl.step(10)  # holds extended
    assert limits(cyl) == (True, False)

    cyl.plc("CYL_01_EXT_DO", False)
    cyl.step()
    assert limits(cyl) == (False, False)
    cyl.step(19)
    assert limits(cyl) == (False, True)
    assert pusher.position == 0.0


def test_reverses_mid_stroke(cyl):
    pusher = cyl["CYL_01"]
    cyl.plc("CYL_01_EXT_DO", True)
    cyl.step(10)
    assert pusher.position == pytest.approx(0.25)
    cyl.plc("CYL_01_EXT_DO", False)
    cyl.step(5)
    assert pusher.position == pytest.approx(0.125)
    assert limits(cyl) == (False, False)
    cyl.step(5)
    assert limits(cyl) == (False, True)


def test_state_and_geometry(cyl):
    cyl.plc("CYL_01_EXT_DO", True)
    cyl.step(4)
    state = cyl["CYL_01"].state()
    assert state["extending"] is True
    assert state["position_m"] == pytest.approx(0.1)
    assert cyl["CYL_01"].geometry()["stroke"] == 0.5


def test_limit_ports_are_optional(rig):
    r = rig({**CYL, "io": {"extend": "CYL_01_EXT_DO"}})
    r.plc("CYL_01_EXT_DO", True)
    r.step(25)
    assert r["CYL_01"].position == 0.5
