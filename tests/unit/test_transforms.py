"""PLAN 2.17: binding-level signal transforms (invert, delays, combine, window) and the CSV."""

from __future__ import annotations

import copy
import csv
import io as _io
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from openfactory.core.iotable import Writer
from openfactory.core.loader import build
from openfactory.core.schema import Scene, Transform
from openfactory.core.transforms import _Signal
from openfactory.export.io_list_csv import io_list_csv

EXAMPLE = Path(__file__).resolve().parents[2] / "scenes" / "sorting-by-height.yaml"
DT = 0.02
PE_02 = {
    "id": "PE_02",
    "type": "photo_eye",
    "description": "Discharge Photo Eye",
    "position": [3.5, 0.15, 0],
    "beam_length": 0.6,
    "io": {"detect": "PE_02_DI"},
}


@pytest.fixture
def data() -> dict:
    return yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))


def comp(data: dict, cid: str) -> dict:
    return next(c for c in data["components"] if c["id"] == cid)


def box_under(loaded, x: float):
    """A still part under an eye at x (on CONV_01, which is not running)."""
    part = loaded.world.spawn("tall", (0.3, 0.4, 0.3), (x, 0.2, 0.0))
    part.carrier = "CONV_01"
    return part


def test_inverted_photo_eye_reads_true_with_no_part(data):
    comp(data, "PE_01")["io"]["detect"] = {"tag": "PE_01_DI", "invert": True}
    loaded = build(Scene.model_validate(data))
    assert loaded.io.get("PE_01_DI") is True  # primed at load: true before the first tick
    for _ in range(5):
        loaded.step(DT)
    assert loaded.io.get("PE_01_DI") is True and loaded.component("PE_01").raw["detect"] is False
    part = box_under(loaded, 1.5)
    for _ in range(3):
        loaded.step(DT)
    assert loaded.io.get("PE_01_DI") is False  # beam blocked: the NC contact opens
    loaded.world.remove(part.id)
    for _ in range(3):
        loaded.step(DT)
    assert loaded.io.get("PE_01_DI") is True


def test_or_combined_input_follows_either_eye(data):
    data["components"].append(copy.deepcopy(PE_02))
    comp(data, "PE_02")["io"]["detect"] = {
        "tag": "PE_ANY_DI",
        "combine": "or",
        "from": ["PE_01.detect", "PE_02.detect"],
    }
    loaded = build(Scene.model_validate(data))
    seen = []
    for x in (1.5, 3.5):  # a box under PE_01 only, then under PE_02 only
        part = box_under(loaded, x)
        for _ in range(3):
            loaded.step(DT)
        seen.append((loaded.io.get("PE_01_DI"), loaded.io.get("PE_ANY_DI")))
        loaded.world.remove(part.id)
        for _ in range(3):
            loaded.step(DT)
        seen.append((loaded.io.get("PE_01_DI"), loaded.io.get("PE_ANY_DI")))
    print(f"(PE_01_DI, PE_ANY_DI) after each change: {seen}")
    assert seen == [(True, True), (False, False), (False, True), (False, False)]
    assert "PE_02_DI" not in loaded.io  # PE_02's port is bound to the combined tag


@pytest.mark.parametrize(("on_delay", "off_delay"), [(0.2, 0.0), (0.0, 0.1), (0.1, 0.06)])
def test_delays_are_exact_to_the_tick(data, on_delay, off_delay):
    comp(data, "PE_01")["io"]["detect"] = {
        "tag": "PE_01_DI",
        "on_delay_s": on_delay,
        "off_delay_s": off_delay,
    }
    loaded = build(Scene.model_validate(data))
    eye = loaded.component("PE_01")
    raw_ticks, tag_ticks = [], []
    part = None
    for tick in range(1, 61):
        if tick == 5:
            part = box_under(loaded, 1.5)
        if tick == 30:
            loaded.world.remove(part.id)
        before_raw, before_tag = eye.raw.get("detect", False), loaded.io.get("PE_01_DI")
        loaded.step(DT)
        if eye.raw["detect"] != before_raw:
            raw_ticks.append(tick)
        if loaded.io.get("PE_01_DI") != before_tag:
            tag_ticks.append(tick)
    expected = [raw_ticks[0] + round(on_delay / DT), raw_ticks[1] + round(off_delay / DT)]
    print(f"on {on_delay} s, off {off_delay} s: detect changed at {raw_ticks}, tag at {tag_ticks}")
    assert tag_ticks == expected


def test_short_pulse_never_reaches_the_plc():
    class Fake:
        raw = {"detect": False}

    sig = _Signal("X_DI", Transform(on_delay_s=0.1), [(Fake, "detect")])
    states = []
    for value in [True, True, True, False, False]:  # a 3-tick (60 ms) pulse, 100 ms on-delay
        Fake.raw["detect"] = value
        states.append(sig.step(DT))
    assert states == [False] * 5


def test_window_on_an_analog_value():
    class Fake:
        raw = {"distance": 0.0}

    sig = _Signal("D_IN_WINDOW_DI", Transform(window=(0.1, 0.4)), [(Fake, "distance")])
    out = []
    for v in (0.05, 0.1, 0.25, 0.4, 0.41):
        Fake.raw["distance"] = v
        out.append(sig.step(DT))
    assert out == [False, True, True, True, False]


def test_inverted_do_runs_the_belt_with_the_coil_off(data):
    comp(data, "CONV_01")["io"]["run"] = {"tag": "CONV_01_RUN_DO", "invert": True}
    loaded = build(Scene.model_validate(data))
    loaded.step(DT)
    assert loaded.component("CONV_01").target_speed() > 0  # coil off: NC contact runs the belt
    loaded.io.set("CONV_01_RUN_DO", True, Writer.PLC)
    loaded.step(DT)
    assert loaded.component("CONV_01").target_speed() == 0


def test_csv_lists_the_transform(data):
    data["components"].append(copy.deepcopy(PE_02))
    comp(data, "PE_01")["io"]["detect"] = {"tag": "PE_01_DI", "invert": True, "on_delay_s": 0.2}
    comp(data, "PE_02")["io"]["detect"] = {
        "tag": "PE_ANY_DI",
        "combine": "or",
        "from": ["PE_01.detect", "PE_02.detect"],
    }
    text = io_list_csv(Scene.model_validate(data))
    rows = {r["tag"]: r for r in csv.DictReader(_io.StringIO(text))}
    print(f"PE_01_DI transform: {rows['PE_01_DI']['transform']!r}")
    print(f"PE_ANY_DI transform: {rows['PE_ANY_DI']['transform']!r}")
    assert rows["PE_01_DI"]["transform"] == "NC (inverted); on-delay 0.2 s"
    assert rows["PE_ANY_DI"]["transform"] == "or(PE_01.detect, PE_02.detect)"
    assert rows["CONV_01_RUN_DO"]["transform"] == ""


@pytest.mark.parametrize(
    ("binding", "message"),
    [
        ({"tag": "PE_X_DI", "combine": "or"}, "`combine` and `from` go together"),
        (
            {"tag": "PE_X_DI", "combine": "or", "from": ["PE_01.detect", "CYL_01.extended"]},
            "must list this port",
        ),  # fmt: skip
        (
            {"tag": "PE_X_DI", "combine": "or", "from": ["PE_02.detect", "CONV_01.run"]},
            "not a DI port of CONV_01",
        ),  # fmt: skip
        (
            {"tag": "PE_X_DI", "combine": "or", "from": ["PE_02.detect", "NOPE_01.detect"]},
            "unknown component 'NOPE_01'",
        ),  # fmt: skip
        ({"tag": "PE_X_DI", "on_delay_s": -1}, "greater than or equal to 0"),
    ],
)
def test_bad_transforms_are_rejected(data, binding, message):
    data["components"].append(copy.deepcopy(PE_02))
    comp(data, "PE_02")["io"]["detect"] = binding
    with pytest.raises(ValidationError, match=message):
        Scene.model_validate(data)
