"""PLAN 2.5b: parts turn with a turntable deck. The core keeps a yaw, collision and sensing use the
box rounded to 90 degrees, and the state frame writes yaw_deg (relative to the deck carrying the
part, folded by the box's symmetry) only when non-zero, so square boxes never write it."""

from __future__ import annotations

from pathlib import Path

import pytest

from openfactory.core.loader import load
from openfactory.core.world import Part, reduced_yaw
from openfactory.pyplc import ScriptPlc
from openfactory.server.runtime import Runtime

REPO = Path(__file__).resolve().parents[2]
LONG = REPO / "tests" / "scenes" / "turntable-long.yaml"
SQUARE = REPO / "tests" / "scenes" / "demo-turntable.yaml"
SCRIPT = REPO / "tests" / "scenes" / "demo-turntable.py"
DT = 0.02


@pytest.mark.parametrize(
    "yaw, size, folded",
    [
        (0.0, (0.5, 0.2, 0.25), 0.0),
        (90.0, (0.5, 0.2, 0.25), 90.0),
        (180.0, (0.5, 0.2, 0.25), 0.0),
        (-90.0, (0.5, 0.2, 0.25), 90.0),
        (270.0, (0.5, 0.2, 0.25), 90.0),
        (30.0, (0.5, 0.2, 0.25), 30.0),
        (100.0, (0.5, 0.2, 0.25), -80.0),
        (90.0, (0.3, 0.2, 0.3), 0.0),  # a square footprint repeats every 90 degrees
        (30.0, (0.3, 0.2, 0.3), 30.0),
        (-90.0 + 1e-12, (0.3, 0.2, 0.3), 0.0),
    ],
)
def test_reduced_yaw(yaw, size, folded):
    assert reduced_yaw(yaw, size) == pytest.approx(folded)


def test_footprint_swaps_length_and_width_past_45_degrees():
    part = Part("P1", "box", (0.5, 0.2, 0.25), (0.0, 0.1, 0.0))
    assert part.footprint == (0.5, 0.2, 0.25)
    part.yaw = 44.0
    assert part.footprint == (0.5, 0.2, 0.25)
    part.yaw = 46.0
    assert part.footprint == (0.25, 0.2, 0.5)
    assert part.aabb() == ((-0.125, 0.0, -0.25), (0.125, 0.2, 0.25))
    part.yaw = -90.0
    assert part.footprint == (0.25, 0.2, 0.5)


def frames(scene: Path, seconds: float):
    loaded = load(scene)
    plc = ScriptPlc(SCRIPT, loaded.io)
    runtime = Runtime.__new__(Runtime)  # only _part_state, which reads .loaded
    runtime.loaded = loaded
    for _ in range(round(seconds / DT)):
        plc.scan(loaded.time)
        loaded.step(DT)
        yield loaded, [runtime._part_state(p) for p in loaded.world.parts.values()]


def test_a_long_box_turns_with_the_deck_and_keeps_its_turn_after():
    deck_turning, on_side = [], []
    for loaded, states in frames(LONG, 14.0):
        deck = loaded.component("TT_01")
        for state in states:
            part = loaded.world.parts[state["id"]]
            if part.carrier == "TT_01" and deck.turning:
                deck_turning.append((part.yaw, state.get("yaw_deg")))
            if part.carrier == "CONV_SIDE":
                on_side.append((part.yaw, part.footprint, state.get("yaw_deg")))
    assert deck_turning and all(yaw_deg is None for _, yaw_deg in deck_turning)  # turns with it
    assert 0 < deck_turning[len(deck_turning) // 2][0] < 90  # mid-turn, in the core
    yaw, footprint, yaw_deg = on_side[-1]
    assert yaw == pytest.approx(90.0) and yaw_deg == pytest.approx(90.0)
    assert footprint == (0.25, 0.2, 0.5)  # its 0.5 m length now lies along CONV_SIDE's flow (-z)


def test_square_boxes_never_write_a_yaw():
    assert all("yaw_deg" not in state for _, states in frames(SQUARE, 25.0) for state in states)


def test_queueing_uses_the_turned_box():
    loaded = load(LONG)
    side = loaded.component("CONV_SIDE")
    part = loaded.world.spawn("box", (0.5, 0.2, 0.25), (2.4, 0.1, -1.0))
    part.yaw = 90.0
    loaded.step(DT)
    assert part.carrier == "CONV_SIDE"
    # along CONV_SIDE's flow (local x, world -z) the turned box is 0.5 m long, across it 0.25 m
    assert loaded.kinematics._extent(part, side) == pytest.approx((0.25, 0.125))
