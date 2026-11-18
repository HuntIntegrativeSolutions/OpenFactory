"""PLAN 5.4a: plants in several files, ``include:`` at load (design note §4.2)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from openfactory.core.plant import PlantError
from openfactory.core.schema import MAX_SCENE_BYTES, load_scene


def belt(cid: str, x: float, tag: str | None = None) -> dict:
    return {"id": cid, "type": "belt_conveyor", "description": f"Belt {cid}",
            "position": [x, 0, 0], "length": 2.0, "width": 0.5, "speed_mps": 0.3,
            "io": {"run": tag or f"{cid}_RUN_DO"}}  # fmt: skip


def area_doc(name: str, *components: dict, **extra) -> dict:
    return {"openfactory": "0.1", "scene": {"name": name, "description": f"Area {name}."},
            "components": list(components), **extra}  # fmt: skip


def write(path: Path, doc: dict | str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(doc if isinstance(doc, str) else yaml.safe_dump(doc, sort_keys=False),
                    encoding="utf-8")  # fmt: skip
    return path


def plant(tmp_path: Path, include: list[dict] | None = None, **extra) -> Path:
    """A plant of two areas, A (CV_101) and B (CV_201), linked CV_101 -> CV_201."""
    folder = tmp_path / "plant"
    write(folder / "areas" / "a.yaml", area_doc("area-a", belt("CV_101", 0.0)))
    write(folder / "areas" / "b.yaml", area_doc("area-b", belt("CV_201", 2.0)))
    doc = {"openfactory": "0.1", "scene": {"name": "plant", "description": "A plant."},
           "plc": {"protocol": "modbus_tcp"},
           "include": include or [{"area": "A", "file": "areas/a.yaml"},
                                  {"area": "B", "file": "areas/b.yaml"}],
           "connections": [{"from": "CV_101", "to": "CV_201"}], **extra}  # fmt: skip
    return write(folder / "plant.yaml", doc)


def test_a_plant_loads_as_one_scene_and_an_area_alone(tmp_path) -> None:
    path = plant(tmp_path)
    scene = load_scene(path)
    assert [c.id for c in scene.components] == ["CV_101", "CV_201"]
    assert [(c.from_, c.to) for c in scene.connections] == [("CV_101", "CV_201")]
    assert scene.plant.sources == {"CV_101": "areas/a.yaml", "CV_201": "areas/b.yaml"}
    alone = load_scene(path, area="B")
    assert [c.id for c in alone.components] == ["CV_201"] and alone.connections == []
    assert alone.scene.area == "B"
    assert alone.plant.notes == [
        "--area B: the plant's connection CV_101 -> CV_201 has an end outside the area and is "
        "left out"
    ]
    with pytest.raises(PlantError, match="no area 'C'; its areas are A, B"):
        load_scene(path, area="C")


def test_a_scene_without_include_has_no_areas(tmp_path) -> None:
    path = write(tmp_path / "s.yaml", area_doc("s", belt("CV_101", 0.0)))
    assert load_scene(path).plant is None
    with pytest.raises(ValueError, match="has no include:, so it is not a plant"):
        load_scene(path, area="A")


@pytest.mark.parametrize(
    ("file", "message"),
    [
        ("/etc/hosts", "is an absolute path"),
        ("C:/areas/a.yaml", "is an absolute path"),
        ("\\\\server\\share\\a.yaml", "is an absolute path"),
        ("../outside.yaml", "leaves the plant's folder"),
        ("areas/../../outside.yaml", "leaves the plant's folder"),
    ],
)
def test_an_include_path_must_stay_in_the_plants_folder(tmp_path, file, message) -> None:
    write(tmp_path / "outside.yaml", area_doc("out", belt("CV_901", 9.0)))
    path = plant(tmp_path, [{"area": "A", "file": "areas/a.yaml"}, {"area": "X", "file": file}])
    with pytest.raises(PlantError, match=f"plant.yaml: include .* {message}"):
        load_scene(path)


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_a_symlink_out_of_the_folder_is_refused(tmp_path) -> None:
    outside = write(tmp_path / "outside.yaml", area_doc("out", belt("CV_901", 9.0)))
    path = plant(tmp_path, [{"area": "A", "file": "areas/a.yaml"},
                            {"area": "X", "file": "areas/link.yaml"}])  # fmt: skip
    (path.parent / "areas" / "link.yaml").symlink_to(outside)
    with pytest.raises(PlantError, match="outside the plant's folder .* through a symlink"):
        load_scene(path)


def test_an_included_file_is_read_as_a_scene_is(tmp_path) -> None:
    path = plant(tmp_path)
    big = path.parent / "areas" / "b.yaml"
    big.write_text("#" * (MAX_SCENE_BYTES + 1), encoding="utf-8")
    with pytest.raises(PlantError, match=r"areas/b.yaml: b.yaml: \d+ bytes; a scene file is at"):
        load_scene(path)
    big.write_text("openfactory: !!python/object/apply:os.system ['echo hi']\n", encoding="utf-8")
    with pytest.raises(PlantError, match="areas/b.yaml: could not determine a constructor"):
        load_scene(path)


def test_a_plant_that_includes_itself_is_a_cycle_naming_the_files(tmp_path) -> None:
    for spelling in ("plant.yaml", "./plant.yaml", "areas/../plant.yaml"):
        path = plant(tmp_path, [{"area": "A", "file": spelling}])
        with pytest.raises(PlantError, match=f"include cycle: plant.yaml includes '{spelling}"
                                             "', which is plant.yaml itself"):  # fmt: skip
            load_scene(path)


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_a_cycle_through_a_symlink_or_an_area_is_refused(tmp_path) -> None:
    path = plant(tmp_path, [{"area": "A", "file": "areas/again.yaml"}])
    (path.parent / "areas" / "again.yaml").symlink_to(path)
    with pytest.raises(PlantError, match="include cycle: plant.yaml includes 'areas/again.yaml'"):
        load_scene(path)
    path = plant(tmp_path)
    a = path.parent / "areas" / "a.yaml"
    write(a, area_doc("area-a", belt("CV_101", 0.0),
                      include=[{"area": "P", "file": "../plant.yaml"}]))  # fmt: skip
    with pytest.raises(PlantError, match="include cycle: plant.yaml includes areas/a.yaml, which "
                                         "includes plant.yaml"):  # fmt: skip
        load_scene(path)


def test_an_area_cannot_include(tmp_path) -> None:
    path = plant(tmp_path)
    write(path.parent / "areas" / "c.yaml", area_doc("c", belt("CV_301", 4.0)))
    write(path.parent / "areas" / "a.yaml", area_doc(
        "area-a", belt("CV_101", 0.0), include=[{"area": "C", "file": "c.yaml"}]))  # fmt: skip
    with pytest.raises(PlantError, match="areas/a.yaml: an area cannot include \\(include is one "
                                         "level deep\\); plant.yaml includes it"):  # fmt: skip
        load_scene(path)


def test_an_error_in_an_included_file_names_the_file_and_its_line(tmp_path) -> None:
    path = plant(tmp_path)
    b = path.parent / "areas" / "b.yaml"
    b.write_text(
        'openfactory: "0.1"\nscene: {name: area-b, description: Area B.}\ncomponents:\n'
        "  - id: CV_201\n    type: belt_conveyor\n    description: Belt\n"
        "    position: [2, 0, 0]\n    length: 2.0\n    width: 0.5\n    speed_mps: fast\n"
        "    io: {run: CV_201_RUN_DO}\n",
        encoding="utf-8",
    )
    with pytest.raises(PlantError, match=r"^areas/b.yaml: line 10: components.0.belt_conveyor."
                                         r"speed_mps: Input should be a valid number"):  # fmt: skip
        load_scene(path)


def test_an_id_or_a_tag_in_two_areas_is_a_load_error_naming_both_files(tmp_path) -> None:
    path = plant(tmp_path)
    write(path.parent / "areas" / "b.yaml", area_doc("area-b", belt("CV_101", 2.0, "X_RUN_DO")))
    with pytest.raises(PlantError, match="id 'CV_101' is in both areas/a.yaml and areas/b.yaml"):
        load_scene(path)
    write(
        path.parent / "areas" / "b.yaml", area_doc("area-b", belt("CV_201", 2.0, "CV_101_RUN_DO"))
    )
    with pytest.raises(PlantError, match="tag 'CV_101_RUN_DO' is in both areas/a.yaml and "
                                         "areas/b.yaml"):  # fmt: skip
        load_scene(path)
    # the plant file's own components count too
    path = plant(tmp_path, components=[belt("CV_201", 6.0, "Y_RUN_DO")])
    with pytest.raises(PlantError, match="id 'CV_201' is in both areas/b.yaml and plant.yaml"):
        load_scene(path)


FAN = {"type": "fan_unit", "description": "A fan.",
       "parts": [{"id": "body", "type": "scenery", "description": "Body",
                  "position": [0, 0.25, 0], "size": [0.5, 0.5, 0.5]}],
       "io": {"run": {"command": "DO"}, "running": {"from": "run"}}}  # fmt: skip


def fan(cid: str, x: float) -> dict:
    return {"id": cid, "type": "fan_unit", "description": f"Fan {cid}", "position": [x, 0, 1],
            "io": {"run": f"{cid}_RUN_DO", "running": f"{cid}_RUN_DI"}}  # fmt: skip


def test_a_composite_in_two_areas_is_one_type_and_a_different_one_an_error(tmp_path) -> None:
    path = plant(tmp_path)
    write(
        path.parent / "areas" / "a.yaml",
        area_doc("area-a", belt("CV_101", 0.0), fan("FAN_101", 0.0), composites=[FAN]),
    )
    write(
        path.parent / "areas" / "b.yaml",
        area_doc("area-b", belt("CV_201", 2.0), fan("FAN_201", 2.0), composites=[FAN]),
    )
    scene = load_scene(path)  # fmt: skip
    assert {scene.instance_type("FAN_101"), scene.instance_type("FAN_201")} == {"fan_unit"}
    other = {**FAN, "description": "Another fan."}
    write(
        path.parent / "areas" / "b.yaml",
        area_doc("area-b", belt("CV_201", 2.0), fan("FAN_201", 2.0), composites=[other]),
    )
    with pytest.raises(PlantError, match="composite type 'fan_unit' is defined differently in "
                                         "areas/a.yaml and areas/b.yaml"):  # fmt: skip
        load_scene(path)
