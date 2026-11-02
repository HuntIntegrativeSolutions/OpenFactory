"""PLAN 2.5b: debounce_ms on photo_eye (default 20, as before) and color_sensor (default 0, as
before), through the shared core.debounce.Debounce."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from openfactory.core.debounce import Debounce
from openfactory.core.schema import Scene

DT = 0.02
PE = {
    "id": "PE_01",
    "type": "photo_eye",
    "description": "Eye",
    "position": [1.0, 0.1, 0.0],
    "beam_length": 0.6,
    "io": {"detect": "PE_01_DI"},
}
CS = {
    "id": "CS_01",
    "type": "color_sensor",
    "description": "Color",
    "position": [1.0, 0.5, 0.0],
    "teach": "red",
    "io": {"match": "CS_01_RED_DI", "detect": "CS_01_PART_DI"},
}


def test_debounce_both_edges_and_short_pulses():
    d = Debounce(0.06)
    raw = [True, True, True, True, False, True, True, True, True, False, False, False, False]
    seen = [d.update(r, DT) for r in raw]
    # on after 60 ms of true (the 4th step), a 1-step false does not show, off after 60 ms
    assert seen == [
        False,
        False,
        False,
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        False,
    ]
    assert [Debounce(0.0).update(r, DT) for r in (True, False)] == [True, False]


def ticks_to_detect(rig, component, tag, place):
    r = rig(component)
    place(r)
    for k in range(1, 20):
        r.step()
        if r.io.get(tag):
            return k
    raise AssertionError("never detected")


def put_box(color=None):
    def place(r):
        r.world.spawn("box", (0.3, 0.3, 0.3), (1.0, 0.15, 0.0), color=color)

    return place


@pytest.mark.parametrize("debounce, ticks", [(None, 2), (0, 1), (20, 2), (60, 4)])
def test_photo_eye_debounce_ms(rig, debounce, ticks):
    eye = dict(PE) if debounce is None else {**PE, "debounce_ms": debounce}
    # the step that first sees the box is tick 1; 20 ms more is one more tick (the old default)
    assert ticks_to_detect(rig, eye, "PE_01_DI", put_box()) == ticks


@pytest.mark.parametrize("debounce, ticks", [(None, 1), (0, 1), (40, 3)])
def test_color_sensor_debounce_ms(rig, debounce, ticks):
    cs = dict(CS) if debounce is None else {**CS, "debounce_ms": debounce}
    assert ticks_to_detect(rig, cs, "CS_01_RED_DI", put_box("red")) == ticks
    assert ticks_to_detect(rig, cs, "CS_01_PART_DI", put_box("blue")) == ticks


def test_a_color_flicker_shorter_than_the_debounce_never_reaches_the_plc(rig):
    r = rig({**CS, "debounce_ms": 40})
    box = r.world.spawn("box", (0.3, 0.3, 0.3), (1.0, 0.15, 0.0), color="red")
    r.step(3)
    assert r.io.get("CS_01_PART_DI") and r.io.get("CS_01_RED_DI")
    box.color = "blue"  # one tick of another color: match flickers off in the raw state only
    r.step()
    box.color = "red"
    r.step(3)
    assert r.io.get("CS_01_RED_DI") is True and r.io.get("CS_01_PART_DI") is True
    assert r["CS_01"].state()["match"] is True


@pytest.mark.parametrize("component", [PE, CS])
def test_debounce_ms_range(component):
    for bad in (-1, 1001):
        with pytest.raises(ValidationError, match="debounce_ms"):
            Scene.model_validate(
                {
                    "openfactory": "0.1",
                    "scene": {"name": "t", "description": "t"},
                    "components": [{**component, "debounce_ms": bad}],
                }  # fmt: skip
            )
