"""PLAN 5.4c: lint across a plant's areas (design note §4.2, "Lint across areas")."""

from __future__ import annotations

from pathlib import Path

import yaml
from typer.testing import CliRunner

from openfactory.cli import app as cli
from openfactory.lint import lint_scene


def belt(cid: str, x: float, z: float = 0.0) -> dict:
    return {"id": cid, "type": "belt_conveyor", "description": f"Belt {cid}",
            "position": [x, 0, z], "length": 2.0, "width": 0.5, "speed_mps": 0.3,
            "io": {"run": f"{cid}_RUN_DO"}}  # fmt: skip


def write(path: Path, doc: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return path


def make(folder: Path, b_at: float = 2.0, area_dir: str = "areas") -> Path:
    for name, comp in (("a", belt("CV_101", 0.0)), ("b", belt("CV_201", b_at))):
        write(folder / area_dir / f"{name}.yaml",
              {"openfactory": "0.1", "scene": {"name": f"area-{name}", "description": "An area."},
               "plc": {"protocol": "modbus_tcp"}, "components": [comp],
               "checks": {"duration_s": 10}})  # fmt: skip
    prefix = f"{area_dir}/" if area_dir else ""
    return write(folder / "plant.yaml",
                 {"openfactory": "0.1", "scene": {"name": "plant", "description": "A plant."},
                  "plc": {"protocol": "modbus_tcp"},
                  "include": [{"area": "A", "file": f"{prefix}a.yaml"},
                              {"area": "B", "file": f"{prefix}b.yaml"}]})  # fmt: skip


def codes(findings) -> list[str]:
    return [f.code for f in findings]


def test_each_included_file_gets_one_info_line(tmp_path) -> None:
    plant = make(tmp_path / "p")
    infos = [str(f) for f in lint_scene(plant) if f.level == "info"]
    assert infos == [
        "INFO  I216 areas/a.yaml (area A): its own scene, plc and checks are for running it "
        "alone; the plant's apply here",
        "INFO  I216 areas/b.yaml (area B): its own scene, plc and checks are for running it "
        "alone; the plant's apply here",
    ]


def test_w216_an_included_file_with_its_own_lockfile(tmp_path) -> None:
    plant = make(tmp_path / "p")
    assert "W216" not in codes(lint_scene(plant))
    (plant.parent / "areas" / "b.float32.modbus.yaml").write_text("x: 1\n", encoding="utf-8")
    w216 = [str(f) for f in lint_scene(plant) if f.code == "W216"]
    assert w216 == [
        "WARN  W216 areas/b.yaml (area B) has its own lockfile b.float32.modbus.yaml: run alone it "
        "has its own address map, not the plant's; run it with --area B instead"
    ]


def test_an_overlap_across_the_area_boundary_is_found_only_in_the_plant(tmp_path) -> None:
    plant = make(tmp_path / "p", b_at=1.0)  # CV_201 starts halfway along CV_101
    assert "W205" in codes(lint_scene(plant))
    for area in ("a", "b"):
        assert "W205" not in codes(lint_scene(plant.parent / "areas" / f"{area}.yaml"))


def test_a_folder_lint_lints_each_area_once_inside_its_plant(tmp_path) -> None:
    folder = tmp_path / "flat"
    make(folder, area_dir="")  # plant.yaml, a.yaml and b.yaml side by side
    write(folder / "other.yaml", {"openfactory": "0.1",
                                  "scene": {"name": "other", "description": "Another scene."},
                                  "components": [belt("CV_901", 9.0)]})  # fmt: skip
    for command in ("lint", "validate"):
        done = CliRunner().invoke(cli, [command, str(folder)])
        assert done.exit_code == 0, done.output
        assert "plant.yaml" in done.output and "other.yaml" in done.output
        assert "/a.yaml" not in done.output.replace("I216 a.yaml", "")
        assert f"{folder / 'b.yaml'}" not in done.output
    done = CliRunner().invoke(cli, ["lint", str(folder)])
    assert "2 scene(s)" in done.output


def test_the_editor_refuses_a_plant_until_5_4e(tmp_path) -> None:
    plant = make(tmp_path / "p")
    done = CliRunner().invoke(cli, ["edit", str(plant)])
    assert done.exit_code == 1
    assert "is a plant file (include:); the editor does not open a plant yet" in done.output
