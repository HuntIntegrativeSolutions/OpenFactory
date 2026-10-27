"""pick_and_place: axis timing and limits, gripping only when lowered over a part, carrying,
release onto the belt below, the viewer's follow flag, and the demo under its script."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from openfactory.cli import app
from openfactory.core.iotable import Writer
from openfactory.core.loader import load

REPO = Path(__file__).resolve().parents[2]
SCENE = REPO / "tests" / "scenes" / "demo-pick-place.yaml"
SCRIPT = REPO / "tests" / "scenes" / "demo-pick-place.py"
DT = 0.02
PP = {
    "id": "PP_01",
    "type": "pick_and_place",
    "description": "Transfer Gantry",
    "position": [0, 0.45, 0],
    "travel": 1.0,
    "stroke": 0.25,
    "travel_time_s": 1.0,  # 50 ticks
    "lower_time_s": 0.3,  # 15 ticks
    "io": {
        "traverse": "PP_01_TRAV_DO",
        "lower": "PP_01_LOWER_DO",
        "grip": "PP_01_GRIP_DO",
        "at_pick": "PP_01_AT_PICK_DI",
        "at_place": "PP_01_AT_PLACE_DI",
        "lowered": "PP_01_DOWN_DI",
        "holding": "PP_01_HOLD_DI",
    },
}


def dis(r) -> tuple[bool, bool, bool, bool]:
    return tuple(r.io.get(f"PP_01_{n}_DI") for n in ("AT_PICK", "AT_PLACE", "DOWN", "HOLD"))  # type: ignore[return-value]


def test_axes_take_their_times_and_limits_hold_only_at_the_ends(rig):
    r = rig(PP)
    r.step()
    assert dis(r) == (True, False, False, False)
    r.plc("PP_01_TRAV_DO", True)
    r.plc("PP_01_LOWER_DO", True)
    r.step(14)
    assert dis(r) == (False, False, False, False)
    r.step()
    assert dis(r) == (False, False, True, False) and r["PP_01"].y == pytest.approx(0.25)
    r.step(35)
    assert dis(r) == (False, True, True, False) and r["PP_01"].pad == pytest.approx((1.0, 0.2, 0))


def test_grip_needs_the_pad_lowered_onto_a_part(rig):
    r = rig(PP)
    box = r.world.spawn("box", (0.3, 0.2, 0.3), (0.05, 0.1, 0.0))  # top at 0.20
    r.plc("PP_01_GRIP_DO", True)
    r.step(5)
    assert r["PP_01"].held is None  # raised: nothing within reach
    r.plc("PP_01_LOWER_DO", True)
    r.step(15)
    assert r["PP_01"].held == box.id and box.held_by == "PP_01" and box.carrier is None
    assert r.io.get("PP_01_HOLD_DI") is True
    r.plc("PP_01_LOWER_DO", False)
    r.plc("PP_01_TRAV_DO", True)
    r.step(60)
    assert box.position == pytest.approx((1.05, 0.35, 0.0))  # kept its 5 cm offset from the pad


def test_release_drops_the_part_onto_the_belt_and_the_viewer_follows_it_while_held():
    loaded = load(SCENE)
    for tag in ("CONV_A_RUN_DO", "CONV_B_RUN_DO", "STP_A_RAISE_DO"):
        loaded.io.set(tag, True, Writer.PLC)
    box = loaded.world.spawn("box", (0.3, 0.2, 0.3), (2.05, 0.1, 0.0))
    loaded.io.set("PP_01_LOWER_DO", True, Writer.PLC)
    loaded.io.set("PP_01_GRIP_DO", True, Writer.PLC)
    for _ in range(16):
        loaded.step(DT)
    assert box.held_by == "PP_01" and loaded.kinematics.follows_core(box)
    loaded.io.set("PP_01_LOWER_DO", False, Writer.PLC)
    loaded.io.set("PP_01_TRAV_DO", True, Writer.PLC)
    for _ in range(55):
        loaded.step(DT)
    loaded.io.set("PP_01_GRIP_DO", False, Writer.PLC)  # released 0.25 m above CONV_B
    for _ in range(20):
        loaded.step(DT)
    assert box.held_by is None and not loaded.kinematics.follows_core(box)
    assert box.carrier == "CONV_B" and box.position[1] == pytest.approx(0.1)


def test_demo_transfers_every_box_under_the_script(tmp_path, strip_ansi):
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
    assert "spawned 10" in summary and "lost {}" in summary
    assert "by remover {'REM_B': {'box': 9}}" in summary
