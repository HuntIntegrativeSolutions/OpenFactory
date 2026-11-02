"""lift (PLAN 2.5b): raise and limits, the deck frame, parts moving with the deck, level-only
handoffs, and the test scene under its stand-in script."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from openfactory.cli import app
from openfactory.core.iotable import Writer
from openfactory.core.loader import load
from openfactory.core.schema import Scene

REPO = Path(__file__).resolve().parents[2]
SCENE = REPO / "tests" / "scenes" / "demo-lift.yaml"
SCRIPT = REPO / "tests" / "scenes" / "demo-lift.py"
DT = 0.02
LFT = {
    "id": "LFT_01",
    "type": "lift",
    "description": "Box Lift",
    "position": [2.0, 0, 0],
    "length": 1.0,
    "width": 0.5,
    "speed_mps": 0.5,
    "lift_height": 0.6,
    "lift_time_s": 1.0,  # 50 ticks
    "io": {
        "run": "LFT_01_RUN_DO",
        "raise": "LFT_01_RAISE_DO",
        "running": "LFT_01_RUN_DI",
        "at_top": "LFT_01_TOP_DI",
        "at_bottom": "LFT_01_BOT_DI",
    },
}


def limits(r):
    return r.io.get("LFT_01_TOP_DI"), r.io.get("LFT_01_BOT_DI")


def test_raise_held_drives_to_the_top_and_released_returns(rig):
    r = rig(LFT)
    lift = r["LFT_01"]
    r.step()
    assert limits(r) == (False, True)
    r.plc("LFT_01_RAISE_DO", True)
    r.step(25)
    assert limits(r) == (False, False) and lift.height == pytest.approx(0.3)
    assert lift.moving and lift.state()["height_m"] == pytest.approx(0.3)
    assert lift.frame() == ((2.0, pytest.approx(0.3), 0), 0.0)
    r.step(25)
    assert limits(r) == (True, False) and not lift.moving
    r.plc("LFT_01_RAISE_DO", False)
    r.step(10)
    assert lift.height == pytest.approx(0.48)
    r.step(40)
    assert limits(r) == (False, True)
    r.plc("LFT_01_RUN_DO", True)
    r.step()
    assert r.io.get("LFT_01_RUN_DI") is True and lift.state()["running"] is True


def test_start_top_slow_and_jammed(rig):
    r = rig({**LFT, "start": "top"})
    lift = r["LFT_01"]
    r.step()
    assert limits(r) == (False, False) and lift.height == pytest.approx(0.588)  # going down
    lift.fault_inject("jammed")
    r.step(20)
    assert lift.height == pytest.approx(0.588)
    lift.fault_inject("slow")
    r.step(10)
    assert lift.height == pytest.approx(0.588 - 10 * 0.006)  # half of 0.012 m a tick


def test_lift_may_feed_several_conveyors(rig):
    belt = {"type": "belt_conveyor", "length": 1.0, "width": 0.5, "speed_mps": 0.5}
    scene = {
        "openfactory": "0.1",
        "scene": {"name": "t", "description": "t"},
        "components": [
            LFT,
            {**belt, "id": "LOW", "description": "Low", "position": [3, 0, 0],
             "io": {"run": "LOW_RUN_DO"}},
            {**belt, "id": "HIGH", "description": "High", "position": [3, 0.6, 0],
             "io": {"run": "HIGH_RUN_DO"}},
        ],
        "connections": [{"from": "LFT_01", "to": "LOW"}, {"from": "LFT_01", "to": "HIGH"}],
    }  # fmt: skip
    Scene.model_validate(scene)
    scene["connections"].append({"from": "LOW", "to": "HIGH"})
    scene["connections"].append({"from": "LOW", "to": "LFT_01"})
    with pytest.raises(ValidationError, match="feeds both 'HIGH' and 'LFT_01'"):
        Scene.model_validate(scene)


def start(loaded):
    for tag in ("CONV_IN_RUN_DO", "CONV_OUT_RUN_DO", "LFT_01_RUN_DO"):
        loaded.io.set(tag, True, Writer.PLC)


def test_a_part_moves_with_the_deck_and_follows_the_core_while_it_moves():
    loaded = load(SCENE)
    part = loaded.world.spawn("box", (0.3, 0.3, 0.3), (2.4, 0.15, 0.0))
    loaded.step(DT)  # at rest on the deck: lands in its first step
    assert part.carrier == "LFT_01" and not loaded.kinematics.follows_core(part)
    loaded.io.set("LFT_01_RAISE_DO", True, Writer.PLC)  # deck belt stopped: the part only rises
    for _ in range(40):
        loaded.step(DT)
    assert loaded.kinematics.follows_core(part)  # mid-rise: the viewer places it
    # parts move before components step, so the part trails the deck by one tick (as on a deck
    # that turns): after 40 ticks the deck has risen 40 x 8 mm, the part 39 x 8 mm
    assert part.position[1] == pytest.approx(0.15 + 0.6 * 39 / 75, abs=1e-6)
    for _ in range(40):
        loaded.step(DT)
    assert part.position == pytest.approx((2.4, 0.75, 0.0))
    assert part.carrier == "LFT_01" and not loaded.kinematics.follows_core(part)


def test_handoff_only_while_level_and_parts_wait_at_an_unlevel_end():
    loaded = load(SCENE)
    start(loaded)
    loaded.io.set("LFT_01_RAISE_DO", True, Writer.PLC)
    waiting = loaded.world.spawn("box", (0.3, 0.3, 0.3), (1.0, 0.155, 0.0))
    for _ in range(150):  # deck up: CONV_IN's only connection is out of level
        loaded.step(DT)
    assert waiting.carrier == "CONV_IN" and waiting.position[0] == pytest.approx(1.85)
    assert loaded.kinematics.downstream.get("LFT_01") == "CONV_OUT"
    assert "CONV_IN" not in loaded.kinematics.downstream
    loaded.io.set("LFT_01_RAISE_DO", False, Writer.PLC)
    for _ in range(100):  # 1.5 s down, then it crosses onto the deck
        loaded.step(DT)
    assert loaded.kinematics.downstream.get("CONV_IN") == "LFT_01"
    assert waiting.carrier == "LFT_01"
    # at the bottom the deck's discharge end is out of level with CONV_OUT: the part waits
    for _ in range(150):
        loaded.step(DT)
    assert waiting.carrier == "LFT_01" and waiting.position[0] == pytest.approx(2.85)


def test_demo_lifts_every_box_to_the_upper_belt(tmp_path):
    for name in (SCENE.name, SCRIPT.name, "demo-lift.float32.modbus.yaml"):
        (tmp_path / name).write_text((SCENE.parent / name).read_text(encoding="utf-8"))
    result = CliRunner().invoke(
        app, ["test", str(tmp_path / SCENE.name), "--script", str(tmp_path / SCRIPT.name)]
    )
    print(result.output)
    assert result.exit_code == 0
    assert "PASS: 3 of 3 checks passed" in result.output
    assert "box: 6 reached REM_01, none anywhere else" in result.output
