"""PLAN 5.2b: ``lag_s`` on an AI binding, a first-order lag in the transform stage with the
transmitter's discretization (forward Euler, the first value taken as it is)."""

from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from openfactory.core.iotable import Writer
from openfactory.core.loader import build
from openfactory.core.schema import Scene
from openfactory.export.io_list_csv import io_list_csv

REPO = Path(__file__).resolve().parents[2]
ITEM = REPO / "tests" / "scenes" / "composite-additions.yaml"
TANK = REPO / "scenes" / "tank-level-pid.yaml"
DT = 0.02


def lagged_item() -> Scene:
    doc = yaml.safe_load(ITEM.read_text(encoding="utf-8"))
    doc["components"][0]["io"]["speed_fb"] = {"tag": "MX_01_SPD_AI", "lag_s": 4}
    return Scene.model_validate(doc, context={"scene_dir": ITEM.parent})


def step_response() -> list[float]:
    """The speed feedback each tick after a step of the speed command from 0 to 100."""
    scene = build(lagged_item())
    for _ in range(10):
        scene.step(DT)
    assert scene.io.get("MX_01_SPD_AI") == 0.0
    scene.io.set("MX_01_SPD_AO", 100.0, Writer.PLC)
    scene.io.set("MX_01_RUN_DO", True, Writer.PLC)
    out = []
    for _ in range(800):
        scene.step(DT)
        out.append(scene.io.get("MX_01_SPD_AI"))
    return out


def test_a_step_through_lag_4_reads_63_at_4_s_and_98_at_16_s() -> None:
    """Done-when: within 0.5 of 63.2 at 4 s and of 98.2 at 16 s, at dt 0.02."""
    values = step_response()
    at_4, at_16 = values[199], values[799]  # after 200 and 800 ticks of 0.02 s
    assert at_4 == pytest.approx(63.2, abs=0.5)
    assert at_16 == pytest.approx(98.2, abs=0.5)
    # forward Euler, as the transmitter: 100 (1 - (1 - dt / lag) ** n), to float rounding
    assert at_4 == pytest.approx(100 * (1 - (1 - DT / 4) ** 200), abs=1e-3)
    assert at_16 == pytest.approx(100 * (1 - (1 - DT / 4) ** 800), abs=1e-3)
    print(f"lag 4 s: {at_4:.4f} at 4 s, {at_16:.4f} at 16 s")


def test_the_run_is_identical_twice() -> None:
    assert step_response() == step_response()


def test_the_csv_transform_column_says_lag_4_s() -> None:
    rows = csv.DictReader(io.StringIO(io_list_csv(lagged_item(), write_lock=False)))
    row = next(r for r in rows if r["tag"] == "MX_01_SPD_AI")
    assert row["transform"] == "expr(speed if running else 0); lag 4 s"


def test_a_part_ai_port_is_lagged_from_its_raw_value_and_starts_as_it_is() -> None:
    """A transmitter's own pv, lagged again on its binding: the tag is the Euler filter of the
    port's raw value each tick, its first value the raw one."""
    doc = yaml.safe_load(TANK.read_text(encoding="utf-8"))
    lt = next(c for c in doc["components"] if c["type"] == "transmitter")
    lt["io"]["pv"] = {"tag": lt["io"]["pv"], "lag_s": 2.5}
    scene = build(Scene.model_validate(doc, context={"scene_dir": TANK.parent}))
    comp = next(c for c in scene.components if c.type == "transmitter")
    tag = lt["io"]["pv"]["tag"]
    assert scene.io.get(tag) == pytest.approx(float(comp.raw.get("pv", 0.0)))
    expected = scene.io.get(tag)
    for _ in range(500):
        scene.step(DT)
        expected += (float(comp.raw["pv"]) - expected) * min(DT / 2.5, 1.0)
        assert scene.io.get(tag) == pytest.approx(expected, abs=1e-9)
    assert abs(scene.io.get(tag) - float(comp.raw["pv"])) > 1e-4  # it lags


def test_lag_s_is_refused_on_a_windowed_binding() -> None:
    from openfactory.core.schema import AiTagSpec

    with pytest.raises(ValidationError, match="takes no lag_s"):
        AiTagSpec.model_validate({"tag": "DS_01_DI", "window": [0.1, 0.2], "lag_s": 1})
    with pytest.raises(ValidationError):
        AiTagSpec.model_validate({"tag": "DS_01_AI", "lag_s": -1})
