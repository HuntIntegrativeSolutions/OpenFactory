"""PLAN 5.5, review fixes of 2026-11-19 (second set), ruling 4: layout checks its own result.
After placing, it runs the W217 overlap check over what it placed against everything, reports
every finding and exits 1 when there is one; it still writes the scene and says so. Where it is
cheap, layout steps an item out until it is clear (an instrument, a drive), in the same fixed
order; a conveyor that continues a drawn belt cannot step and is reported."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from openfactory import takeoff as tk
from openfactory.cli import app
from openfactory.core.loader import load
from openfactory.scaffold import scaffold

HEAD = """\
takeoff: "0.1"
plant: {name: blind, description: A drawn tank where a blind placement lands.}
drawings: [{id: P-1, kind: pid, title: Test P&ID, rev: A}]
"""
PUMP_LINE = """\
  - {row: EQ-1, tag: SUP-1, type: supply, description: Test Supply, ref: [{sheet: P-1}],
     at: [-1.5, 0.3, 0.0], confidence: read, questions: []}
  - {row: EQ-2, tag: P-1, type: pump, description: Test Pump, ref: [{sheet: P-1}],
     at: [0.0, 0.3, 0.0], confidence: read, questions: []}
  - {row: EQ-3, tag: DR-1, type: drain, description: Test Drain, ref: [{sheet: P-1}],
     at: [1.5, 0.3, 0.0], confidence: read, questions: []}
"""
PUMP_LINKS = """\
connections:
  - {row: CN-1, from: SUP-1, to: P-1.suction, ref: [{sheet: P-1}], confidence: read, questions: []}
  - {row: CN-2, from: P-1.discharge, to: DR-1, ref: [{sheet: P-1}], confidence: read, questions: []}
io:
"""


def io(row: int, equipment: str, port: str, signal: str) -> str:
    return (f"  - {{row: IO-{row}, equipment: {equipment}, port: {port}, signal: {signal},"
            " ref: [{sheet: P-1}], confidence: read, questions: []}\n")  # fmt: skip


def obstacle(x: float, y: float, z: float) -> str:
    """A drawn tank, 0.4 m across and 1.0 m tall, standing at x, y, z."""
    return (
        "  - {row: EQ-9, tag: TK-9, type: tank, description: Test Obstacle Tank,"
        " params: {diameter: 0.4, height: 1.0}, ref: [{sheet: P-1}],"
        f" at: [{x}, {y}, {z}], confidence: read, questions: []}}\n"
    )


INSTRUMENT = (
    HEAD
    + "equipment:\n"
    + PUMP_LINE
    + """\
  - {row: EQ-4, tag: FT-1, type: transmitter, description: Test Flow Transmitter,
     params: {measures: P-1.flow, range: [0, 5]}, ref: [{sheet: P-1}],
     confidence: read, questions: []}
"""
    + obstacle(0.0, 0.0, -0.6)
    + PUMP_LINKS
    + io(1, "P-1", "run", "DO")
    + io(2, "FT-1", "pv", "AI")
)

DRIVE = (
    HEAD
    + "equipment:\n"
    + PUMP_LINE
    + """\
  - {row: EQ-4, tag: M-1, type: vfd_motor, description: Test Pump Drive,
     params: {drives: P-1}, ref: [{sheet: P-1}], confidence: read, questions: []}
"""
    + obstacle(0.0, 0.0, 0.8)
    + PUMP_LINKS
    + io(2, "M-1", "run", "DO")
    + io(3, "M-1", "speed", "AO")
)

CONVEYOR = (
    HEAD
    + """\
equipment:
  - {row: EQ-1, tag: CV-1, type: belt_conveyor, description: Test Drawn Belt,
     params: {length: 2.0, width: 0.5, speed_mps: 0.3}, ref: [{sheet: P-1}], at: [0.0, 0.8, 3.0],
     confidence: read, questions: []}
  - {row: EQ-2, tag: CV-2, type: belt_conveyor, description: Test Laid Out Belt,
     params: {length: 2.0, width: 0.5, speed_mps: 0.3}, ref: [{sheet: P-1}],
     confidence: read, questions: []}
"""
    + obstacle(2.6, 0.0, 3.0)
    + """\
connections:
  - {row: CN-1, from: CV-1, to: CV-2, ref: [{sheet: P-1}], confidence: read, questions: []}
io:
"""
    + io(1, "CV-1", "run", "DO")
    + io(2, "CV-2", "run", "DO")
)


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "blind.takeoff.yaml"
    path.write_text(text, encoding="utf-8")
    assert not [f for f in tk.check(tk.load(path)) if f.level == "error"]
    return path


def test_a_conveyor_continuing_a_drawn_belt_onto_a_drawn_body_is_reported_exit_1(tmp_path):
    """It cannot step without leaving its belt's discharge: the finding is printed, the scene is
    written, and the exit code is 1."""
    takeoff = write(tmp_path, CONVEYOR)
    result = scaffold(tk.load(takeoff))
    assert result.overlaps and all("CV_2" in m and "TK_9" in m for m in result.overlaps)
    assert result.exit_code() == 1
    out = tmp_path / "blind.yaml"
    done = CliRunner().invoke(app, ["layout", str(takeoff), "-o", str(out)])
    assert done.exit_code == 1, done.output
    assert "W217" in done.output and "CV_2 and TK_9 overlap" in done.output
    assert "written" in done.output and out.is_file()
    load(out).step(0.02)  # the scene it wrote builds and runs


def test_a_finding_in_layout_scene_is_reported_and_the_scene_still_written(tmp_path):
    """`layout SCENE` checks the same way: what it placed against everything."""
    takeoff = write(tmp_path, CONVEYOR)
    scene = tmp_path / "blind.yaml"
    scene.write_text(scaffold(tk.load(takeoff)).text, encoding="utf-8")
    out = tmp_path / "again.yaml"
    done = CliRunner().invoke(app, ["layout", str(scene), "-o", str(out)])
    assert done.exit_code == 1, done.output
    assert "CV_2 and TK_9 overlap" in done.output and out.is_file()


def test_an_instrument_and_a_drive_step_out_clear_of_a_drawn_body(tmp_path):
    """A flow transmitter in front of its pump, and a drive behind it, where a drawn tank stands:
    each steps further out until clear, so there is no finding and the exit code is not 1."""
    for text, item, axis, sign in ((INSTRUMENT, "FT_1", 2, -1), (DRIVE, "M_1", 2, 1)):
        result = scaffold(tk.load(write(tmp_path, text)))
        assert not result.overlaps, result.overlaps
        assert result.exit_code() != 1
        scene = tmp_path / "blind.yaml"
        scene.write_text(result.text, encoding="utf-8")
        comps = {c.id: c for c in load(scene).components}
        pump, tank = comps["P_1"].config.position, comps["TK_9"].config.position
        moved = comps[item].config.position[axis] - pump[axis]
        assert sign * moved > sign * (tank[axis] - pump[axis]), (item, moved)  # past the tank


AREA = """\
openfactory: "0.1"
scene: {name: area-a, description: One area of a test plant.}
plc: {protocol: modbus_tcp}
components:
  - id: CV_A01
    type: belt_conveyor
    description: Area A Belt Conveyor
    position: [0.0, 0.8, 0.0]
    length: 4.0
    width: 0.6
    speed_mps: 0.3
    io: {run: CV_A01_RUN_DO}
"""
PLANT = """\
openfactory: "0.1"
scene: {name: test-plant, description: A plant with one included area and one item of its own.}
plc: {protocol: modbus_tcp}
include:
  - {area: A, file: areas/a.yaml}
components:
  - id: TK_P01
    type: tank
    description: Plant Wash Water Tank
    position: [2.0, 0.0, 0.0]
    laid_out: true
    diameter: 1.0
    height: 1.5
"""


def test_layout_on_a_plant_keeps_included_components_as_obstacles(tmp_path):
    """Review fix 1d (docs/layout.md): on a plant file, layout places only the plant file's own
    marked components, treats every included component as an obstacle it keeps clear of, and
    never moves or writes an included file."""
    (tmp_path / "areas").mkdir()
    area = tmp_path / "areas" / "a.yaml"
    area.write_text(AREA, encoding="utf-8")
    plant = tmp_path / "plant.yaml"
    plant.write_text(PLANT, encoding="utf-8")
    out = tmp_path / "plant-laid.yaml"
    done = CliRunner().invoke(app, ["layout", str(plant), "-o", str(out)])
    assert done.exit_code == 0, done.output
    assert area.read_text(encoding="utf-8") == AREA  # never written
    assert "CV_A01" not in out.read_text(encoding="utf-8")  # nor copied into the plant file
    comps = {c.id: c for c in load(out).components}
    assert comps["CV_A01"].config.position == (0.0, 0.8, 0.0)  # never moved
    assert comps["TK_P01"].config.position != (2.0, 0.0, 0.0)  # it stood on the belt
    lint = CliRunner().invoke(app, ["lint", str(out)])
    assert "W217" not in lint.output, lint.output
    docs = (Path(__file__).resolve().parents[2] / "docs" / "layout.md").read_text("utf-8")
    assert "included" in docs and "obstacle" in docs
