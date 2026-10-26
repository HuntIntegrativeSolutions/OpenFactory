"""PLAN 2.20: `openfactory lint` errors (exit 1) and warnings (exit 0)."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from openfactory.cli import app
from openfactory.lint import lint_scene, script_tags

REPO = Path(__file__).resolve().parents[2]
EXAMPLE = REPO / "scenes" / "sorting-by-height.yaml"
QUEUE = REPO / "tests" / "scenes" / "queue-parity.yaml"
runner = CliRunner()


def write(tmp_path: Path, data: dict, name: str = "scene.yaml") -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


@pytest.fixture
def sorter() -> dict:
    return yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))


def codes(findings) -> list[str]:
    return [f.code for f in findings]


def test_shipped_scene_is_clean(strip_ansi):
    assert lint_scene(EXAMPLE) == []
    result = runner.invoke(app, ["lint", str(EXAMPLE.parent)])
    out = strip_ansi(result.output)
    print(out)
    assert result.exit_code == 0 and "0 error(s), 0 warning(s)" in out


def test_unbound_required_port_is_an_error_and_exits_1(tmp_path, sorter, strip_ansi):
    pe = next(c for c in sorter["components"] if c["id"] == "PE_01")
    del pe["io"]["detect"]
    path = write(tmp_path, sorter)
    findings = lint_scene(path)
    assert codes(findings) == ["E100"] and "detect" in findings[0].message
    assert "Field required" in findings[0].message
    result = runner.invoke(app, ["lint", str(path)])
    out = strip_ansi(result.output)
    print(out)
    assert result.exit_code == 1 and "FAIL" in out and "1 error(s)" in out


def test_lockfile_conflict_is_an_error(tmp_path, sorter):
    path = write(tmp_path, sorter, "sorting-by-height.yaml")
    (tmp_path / "sorting-by-height.float32.modbus.yaml").write_text("encoding: int16_scaled\n")
    findings = lint_scene(path)
    assert [f.code for f in findings if f.level == "error"] == ["E101"]
    assert "written for int16_scaled" in findings[0].message


def test_tags_no_plc_side_writes_or_reads_are_warnings_a_script_clears(tmp_path):
    findings = lint_scene(QUEUE)
    print("\n".join(map(str, findings)))
    assert codes(findings) == ["W201", "W201", "W202"]
    script = tmp_path / "plc.py"
    script.write_text(
        "def step(io, t):\n"
        "    io['CONV_A_RUN_DO'] = True\n"
        "    io[\"CONV_B_RUN_DO\"] = io['PE_B_DI'] or t > 1\n"
    )
    assert script_tags([script]) == ({"CONV_A_RUN_DO", "CONV_B_RUN_DO"}, {"PE_B_DI"})
    assert lint_scene(QUEUE, [script]) == []
    result = runner.invoke(app, ["lint", str(QUEUE), "--script", str(script)])
    assert result.exit_code == 0


def test_touching_conveyors_outside_floor_and_overlaps_warn(tmp_path, sorter, strip_ansi):
    conv2 = next(c for c in sorter["components"] if c["id"] == "CONV_02")
    conv2["position"] = [4.0, 0, 0]  # touches CONV_01's discharge, no connection: W203
    far = copy.deepcopy(conv2)
    far.update(id="CONV_09", position=[25.0, 0, 0], description="Far Conveyor")  # W204
    far["io"] = {"run": "CONV_09_RUN_DO"}
    over = copy.deepcopy(conv2)
    over.update(id="CONV_08", position=[1.0, 0, 0.1], description="Overlapping Conveyor")  # W205
    over["io"] = {"run": "CONV_08_RUN_DO"}
    upper = copy.deepcopy(over)
    upper.update(id="CONV_07", position=[1.0, 1.0, 0.1], description="Upper Deck Conveyor")
    upper["io"] = {"run": "CONV_07_RUN_DO"}  # same footprint, 1 m higher: not an overlap
    sorter["components"] += [far, over, upper]
    path = write(tmp_path, sorter, "sorting-by-height.yaml")
    # no lockfile next to this copy, so every tag also gets W201/W202; look at the rest
    findings = [f for f in lint_scene(path) if f.code not in ("W201", "W202")]
    print("\n".join(map(str, findings)))
    assert codes(findings) == ["W203", "W204", "W205"]
    assert "CONV_01 and CONV_08" in findings[2].message
    result = runner.invoke(app, ["lint", str(path)])
    assert result.exit_code == 0 and "0 error(s)" in strip_ansi(result.output)


def test_connected_end_to_end_conveyors_do_not_count_as_overlapping(tmp_path, sorter):
    conv2 = next(c for c in sorter["components"] if c["id"] == "CONV_02")
    conv2["position"] = [3.9, 0, 0]  # 0.1 m overlap with CONV_01, joined by a connection
    sorter["connections"] = [{"from": "CONV_01", "to": "CONV_02"}]
    path = write(tmp_path, sorter)
    assert "W205" not in codes(lint_scene(path))
