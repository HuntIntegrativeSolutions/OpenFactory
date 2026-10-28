"""PLAN 2.11: exercise checks. The schema validates what a `checks:` block names, `evaluate`
grades a run, and `openfactory test` prints the grade and exits 1 on a failure."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from typer.testing import CliRunner

from openfactory.checks import evaluate
from openfactory.cli import app
from openfactory.core.iotable import Writer
from openfactory.core.loader import build
from openfactory.core.schema import Scene

REPO = Path(__file__).resolve().parents[2]
SORTER = REPO / "scenes" / "sorting-by-height.yaml"
SCRIPT = REPO / "examples" / "pyplc" / "sorting-by-height.py"
runner = CliRunner()
CHECKS = {
    "parts_reached": {"REM_01": "short", "REM_02": ["tall"]},
    "none_lost": True,
    "max_cycle_s": 12,
}


def sorter(checks: dict | None) -> dict:
    doc = yaml.safe_load(SORTER.read_text(encoding="utf-8"))
    doc.pop("checks", None)  # the shipped sorter's own; each test gives its own or none
    if checks is not None:
        doc["checks"] = checks
    return doc


def scene_file(tmp_path: Path, checks: dict | None) -> Path:
    path = tmp_path / SORTER.name
    path.write_text(yaml.safe_dump(sorter(checks), sort_keys=False), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("checks", "message"),
    [
        ({"parts_reached": {"REM_09": "tall"}}, "'REM_09' is not in the scene, not a part_remover"),
        (
            {"parts_reached": {"CONV_01": "tall"}},
            "'CONV_01' is a belt_conveyor, not a part_remover",
        ),
        ({"parts_reached": {"REM_02": "talll"}}, "no box_emitter makes a part named 'talll'"),
        (
            {"parts_reached": {"REM_01": "tall", "REM_02": "tall"}},
            "'tall' is listed for both 'REM_01' and 'REM_02'",
        ),
        ({"max_cycle_s": 0}, "greater than 0"),
        ({"none_lost": True, "grade": "A"}, "Extra inputs are not permitted"),
    ],
)
def test_a_checks_block_naming_the_wrong_thing_fails_at_load(checks, message):
    with pytest.raises(ValidationError, match=message):
        Scene.model_validate(sorter(checks))


def run(checks: dict, ticks: int, *, belts: bool = True, extend: bool = False):
    loaded = build(Scene.model_validate(sorter(checks)))
    for tag in ("CONV_01_RUN_DO", "CONV_02_RUN_DO", "EMT_01_EN_DO"):
        loaded.io.set(tag, belts if tag != "EMT_01_EN_DO" else True, Writer.PLC)
    loaded.io.set("CONV_01_SPD_AO", 100.0, Writer.PLC)
    loaded.io.set("CONV_02_SPD_AO", 100.0, Writer.PLC)
    loaded.io.set("CYL_01_EXT_DO", extend, Writer.PLC)
    for _ in range(ticks):
        loaded.step(0.02)
    return {r.name: r for r in evaluate(Scene.model_validate(sorter(checks)).checks, loaded)}


def test_with_no_divert_tall_boxes_reach_the_wrong_remover():
    got = run(CHECKS, 3000)
    for r in got.values():
        print(f"{'PASS' if r.passed else 'FAIL'} {r.name}: {r.detail}")
    assert got["parts_reached REM_01: short"].passed
    tall = got["parts_reached REM_02: tall"]
    assert not tall.passed and tall.detail.startswith("0 reached REM_02, but 9 to REM_01")
    assert got["none_lost"].passed and got["max_cycle_s 12"].passed


def test_a_stopped_line_fails_parts_reached_and_max_cycle():
    got = run(CHECKS, 3000, belts=False)  # boxes are spawned, but nothing moves
    print({n: r.detail for n, r in got.items()})
    assert got["parts_reached REM_01: short"].detail == "no short part reached any remover"
    assert not got["parts_reached REM_02: tall"].passed
    cycle = got["max_cycle_s 12"]
    assert not cycle.passed and "over 0 parts" in cycle.detail  # failed on the waiting part


def test_cli_test_passes_the_sorter_under_its_script(tmp_path):
    result = runner.invoke(
        app, ["test", str(scene_file(tmp_path, CHECKS)), "--script", str(SCRIPT)]
    )
    print(result.output)
    assert result.exit_code == 0
    assert "PASS: 4 of 4 checks passed" in result.output


def test_cli_test_exits_1_on_a_failed_check(tmp_path):
    wrong = {**CHECKS, "max_cycle_s": 5}
    result = runner.invoke(app, ["test", str(scene_file(tmp_path, wrong)), "--script", str(SCRIPT)])
    print(result.output)
    assert result.exit_code == 1
    assert "FAIL  max_cycle_s 5: longest spawn-to-remover 7.70 s" in result.output
    assert "FAIL: 3 of 4 checks passed" in result.output


def test_cli_test_needs_checks_and_exactly_one_controller(tmp_path):
    bare = runner.invoke(app, ["test", str(scene_file(tmp_path, None)), "--script", str(SCRIPT)])
    assert bare.exit_code == 1 and "has no checks: block" in bare.output
    both = runner.invoke(app, ["test", str(SORTER), "--script", str(SCRIPT), "--plc"])
    neither = runner.invoke(app, ["test", str(SORTER)])
    for result in (both, neither):
        assert result.exit_code == 2 and "exactly one of --script" in result.output
