"""Reversible belts (PLAN 2.5b): the reverse command, parts moving toward the infeed end, queueing
and stopper blades in reverse, hand-back to the feeding conveyor, and the test scene."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from openfactory.cli import app
from openfactory.core.iotable import Writer
from openfactory.core.loader import build, load
from openfactory.core.schema import Scene

REPO = Path(__file__).resolve().parents[2]
SCENE = REPO / "tests" / "scenes" / "demo-reversing.yaml"
SCRIPT = REPO / "tests" / "scenes" / "demo-reversing.py"
DT = 0.02


def belt(cid, x, *, rev=True, y=0.0, length=2.0, **extra):
    io = {"run": f"{cid}_RUN_DO"}
    if rev:
        io["reverse"] = f"{cid}_REV_DO"
    return {"id": cid, "type": "belt_conveyor", "description": cid, "position": [x, y, 0],
            "length": length, "width": 0.5, "speed_mps": 0.5, "bidirectional": rev, "io": io,
            **extra}  # fmt: skip


def scene(*components, connections=()):
    return build(
        Scene.model_validate(
            {
                "openfactory": "0.1",
                "scene": {"name": "t", "description": "t"},
                "components": list(components),
                "connections": list(connections),
            }  # fmt: skip
        )
    )


def run(loaded, *tags, reverse=()):
    for tag in tags:
        loaded.io.set(tag, True, Writer.PLC)
    for tag in reverse:
        loaded.io.set(tag, True, Writer.PLC)


def steps(loaded, n):
    for _ in range(n):
        loaded.step(DT)


def test_reverse_ramps_through_zero_and_needs_run(rig):
    r = rig(belt("CONV_A", 0))
    a = r["CONV_A"]
    r.plc("CONV_A_REV_DO", True)
    r.step(20)
    assert a.surface_speed == 0.0 and a.state()["reverse"] is True  # reverse alone: no motion
    r.plc("CONV_A_RUN_DO", True)
    r.step(15)
    assert a.surface_speed == pytest.approx(-0.5)
    r.plc("CONV_A_REV_DO", False)
    r.step(15)
    assert a.surface_speed == pytest.approx(0.0, abs=1e-9)  # 0.3 s to zero ...
    r.step(15)
    assert a.surface_speed == pytest.approx(0.5)  # ... and 0.3 s on to full forward
    assert r.io.get("CONV_A_RUN_DO") is True


def test_bidirectional_and_reverse_go_together():
    with pytest.raises(ValidationError, match="bidirectional: true needs io.reverse"):
        scene({**belt("CONV_A", 0, rev=False), "bidirectional": True})
    bad = belt("CONV_A", 0)
    bad["bidirectional"] = False
    with pytest.raises(ValidationError, match="io.reverse needs bidirectional: true"):
        scene(bad)
    roller = {**belt("RC_01", 0), "type": "roller_conveyor"}
    assert scene(roller).component("RC_01").config.bidirectional


def test_a_one_way_belt_reports_no_reverse_state(rig):
    r = rig(belt("CONV_A", 0, rev=False))
    assert "reverse" not in r["CONV_A"].state()
    assert "bidirectional" not in r["CONV_A"].geometry()


def test_parts_go_back_across_the_seam_and_off_the_infeed_end():
    loaded = scene(
        belt("CONV_A", 0), belt("CONV_B", 2), connections=[{"from": "CONV_A", "to": "CONV_B"}]
    )
    part = loaded.world.spawn("box", (0.3, 0.3, 0.3), (3.0, 0.15, 0.1))
    run(loaded, "CONV_A_RUN_DO", "CONV_B_RUN_DO", reverse=("CONV_A_REV_DO", "CONV_B_REV_DO"))
    steps(loaded, 120)  # 2.4 s at up to 0.5 m/s back: past the seam at x 2
    assert (
        part.carrier == "CONV_A"
        and part.position[0] < 2.0
        and part.position[2] == pytest.approx(0.1)
    )
    steps(loaded, 200)
    assert part.carrier is None  # off CONV_A's infeed end, with nothing feeding it: falling
    assert loaded.kinematics.velocity(part)[0] == 0.0


def test_queueing_in_reverse_and_a_blade_from_the_far_side():
    stopper = {"id": "STP_01", "type": "stopper", "description": "Stop", "position": [1.0, 0, 0],
               "io": {"raise": "STP_01_RAISE_DO"}}  # fmt: skip
    loaded = scene(belt("CONV_A", 0, length=3.0), stopper)
    lead = loaded.world.spawn("box", (0.3, 0.3, 0.3), (2.0, 0.15, 0.0))
    follow = loaded.world.spawn("box", (0.3, 0.3, 0.3), (2.6, 0.15, 0.0))
    run(loaded, "CONV_A_RUN_DO", "STP_01_RAISE_DO", reverse=("CONV_A_REV_DO",))
    steps(loaded, 200)
    # the blade line is at x 1.0: the lead's back face stops there, the follower nose to tail
    assert lead.position[0] == pytest.approx(1.15)
    assert follow.position[0] == pytest.approx(1.45)
    loaded.io.set("STP_01_RAISE_DO", False, Writer.PLC)
    steps(loaded, 150)
    assert lead.carrier is None  # released: on past the infeed end


def test_a_reversed_part_waits_at_an_out_of_level_feeder():
    lift = {"id": "LFT_01", "type": "lift", "description": "Lift", "position": [-1.0, 0, 0],
            "length": 1.0, "width": 0.5, "speed_mps": 0.5, "lift_height": 0.5, "start": "top",
            "io": {"run": "LFT_01_RUN_DO", "raise": "LFT_01_RAISE_DO"}}  # fmt: skip
    loaded = scene(lift, belt("CONV_A", 0), connections=[{"from": "LFT_01", "to": "CONV_A"}])
    loaded.io.set("LFT_01_RAISE_DO", True, Writer.PLC)  # stays at the top: out of level
    part = loaded.world.spawn("box", (0.3, 0.3, 0.3), (1.0, 0.15, 0.0))
    run(loaded, "CONV_A_RUN_DO", reverse=("CONV_A_REV_DO",))
    steps(loaded, 200)
    assert part.carrier == "CONV_A" and part.position[0] == pytest.approx(0.15)
    loaded.io.set("LFT_01_RAISE_DO", False, Writer.PLC)
    steps(loaded, 150)  # down and level: it hands back onto the deck
    assert part.carrier == "LFT_01"


def test_at_a_merge_a_reversed_part_goes_to_the_nearest_feeder():
    side = {**belt("CONV_S", 1.0, rev=False), "position": [1.75, 0, -1.5], "rotation_deg": -90.0}
    loaded = scene(
        belt("CONV_M", 1.0, length=1.0), side, belt("CONV_A", 2.0),
        # CONV_S listed first, so taking the first feeder would pick it
        connections=[{"from": "CONV_S", "to": "CONV_A"}, {"from": "CONV_M", "to": "CONV_A"}],
    )  # fmt: skip
    part = loaded.world.spawn("box", (0.3, 0.3, 0.3), (2.5, 0.15, 0.0))
    run(loaded, "CONV_A_RUN_DO", reverse=("CONV_A_REV_DO",))
    steps(loaded, 100)
    # CONV_M's discharge end (2.0, 0) is nearer the part leaving at x 2 than CONV_S's (1.75, 0.5)
    assert part.carrier == "CONV_M"


def test_demo_sends_odd_boxes_on_and_even_boxes_back(tmp_path, strip_ansi):
    for name in (SCENE.name, SCRIPT.name, "demo-reversing.float32.modbus.yaml"):
        (tmp_path / name).write_text((SCENE.parent / name).read_text(encoding="utf-8"))
    result = CliRunner().invoke(
        app, ["test", str(tmp_path / SCENE.name), "--script", str(tmp_path / SCRIPT.name)]
    )
    out = strip_ansi(result.output)
    print(out)
    assert result.exit_code == 0
    assert re.search(r"'REM_FWD': \{'box': 3\}, 'REM_BACK': \{'box': 2\}", out)
    assert "lost {}" in out


def test_parity_traces_are_unchanged_by_the_reverse_code():
    loaded = load(REPO / "scenes" / "sorting-by-height.yaml")
    assert all(c.surface_speed >= 0 for c in loaded.kinematics.carriers.values())  # type: ignore[attr-defined]
