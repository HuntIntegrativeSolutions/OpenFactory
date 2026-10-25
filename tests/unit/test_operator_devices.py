"""pushbutton, pilot_light, selector_switch: operator panel devices."""

from __future__ import annotations

import pytest

PB = {
    "id": "PB_01",
    "type": "pushbutton",
    "description": "Line Start Pushbutton",
    "position": [0, 1, 0],
    "io": {"pressed": "PB_01_DI"},
}
PL = {
    "id": "PL_01",
    "type": "pilot_light",
    "description": "Line Running Pilot Light",
    "position": [0, 1.2, 0],
    "color": "green",
    "io": {"on": "PL_01_DO"},
}
SS = {
    "id": "SS_01",
    "type": "selector_switch",
    "description": "Line Hand-Off-Auto Selector",
    "position": [0.2, 1, 0],
    "positions": 3,
    "initial": "center",
    "io": {"pos_a": "SS_01_HAND_DI", "pos_b": "SS_01_AUTO_DI"},
}


# --- pushbutton --------------------------------------------------------------------------------


def test_momentary_press_is_true_for_300_ms(rig):
    r = rig(PB)
    r.step()
    assert r.io.get("PB_01_DI") is False
    r["PB_01"].press()
    assert r.io.get("PB_01_DI") is False  # applied on the next step, not from the viewer thread
    r.step()
    assert r.io.get("PB_01_DI") is True
    r.step(14)  # 15 ticks = 300 ms true
    assert r.io.get("PB_01_DI") is True
    r.step()
    assert r.io.get("PB_01_DI") is False


def test_momentary_repress_restarts_pulse(rig):
    r = rig(PB)
    r["PB_01"].press()
    r.step(10)
    r["PB_01"].press()
    r.step(15)  # the re-press step plus 14 more: a fresh 300 ms pulse
    assert r.io.get("PB_01_DI") is True
    r.step()
    assert r.io.get("PB_01_DI") is False


def test_maintained_toggles(rig):
    r = rig({**PB, "mode": "maintained"})
    r["PB_01"].press()
    r.step(50)
    assert r.io.get("PB_01_DI") is True
    r["PB_01"].press()
    r.step()
    assert r.io.get("PB_01_DI") is False
    r["PB_01"].press()
    r["PB_01"].press()  # two presses between steps cancel
    r.step()
    assert r.io.get("PB_01_DI") is False
    assert r["PB_01"].state() == {"pressed": False}
    assert r["PB_01"].geometry()["mode"] == "maintained"


# --- pilot light -------------------------------------------------------------------------------


def test_pilot_light_follows_command(rig):
    r = rig(PL)
    light = r["PL_01"]
    r.step()
    assert light.state() == {"lit": False}
    r.plc("PL_01_DO", True)
    r.step()
    assert light.state() == {"lit": True}
    assert light.outputs == {}
    assert light.geometry()["color"] == "green"


# --- selector switch ---------------------------------------------------------------------------


def outputs(r):
    return r.io.get("SS_01_HAND_DI"), r.io.get("SS_01_AUTO_DI")


def test_three_position_switch(rig):
    r = rig(SS)
    r.step()
    assert outputs(r) == (False, False)  # center (OFF)
    r["SS_01"].set_position("a")
    r.step()
    assert outputs(r) == (True, False)
    r["SS_01"].set_position("b")
    r.step()
    assert outputs(r) == (False, True)
    assert r["SS_01"].state() == {"position": "b"}


def test_two_position_switch_initial_written_on_first_step(rig):
    r = rig({**SS, "positions": 2, "initial": "a"})
    assert outputs(r) == (False, False)  # before the first step
    r.step()
    assert outputs(r) == (True, False)
    with pytest.raises(ValueError, match="center"):
        r["SS_01"].set_position("center")


def test_newest_request_wins_between_steps(rig):
    r = rig(SS)
    r["SS_01"].set_position("a")
    r["SS_01"].set_position("b")
    r.step()
    assert outputs(r) == (False, True)
    assert r["SS_01"].geometry()["positions"] == 3
