"""roller_conveyor: belt behavior, its own friction, and kinematics as a carrier."""

from __future__ import annotations

import pytest

from openfactory.core.iotable import Writer
from openfactory.core.kinematics import GRAVITY
from openfactory.core.loader import build
from openfactory.core.schema import CARRIER_TYPES, Scene

DT = 0.02
ROLLER = {
    "id": "RC_01",
    "type": "roller_conveyor",
    "description": "Transfer Roller Conveyor",
    "position": [0, 0, 0],
    "length": 2.0,
    "width": 0.5,
    "speed_mps": 0.5,
    "friction": 0.3,
    "io": {"run": "RC_01_RUN_DO", "speed": "RC_01_SPD_AO", "running": "RC_01_RUN_DI"},
}


def scene(*components: dict, friction: float = 0.8, connections: list | None = None) -> Scene:
    return Scene.model_validate(
        {
            "openfactory": "0.1",
            "scene": {"name": "roller", "description": "roller test", "friction": friction},
            "components": list(components),
            "connections": connections or [],
        }
    )


def ticks_to_belt_speed(comp: dict, friction: float = 0.8) -> int:
    """Ticks a box placed at rest on the running conveyor takes to reach its surface speed."""
    loaded = build(scene(comp, friction=friction))
    loaded.io.set(comp["io"]["run"], True, Writer.PLC)
    loaded.io.set(comp["io"]["speed"], 100.0, Writer.PLC)
    for _ in range(20):  # belt ramp (0.3 s) done before the part arrives
        loaded.step(DT)
    part = loaded.world.spawn("box", (0.3, 0.2, 0.3), (0.5, 0.1, 0.0))
    for k in range(1, 200):
        loaded.step(DT)
        if part.v_flow >= comp["speed_mps"] - 1e-9:
            return k
    raise AssertionError("never reached belt speed")


def test_runs_like_a_belt(rig):
    r = rig(ROLLER)
    r.plc("RC_01_RUN_DO", True)
    r.plc("RC_01_SPD_AO", 50)
    r.step(25)
    assert r.io.get("RC_01_RUN_DI") is True
    assert r["RC_01"].surface_speed == pytest.approx(0.25)
    assert r["RC_01"].state()["running"] is True
    assert r["RC_01"].geometry()["roller_pitch"] == 0.1 and r["RC_01"].geometry()["friction"] == 0.3
    assert "roller_conveyor" in CARRIER_TYPES


def test_own_friction_sets_the_ramp_and_defaults_to_the_scene():
    # v / (mu g dt): 0.5 / (0.3 * 9.81 * 0.02) = 8.5 ticks; at the scene's 0.8, 3.2 ticks
    slow = ticks_to_belt_speed(ROLLER)
    default = ticks_to_belt_speed({**ROLLER, "friction": None})
    print(f"ticks to 0.5 m/s: roller mu 0.3 -> {slow}, scene mu 0.8 -> {default}")
    assert slow == pytest.approx(0.5 / (0.3 * GRAVITY * DT), abs=1)
    assert default == pytest.approx(0.5 / (0.8 * GRAVITY * DT), abs=1)


def test_a_part_hands_off_from_a_belt_onto_rollers():
    belt = {
        "id": "CONV_01",
        "type": "belt_conveyor",
        "description": "Infeed Belt Conveyor",
        "position": [-2.0, 0, 0],
        "length": 2.0,
        "width": 0.5,
        "speed_mps": 0.5,
        "io": {"run": "CONV_01_RUN_DO"},
    }
    loaded = build(scene(belt, ROLLER, connections=[{"from": "CONV_01", "to": "RC_01"}]))
    for tag in ("CONV_01_RUN_DO", "RC_01_RUN_DO"):
        loaded.io.set(tag, True, Writer.PLC)
    loaded.io.set("RC_01_SPD_AO", 100.0, Writer.PLC)
    part = loaded.world.spawn("box", (0.3, 0.2, 0.3), (-1.0, 0.1, 0.0))
    for _ in range(150):  # 3 s at 0.5 m/s: 1.5 m, across the seam at x = 0
        loaded.step(DT)
    # 1.5 m less the start-up ramp and the slower pick-up on the 0.3 rollers at the seam
    assert part.carrier == "RC_01" and 0.3 < part.position[0] < 0.5
    assert part.v_flow == pytest.approx(0.5)
