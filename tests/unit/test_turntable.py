"""turntable: turn and limits, the deck frame, parts turning with it, aligned handoffs only."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openfactory.cli import app
from openfactory.core.iotable import Writer
from openfactory.core.loader import load

REPO = Path(__file__).resolve().parents[2]
SCENE = REPO / "tests" / "scenes" / "demo-turntable.yaml"
SCRIPT = REPO / "tests" / "scenes" / "demo-turntable.py"
DT = 0.02
TT = {
    "id": "TT_01",
    "type": "turntable",
    "description": "Routing Turntable",
    "position": [2.4, 0, 0],
    "length": 0.8,
    "width": 0.5,
    "speed_mps": 0.5,
    "turn_deg": 90,
    "turn_time_s": 1.0,  # 50 ticks
    "io": {
        "run": "TT_01_RUN_DO",
        "turn": "TT_01_TURN_DO",
        "running": "TT_01_RUN_DI",
        "at_0": "TT_01_AT0_DI",
        "at_90": "TT_01_AT90_DI",
    },
}


def limits(r):
    return r.io.get("TT_01_AT0_DI"), r.io.get("TT_01_AT90_DI")


def test_turn_held_drives_to_turn_deg_and_released_returns_to_0(rig):
    r = rig(TT)
    r.step()
    assert limits(r) == (True, False)
    r.plc("TT_01_TURN_DO", True)
    r.step(25)
    assert limits(r) == (False, False) and r["TT_01"].angle == pytest.approx(45)
    assert r["TT_01"].turning and r["TT_01"].state()["angle_deg"] == pytest.approx(45)
    r.step(25)
    assert limits(r) == (False, True) and not r["TT_01"].turning
    r.plc("TT_01_TURN_DO", False)
    r.step(10)
    assert limits(r) == (False, False) and r["TT_01"].angle == pytest.approx(72)
    r.step(40)
    assert limits(r) == (True, False)


def test_a_clockwise_turntable_and_its_deck_frame(rig):
    r = rig({**TT, "turn_deg": -90})
    deck = r["TT_01"]
    origin, heading = deck.frame()
    assert origin == pytest.approx((2.0, 0, 0)) and heading == 0  # infeed end at angle 0
    r.plc("TT_01_TURN_DO", True)
    r.step(50)
    origin, heading = deck.frame()
    assert deck.angle == -90 and heading == -90
    assert origin == pytest.approx((2.4, 0, -0.4))  # turned clockwise: infeed now at -z
    assert r.io.get("TT_01_AT90_DI") is True


def start(loaded):
    for tag in ("CONV_IN_RUN_DO", "TT_01_RUN_DO", "CONV_OUT_RUN_DO", "CONV_SIDE_RUN_DO"):
        loaded.io.set(tag, True, Writer.PLC)


def test_a_part_on_the_deck_turns_with_it_and_follows_the_core_while_turning():
    loaded = load(SCENE)
    part = loaded.world.spawn("box", (0.3, 0.2, 0.3), (2.55, 0.1, 0.0))
    loaded.step(DT)  # lands on the deck
    assert part.carrier == "TT_01" and not loaded.kinematics.follows_core(part)
    loaded.io.set("TT_01_TURN_DO", True, Writer.PLC)  # deck belt stopped: the part only turns
    for _ in range(26):
        loaded.step(DT)
    assert loaded.kinematics.follows_core(part)  # mid-turn: the viewer places it
    for _ in range(30):
        loaded.step(DT)
    # 0.15 m past the centre along +x, turned 90 counter-clockwise: 0.15 m toward -z
    assert part.position[0] == pytest.approx(2.4, abs=1e-9)
    assert part.position[2] == pytest.approx(-0.15, abs=1e-9)
    assert part.carrier == "TT_01" and not loaded.kinematics.follows_core(part)


def test_handoff_only_while_aligned_and_parts_wait_at_an_unaligned_end():
    loaded = load(SCENE)
    start(loaded)
    loaded.io.set("TT_01_TURN_DO", True, Writer.PLC)
    waiting = loaded.world.spawn("box", (0.3, 0.2, 0.3), (1.0, 0.1, 0.0))
    for _ in range(150):  # deck turned: CONV_IN's only connection is out of line
        loaded.step(DT)
    assert waiting.carrier == "CONV_IN" and waiting.position[0] == pytest.approx(1.85)
    assert loaded.kinematics.downstream.get("TT_01") == "CONV_SIDE"
    loaded.io.set("TT_01_TURN_DO", False, Writer.PLC)
    for _ in range(170):  # 1 s to turn back, then it crosses the deck and goes straight on
        loaded.step(DT)
    assert loaded.kinematics.downstream.get("TT_01") == "CONV_OUT"
    assert waiting.carrier == "CONV_OUT" and waiting.position[2] == pytest.approx(0.0)


def test_demo_routes_every_other_box_to_the_side_under_the_script(tmp_path, strip_ansi):
    for name in (SCENE.name, SCRIPT.name):
        (tmp_path / name).write_text((SCENE.parent / name).read_text(encoding="utf-8"))
    result = CliRunner().invoke(
        app,
        [
            "run", str(tmp_path / SCENE.name), "--as-fast-as-possible", "--duration", "60",
            "--port", "0", "--headless", "--script", str(tmp_path / SCRIPT.name),
        ],
    )  # fmt: skip
    out = strip_ansi(result.output)
    assert result.exit_code == 0, out
    summary = next(line for line in out.splitlines() if "ran 3000 ticks" in line)
    print(summary)
    assert "lost {}" in summary
    assert re.search(r"'REM_OUT': \{'box': 5\}, 'REM_SIDE': \{'box': 5\}", summary)
