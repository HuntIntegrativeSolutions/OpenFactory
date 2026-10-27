"""distance_sensor: range along the beam, both axes, per-sensor ranges, the int16_scaled lockfile
recording the range it scaled against, and the window on the switching row of the I/O CSV."""

from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest
import yaml

from openfactory.components.distance_sensor import ray_box
from openfactory.core.loader import build
from openfactory.core.schema import Scene, load_scene
from openfactory.export.io_list_csv import io_list_csv
from openfactory.protocols.modbus import AddressError, encode, scene_register_map

DS = {
    "id": "DS_01",
    "type": "distance_sensor",
    "description": "Box Height Distance Sensor",
    "position": [1.0, 0.6, 0.0],
    "axis": "-y",
    "beam_length": 0.8,
    "io": {
        "distance": "DS_01_DIST_AI",
        "switching": {"tag": "DS_01_TALL_DI", "window": [0.1, 0.3]},
    },
}


def scene(*components: dict) -> dict:
    return {
        "openfactory": "0.1",
        "scene": {"name": "distance", "description": "distance sensor test"},
        "plc": {"protocol": "modbus_tcp", "analog_encoding": "int16_scaled"},
        "components": list(components),
    }


def test_ray_box_enters_starts_inside_and_misses():
    box = ((0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
    assert ray_box((0.5, 3.0, 0.5), (0.0, -1.0, 0.0), *box) == pytest.approx(2.0)
    assert ray_box((0.5, 0.5, 0.5), (0.0, -1.0, 0.0), *box) == 0.0
    assert ray_box((2.0, 3.0, 0.5), (0.0, -1.0, 0.0), *box) is None
    assert ray_box((0.5, 3.0, 0.5), (0.0, 1.0, 0.0), *box) is None  # pointing away


def test_looking_down_it_reads_the_gap_to_the_box_top_and_beam_length_when_clear():
    loaded = build(Scene.model_validate(scene(DS)))
    sensor = loaded.component("DS_01")

    def measure() -> None:  # the sensor, then the window transform, as LoadedScene.step does
        sensor.step(0.02)
        loaded.transforms.step(0.02)  # type: ignore[union-attr]

    measure()
    assert loaded.io.get("DS_01_DIST_AI") == pytest.approx(0.8)
    assert sensor.state() == {"distance_m": pytest.approx(0.8), "target": False}
    loaded.world.spawn("tall", (0.3, 0.4, 0.3), (1.0, 0.2, 0.0))  # top 0.2 below the face
    measure()
    assert loaded.io.get("DS_01_DIST_AI") == pytest.approx(0.2)
    assert loaded.io.get("DS_01_TALL_DI") is True  # window 0.1 to 0.3 m: a tall box
    loaded.world.parts.clear()
    loaded.world.spawn("short", (0.3, 0.2, 0.3), (1.0, 0.1, 0.0))  # 0.4 below
    measure()
    assert loaded.io.get("DS_01_DIST_AI") == pytest.approx(0.4)
    assert loaded.io.get("DS_01_TALL_DI") is False


def test_across_the_flow_it_reads_the_distance_to_the_near_face(rig):
    side = {**DS, "position": [1.0, 0.1, -0.5], "axis": "+z", "rotation_deg": 0, "beam_length": 1.0}
    r = rig({**side, "io": {"distance": "DS_01_DIST_AI"}})
    r.world.spawn("box", (0.3, 0.2, 0.3), (1.05, 0.1, 0.1))  # near face at z -0.05
    r.step()
    assert r.io.get("DS_01_DIST_AI") == pytest.approx(0.45)
    turned = rig({**side, "rotation_deg": 90, "io": {"distance": "DS_01_DIST_AI"}})
    assert turned["DS_01"].direction == pytest.approx((1.0, 0.0, 0.0))  # local +z turned to +x


def test_the_ports_range_is_0_to_beam_length_per_sensor_and_scales_int16():
    loaded = build(Scene.model_validate(scene(DS)))
    tag = loaded.io.tag("DS_01_DIST_AI")
    assert tag.range == (0.0, 0.8) and tag.units == "m"
    assert encode(tag, 0.4, "int16_scaled") == [16384]  # half of 0.8 m is half of 32767
    other = build(Scene.model_validate(scene({**DS, "beam_length": 2.0}))).io.tag("DS_01_DIST_AI")
    assert other.range == (0.0, 2.0)


def test_the_int16_lockfile_records_the_range_and_a_changed_beam_length_fails(tmp_path):
    path = tmp_path / "distance.yaml"
    path.write_text(yaml.safe_dump(scene(DS), sort_keys=False), encoding="utf-8")
    scene_register_map(load_scene(path), scene_path=path)
    lock = (tmp_path / "distance.int16_scaled.modbus.yaml").read_text(encoding="utf-8")
    print(lock)
    assert "ranges:\n  DS_01_DIST_AI: [0, 0.8]\n" in lock
    data = scene({**DS, "beam_length": 1.2})
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    with pytest.raises(AddressError) as err:
        scene_register_map(load_scene(path), scene_path=path)
    print(err.value)
    assert "scales it against 0 to 0.8, but the scene now gives 0 to 1.2" in str(err.value)
    # deleting the range line accepts the new range (and the lockfile records it)
    (tmp_path / "distance.int16_scaled.modbus.yaml").write_text(
        lock.replace("  DS_01_DIST_AI: [0, 0.8]\n", ""), encoding="utf-8"
    )
    scene_register_map(load_scene(path), scene_path=path)
    assert "DS_01_DIST_AI: [0, 1.2]" in (tmp_path / "distance.int16_scaled.modbus.yaml").read_text()


def test_the_io_csv_shows_the_window_on_the_switching_row():
    rows = list(csv.DictReader(io.StringIO(io_list_csv(Scene.model_validate(scene(DS))))))
    by_tag = {r["tag"]: r for r in rows}
    print("\n".join(f"{r['tag']},{r['io_type']},{r['units']},{r['range_min']},{r['range_max']},"
                    f"{r['transform']}" for r in rows))  # fmt: skip
    assert by_tag["DS_01_TALL_DI"]["io_type"] == "DI"
    assert by_tag["DS_01_TALL_DI"]["transform"] == "window 0.1 to 0.3 m"
    assert (by_tag["DS_01_DIST_AI"]["range_min"], by_tag["DS_01_DIST_AI"]["range_max"]) == (
        "0",
        "0.8",
    )


def test_over_a_belt_it_reads_the_belt_when_no_part_is_under_it():
    belt = {
        "id": "CONV_01",
        "type": "belt_conveyor",
        "description": "Belt",
        "position": [0, 0, 0],
        "length": 2.0,
        "width": 0.5,
        "speed_mps": 0.5,
        "io": {"run": "CONV_01_RUN_DO"},
    }
    loaded = build(Scene.model_validate(scene(belt, DS)))
    loaded.step(0.02)
    assert loaded.io.get("DS_01_DIST_AI") == pytest.approx(0.6)  # the belt top, 0.6 below
    assert loaded.component("DS_01").state()["target"] is True
    assert loaded.io.get("DS_01_TALL_DI") is False  # 0.6 is outside the 0.1 to 0.3 window


def test_lint_reports_a_changed_beam_length_as_e101(tmp_path):
    """docs/components.md: a changed beam_length fails the lockfile check, and so lint."""
    from openfactory.lint import lint_scene

    shipped = Path(__file__).resolve().parents[1] / "scenes" / "distance-window.yaml"
    data = yaml.safe_load(shipped.read_text(encoding="utf-8"))
    next(c for c in data["components"] if c["id"] == "DS_01")["beam_length"] = 1.0
    path = tmp_path / shipped.name
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    lock = shipped.with_name("distance-window.int16_scaled.modbus.yaml")
    (tmp_path / lock.name).write_text(lock.read_text(encoding="utf-8"), encoding="utf-8")
    findings = lint_scene(path)
    print("\n".join(str(f) for f in findings))
    assert [f.code for f in findings] == ["E101"]
    assert "scales it against 0 to 0.8, but the scene now gives 0 to 1" in findings[0].message
    assert lint_scene(shipped) == []  # the committed lockfile matches the shipped 0.8 m
