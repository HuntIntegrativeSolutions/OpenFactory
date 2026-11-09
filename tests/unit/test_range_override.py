"""PLAN 3.7: per-tag ``range`` and ``units`` on analog bindings. The tag's span (int16_scaled
scaling, lockfile, I/O list, CONTEXT.md) comes from the binding; the component keeps its port's
physical range, so a speed tag scaled 0 to 50 % runs the belt at half speed at full scale.
``units`` is a label for the I/O list CSV and CONTEXT.md only (ruled 2026-11-08)."""

from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from openfactory.core.iotable import Writer
from openfactory.core.loader import build
from openfactory.core.schema import Scene, load_scene
from openfactory.export.context_md import context_md
from openfactory.export.ignition_tags import ignition_tags_json
from openfactory.export.io_list_csv import io_list_csv
from openfactory.export.l5x_stub import l5x_stub
from openfactory.protocols.modbus import AddressError, decode, encode, scene_register_map

REPO = Path(__file__).resolve().parents[2]
SCENE = REPO / "tests" / "scenes" / "range-override.yaml"


def doc() -> dict:
    return yaml.safe_load(SCENE.read_text(encoding="utf-8"))


def with_io(component: str, port: str, binding: object) -> dict:
    data = doc()
    comp = next(c for c in data["components"] if c["id"] == component)
    comp["io"][port] = binding
    return data


def test_the_tags_take_the_binding_range_and_the_components_keep_the_ports():
    loaded = build(load_scene(SCENE))
    speed, dist = loaded.io.tag("CONV_01_SPD_AO"), loaded.io.tag("DS_01_DIST_AI")
    assert (speed.range, speed.units) == ((0.0, 50.0), "%")
    assert (dist.range, dist.units) == ((0.0, 0.5), "m")
    assert loaded.component("CONV_01").ports["speed"].range == (0.0, 100.0)
    assert loaded.component("DS_01").ports["distance"].range == (0.0, 0.8)


def test_full_scale_on_a_speed_tag_scaled_to_50_percent_runs_the_belt_at_half_speed():
    loaded = build(load_scene(SCENE))
    tag = loaded.io.tag("CONV_01_SPD_AO")
    value = decode(tag, [32767], "int16_scaled")  # what the Modbus server writes into the table
    assert value == 50.0
    loaded.io.set("CONV_01_RUN_DO", True, Writer.PLC)
    loaded.io.set("CONV_01_SPD_AO", value, Writer.PLC)
    for _ in range(100):  # 2 s, past the 0.3 s ramp
        loaded.step(0.02)
    assert loaded.component("CONV_01").state()["speed_mps"] == pytest.approx(0.25)


def test_int16_counts_span_the_binding_range():
    tag = build(load_scene(SCENE)).io.tag("DS_01_DIST_AI")
    assert encode(tag, 0.25, "int16_scaled") == [16384]  # half of 0.5 m, not of the 0.8 m beam
    assert encode(tag, 0.7, "int16_scaled") == [32767]  # past the tag's range: clamped


def test_the_io_list_and_context_md_show_the_binding_ranges(tmp_path):
    scene = load_scene(SCENE)
    rows = {r["tag"]: r for r in csv.DictReader(io.StringIO(io_list_csv(scene)))}
    assert (rows["CONV_01_SPD_AO"]["range_min"], rows["CONV_01_SPD_AO"]["range_max"]) == ("0", "50")
    assert rows["CONV_01_SPD_AO"]["units"] == "%"
    assert (rows["DS_01_DIST_AI"]["range_max"], rows["DS_01_DIST_AI"]["units"]) == ("0.5", "m")
    text = context_md(scene, tmp_path / "range-override.yaml")
    row = next(line for line in text.splitlines() if line.startswith("| `CONV_01_SPD_AO`"))
    assert "% 0 to 50" in row
    row = next(line for line in text.splitlines() if line.startswith("| `DS_01_DIST_AI`"))
    assert "m 0 to 0.5" in row


def test_the_int16_lockfile_records_the_binding_range_and_a_changed_one_fails(tmp_path):
    path = tmp_path / "range-override.yaml"
    path.write_text(yaml.safe_dump(doc(), sort_keys=False), encoding="utf-8")
    scene_register_map(load_scene(path), scene_path=path)
    lock = (tmp_path / "range-override.int16_scaled.modbus.yaml").read_text(encoding="utf-8")
    assert "  CONV_01_SPD_AO: [0, 50]\n" in lock and "  DS_01_DIST_AI: [0, 0.5]\n" in lock
    changed = with_io("CONV_01", "speed", {"tag": "CONV_01_SPD_AO", "range": [0, 80]})
    path.write_text(yaml.safe_dump(changed, sort_keys=False), encoding="utf-8")
    with pytest.raises(
        AddressError, match="scales it against 0 to 50, but the scene now gives 0 to 80"
    ):
        scene_register_map(load_scene(path), scene_path=path)


@pytest.mark.parametrize(
    ("component", "port", "binding", "message"),
    [
        ("CONV_01", "speed", {"tag": "CONV_01_SPD_AO", "range": [50, 50]}, "needs lo < hi"),
        ("CONV_01", "speed", {"tag": "CONV_01_SPD_AO", "range": [60, 10]}, "needs lo < hi"),
        (
            "DS_01",
            "distance",
            {"tag": "DS_01_NEAR_DI", "window": [0.1, 0.3], "range": [0, 0.5]},
            "a windowed binding is a DI and takes no range or units",
        ),
        (
            "CONV_01",
            "speed",
            {"tag": "CONV_01_SPD_AO", "range": [0, 50], "initial": 70},
            "initial 70 for CONV_01_SPD_AO is outside the port's range 0 to 50 %",
        ),
    ],
)
def test_bad_overrides_fail_at_load_and_say_why(component, port, binding, message):
    with pytest.raises(ValidationError, match=message):
        Scene.model_validate(with_io(component, port, binding))


def test_a_units_label_shows_in_the_csv_and_context_md_and_nowhere_else(tmp_path):
    data = with_io("DS_01", "distance", {"tag": "DS_01_DIST_AI", "units": "mH2O"})
    scene = Scene.model_validate(data)
    tag = build(scene).io.tag("DS_01_DIST_AI")
    assert (tag.units, tag.range) == ("m", (0.0, 0.8))  # the label converts nothing
    rows = {r["tag"]: r for r in csv.DictReader(io.StringIO(io_list_csv(scene)))}
    assert (rows["DS_01_DIST_AI"]["units"], rows["DS_01_DIST_AI"]["range_max"]) == ("mH2O", "0.8")
    text = context_md(scene, tmp_path / "range-override.yaml")
    row = next(line for line in text.splitlines() if line.startswith("| `DS_01_DIST_AI`"))
    assert "mH2O 0 to 0.8" in row
    assert '"engUnit": "m"' in ignition_tags_json(scene)
    assert '"engUnit": "mH2O"' not in ignition_tags_json(scene)
    stub = l5x_stub(scene)
    assert "[0 to 0.8 m]" in stub and "mH2O" not in stub


def test_a_label_may_restate_the_ports_units():
    data = with_io("CONV_01", "speed", {"tag": "CONV_01_SPD_AO", "range": [0, 50], "units": "%"})
    assert build(Scene.model_validate(data)).io.tag("CONV_01_SPD_AO").units == "%"


def test_a_binding_without_overrides_is_unchanged():
    data = with_io("CONV_01", "speed", "CONV_01_SPD_AO")
    assert build(Scene.model_validate(data)).io.tag("CONV_01_SPD_AO").range == (0.0, 100.0)
