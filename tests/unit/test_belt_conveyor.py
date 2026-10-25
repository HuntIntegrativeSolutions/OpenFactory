"""belt_conveyor: run/speed command, 0.3 s ramp, running feedback."""

from __future__ import annotations

import pytest

CONV = {
    "id": "CONV_01",
    "type": "belt_conveyor",
    "description": "Infeed Belt Conveyor",
    "position": [0, 0, 0],
    "length": 4.0,
    "width": 0.5,
    "speed_mps": 0.6,
    "io": {"run": "CONV_01_RUN_DO", "speed": "CONV_01_SPD_AO", "running": "CONV_01_RUN_DI"},
}


@pytest.fixture
def conv(rig):
    return rig(CONV)


def test_stopped_until_run(conv):
    conv.plc("CONV_01_SPD_AO", 100)
    conv.step(10)
    assert conv["CONV_01"].surface_speed == 0.0
    assert conv.io.get("CONV_01_RUN_DI") is False


def test_running_feedback_follows_run_in_same_tick(conv):
    conv.plc("CONV_01_RUN_DO", True)
    conv.step()
    assert conv.io.get("CONV_01_RUN_DI") is True
    conv.plc("CONV_01_RUN_DO", False)
    conv.step()
    assert conv.io.get("CONV_01_RUN_DI") is False


def test_ramp_up_takes_0_3_s_at_full_speed(conv):
    conv.plc("CONV_01_SPD_AO", 100)
    conv.plc("CONV_01_RUN_DO", True)
    belt = conv["CONV_01"]
    conv.step(1)
    assert belt.surface_speed == pytest.approx(0.6 * 0.02 / 0.3)
    conv.step(13)  # 14 ticks = 0.28 s, still ramping
    assert belt.surface_speed < 0.6
    conv.step(1)  # 15 ticks = 0.30 s
    assert belt.surface_speed == pytest.approx(0.6)
    conv.step(10)
    assert belt.surface_speed == pytest.approx(0.6)


def test_speed_percent_scales_and_ramps_down_when_stopped(conv):
    conv.plc("CONV_01_SPD_AO", 50)
    conv.plc("CONV_01_RUN_DO", True)
    belt = conv["CONV_01"]
    conv.step(20)
    assert belt.surface_speed == pytest.approx(0.3)
    conv.plc("CONV_01_RUN_DO", False)
    conv.step(1)
    assert 0 < belt.surface_speed < 0.3  # coasting
    conv.step(10)
    assert belt.surface_speed == 0.0
    assert belt.state()["running"] is False


def test_speed_command_is_clamped_to_range(conv):
    conv.plc("CONV_01_SPD_AO", 250)
    conv.plc("CONV_01_RUN_DO", True)
    conv.step(30)
    assert conv["CONV_01"].surface_speed == pytest.approx(0.6)
    conv.plc("CONV_01_SPD_AO", -20)
    conv.step(30)
    assert conv["CONV_01"].surface_speed == 0.0


def test_unbound_speed_runs_at_100_percent(rig):
    no_speed = {**CONV, "io": {"run": "CONV_01_RUN_DO"}}
    r = rig(no_speed)
    r.plc("CONV_01_RUN_DO", True)
    r.step(20)
    assert r["CONV_01"].surface_speed == pytest.approx(0.6)


def test_travel_state_and_geometry(conv):
    conv.plc("CONV_01_SPD_AO", 100)
    conv.plc("CONV_01_RUN_DO", True)
    conv.step(65)  # 0.3 s ramp then 1.0 s at 0.6 m/s
    belt = conv["CONV_01"]
    assert belt.travel == pytest.approx(0.6 * 1.0 + 0.6 * 0.3 / 2 + 0.6 * 0.02 / 2, rel=0.02)
    assert belt.state()["speed_mps"] == pytest.approx(0.6)
    assert belt.geometry()["length"] == 4.0 and belt.geometry()["width"] == 0.5
