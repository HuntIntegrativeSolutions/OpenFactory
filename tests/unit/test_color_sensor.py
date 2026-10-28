"""color_sensor: the nearest part's color against the taught one, belts stopping the beam, plain
parts never matching, part colors from the emitter spec in state frames, and faults."""

from __future__ import annotations

from collections import Counter

import pytest

from openfactory.core.iotable import Writer
from openfactory.core.loader import build
from openfactory.core.schema import Scene
from openfactory.server.runtime import Runtime

CS = {
    "id": "CS_01",
    "type": "color_sensor",
    "description": "Box Color Sensor",
    "position": [1.0, 0.5, 0.0],  # 0.5 m above the belt top, looking down
    "teach": "red",
    "io": {"match": "CS_01_RED_DI", "detect": "CS_01_PART_DI"},
}
BELT = {
    "id": "CONV_01",
    "type": "belt_conveyor",
    "description": "Belt",
    "position": [0, 0, 0],
    "length": 3.0,
    "width": 0.5,
    "speed_mps": 0.5,
    "io": {"run": "CONV_01_RUN_DO"},
}


def scene(*components: dict) -> Scene:
    return Scene.model_validate(
        {
            "openfactory": "0.1",
            "scene": {"name": "color", "description": "color sensor test"},
            "plc": {"protocol": "modbus_tcp"},
            "components": list(components),
        }
    )


def outputs(loaded) -> tuple[bool, bool]:
    loaded.component("CS_01").step(0.02)
    return loaded.io.get("CS_01_PART_DI"), loaded.io.get("CS_01_RED_DI")


@pytest.mark.parametrize(
    ("color", "expected"),
    [("red", (True, True)), ("blue", (True, False)), (None, (True, False))],
)
def test_it_matches_only_the_taught_color(color, expected):
    loaded = build(scene(BELT, CS))
    assert outputs(loaded) == (False, False)  # the belt stops the beam: nothing in range
    loaded.world.spawn("box", (0.3, 0.2, 0.3), (1.0, 0.1, 0.0), color=color)
    assert outputs(loaded) == expected
    seen = loaded.component("CS_01").state()["seen"]
    assert seen == (color or "plain")


def test_the_nearest_part_decides_and_the_range_is_beam_length():
    side = {**CS, "axis": "+z", "position": [1.0, 0.1, -0.5], "beam_length": 0.4}
    loaded = build(scene(BELT, side))
    loaded.world.spawn("far", (0.3, 0.2, 0.3), (1.0, 0.1, 0.0), color="red")  # face at z -0.15
    assert outputs(loaded) == (True, True)  # 0.35 m away, inside 0.4
    loaded.world.spawn("near", (0.1, 0.2, 0.1), (1.0, 0.1, -0.3), color="green")
    assert outputs(loaded) == (True, False)  # the nearer green part hides the red one
    short = {**side, "beam_length": 0.3}
    loaded = build(scene(BELT, short))
    loaded.world.spawn("far", (0.3, 0.2, 0.3), (1.0, 0.1, 0.0), color="red")
    assert outputs(loaded) == (False, False)  # out of range


def test_emitter_colors_reach_parts_and_state_frames():
    emitter = {
        "id": "EMT_01",
        "type": "box_emitter",
        "description": "Source",
        "position": [0.3, 0.105, 0],
        "interval_s": 1.0,
        "parts": [
            {"name": "red_box", "size": [0.3, 0.2, 0.3], "weight": 1, "color": "red"},
            {"name": "blue_box", "size": [0.3, 0.2, 0.3], "weight": 1, "color": "blue"},
        ],
    }
    loaded = build(scene(BELT, emitter, CS))
    loaded.io.set("CONV_01_RUN_DO", True, Writer.PLC)
    seen = Counter()
    for _ in range(400):
        loaded.step(0.02)
        under = [  # the part the downward beam at x 1.0 passes through, if any
            p for p in loaded.world.parts.values() if abs(p.position[0] - 1.0) <= p.size[0] / 2
        ]
        color = under[0].color if under else None
        seen[(color, loaded.io.get("CS_01_RED_DI"), loaded.io.get("CS_01_PART_DI"))] += 1
    frames = Counter(p["color"] for p in Runtime(loaded).snapshot["parts"])
    print("under the sensor (color, match, detect) -> ticks:", dict(seen), "frames:", dict(frames))
    assert set(seen) == {(None, False, False), ("red", True, True), ("blue", False, True)}
    assert set(frames) == {"red", "blue"} and sum(frames.values()) == len(loaded.world.parts)


@pytest.mark.parametrize(
    ("kind", "expected"), [("stuck_on", (True, True)), ("stuck_off", (False, False))]
)
def test_stuck_faults_act_on_both_outputs(kind, expected):
    loaded = build(scene(BELT, CS))
    loaded.world.spawn("box", (0.3, 0.2, 0.3), (2.5, 0.1, 0.0), color="blue")
    loaded.component("CS_01").fault_inject(kind)
    if kind == "stuck_off":
        loaded.world.parts["P000001"].position = (1.0, 0.1, 0.0)
        loaded.world.parts["P000001"].color = "red"
    assert outputs(loaded) == expected
