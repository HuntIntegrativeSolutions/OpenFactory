"""PLAN 2.5b: counter_display, a panel readout of an AO on seven-segment digits."""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from openfactory.components.counter_display import display_text
from openfactory.core.schema import Scene, instance_ports
from openfactory.protocols.modbus import decode, encode

CD = {
    "id": "CNT_01",
    "type": "counter_display",
    "description": "Boxes Sorted Counter",
    "position": [0, 1.2, 0],
    "io": {"value": "CNT_01_VAL_AO"},
}


@pytest.mark.parametrize(
    "value, digits, decimals, shown",
    [
        (0, 4, 0, "   0"),
        (42, 4, 0, "  42"),
        (9999, 4, 0, "9999"),
        (10000, 4, 0, "----"),
        (41.6, 4, 0, "  42"),
        (-12, 4, 0, " -12"),
        (-999, 4, 0, "-999"),
        (-1000, 4, 0, "----"),
        (4.25, 4, 1, "  4.2"),  # round half to even, as Python formats
        (999.9, 4, 1, "999.9"),
        (1000.0, 4, 1, "---.-"),
        (-0.04, 3, 1, " 0.0"),
        (math.nan, 4, 0, "----"),
        (math.inf, 2, 0, "--"),
        (123456, 6, 0, "123456"),
    ],
)
def test_display_text(value, digits, decimals, shown):
    assert display_text(value, digits, decimals) == shown


def test_shows_the_value_and_goes_blank_when_dead(rig):
    r = rig(CD)
    display = r["CNT_01"]
    r.step()
    assert display.state() == {"text": "   0", "value": 0.0}
    r.plc("CNT_01_VAL_AO", 17.0)
    r.step()
    assert display.state()["text"] == "  17"
    display.fault_inject("stuck_off")
    r.step()
    assert display.state()["text"] == "    "
    display.clear_fault()
    r.step()
    assert display.state()["text"] == "  17"
    assert display.outputs == {}
    with pytest.raises(ValueError, match="supports: stuck_off|stuck_off"):
        display.fault_inject("jammed")


def _model(**extra):
    scene = Scene.model_validate(
        {
            "openfactory": "0.1",
            "scene": {"name": "t", "description": "t"},
            "components": [{**CD, **extra}],
        }  # fmt: skip
    )
    return scene.components[0]


def test_range_defaults_to_what_the_digits_show_and_scales_int16():
    assert _model().range == (0.0, 9999.0)
    assert _model(digits=3, decimals=1).range == (0.0, 99.9)
    assert instance_ports(_model())["value"].range == (0.0, 9999.0)
    assert instance_ports(_model(range=[-50, 50]))["value"].range == (-50.0, 50.0)
    tag = _model()  # every count 0..9999 survives int16_scaled and shows as itself
    from openfactory.core.loader import build_iotable

    io = build_iotable(Scene.model_validate(
        {"openfactory": "0.1", "scene": {"name": "t", "description": "t"}, "components": [CD]}
    ))  # fmt: skip
    t = io.tag("CNT_01_VAL_AO")
    assert t.range == (0.0, 9999.0) and tag.digits == 4
    for count in range(0, 10000):
        back = decode(t, encode(t, float(count), "int16_scaled"), "int16_scaled")
        assert display_text(back, 4, 0) == str(count).rjust(4)


@pytest.mark.parametrize(
    "extra, why",
    [
        ({"digits": 0}, "greater than or equal to 1"),
        ({"digits": 7}, "less than or equal to 6"),
        ({"digits": 2, "decimals": 2}, "decimals must be fewer than digits"),
        ({"range": [5, 5]}, "lo < hi"),
        ({"color": "blue"}, "Input should be"),
    ],
)
def test_invalid_settings(extra, why):
    with pytest.raises(ValidationError, match=why):
        _model(**extra)
