"""PLAN 1.10: the openfactory CLI (run, validate, export)."""

from __future__ import annotations

import re
import socket
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openfactory.cli import app
from openfactory.core.schema import load_scene
from openfactory.export.io_list_csv import io_list_csv
from openfactory.protocols.modbus import lockfile_path

EXAMPLE = Path(__file__).resolve().parents[2] / "scenes" / "sorting-by-height.yaml"
runner = CliRunner()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def scene_copy(tmp_path) -> Path:
    """The example scene in tmp_path, so run/export write their lockfile there, not in scenes/."""
    path = tmp_path / EXAMPLE.name
    path.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    return path


# --- Done when: --help lists the three commands; validate exits 1 on a bad scene ----------------


def test_help_lists_run_validate_export(strip_ansi):
    result = subprocess.run(
        [sys.executable, "-m", "openfactory", "--help"], capture_output=True, text=True, check=True
    )
    for command in ("run", "validate", "export"):
        assert f"│ {command} " in strip_ansi(result.stdout)
    print(strip_ansi(result.stdout))


def test_validate_ok_and_bad_scene_exit_codes(tmp_path, strip_ansi):
    ok = runner.invoke(app, ["validate", str(EXAMPLE)])
    assert ok.exit_code == 0, strip_ansi(ok.output)
    assert "OK   " in strip_ansi(
        ok.output
    ) and "7 components, 11 tags, Modbus float32" in strip_ansi(ok.output)
    bad = tmp_path / "bad.yaml"
    bad.write_text(EXAMPLE.read_text(encoding="utf-8").replace("length: 4.0", "lenght: 4.0"))
    result = runner.invoke(app, ["validate", str(EXAMPLE), str(bad)])
    print(strip_ansi(result.output))
    assert result.exit_code == 1
    assert f"FAIL {bad}" in strip_ansi(result.output) and "lenght" in strip_ansi(result.output)
    assert "1 of 2 scene(s) failed" in strip_ansi(result.output)


def test_validate_directory_skips_lockfiles_and_rejects_a_lockfile(strip_ansi):
    result = runner.invoke(app, ["validate", str(EXAMPLE.parent)])
    assert result.exit_code == 0 and ".modbus.yaml" not in strip_ansi(result.output)
    lock = lockfile_path(EXAMPLE, "float32")
    result = runner.invoke(app, ["validate", str(lock)])
    assert result.exit_code == 1 and "address lockfile, not a scene" in strip_ansi(result.output)


def test_validate_reports_warnings_and_lockfile_conflicts(tmp_path, scene_copy, strip_ansi):
    two = tmp_path / "two.yaml"
    two.write_text(
        EXAMPLE.read_text(encoding="utf-8").replace(
            "position: [2.0, 0, 0.5]", "position: [4, 0, 0]"
        )
    )
    result = runner.invoke(app, ["validate", str(two)])
    assert result.exit_code == 0 and "warning: conveyors CONV_01 and CONV_02 touch" in strip_ansi(
        result.output
    )
    lock = lockfile_path(scene_copy, "float32")
    lock.write_text("encoding: int16_scaled\n", encoding="utf-8")
    result = runner.invoke(app, ["validate", str(scene_copy)])
    assert result.exit_code == 1 and "written for int16_scaled" in strip_ansi(result.output)


# --- export -----------------------------------------------------------------------------------


def test_export_writes_csv_and_lockfile(tmp_path, scene_copy, strip_ansi):
    out = tmp_path / "io.csv"
    result = runner.invoke(app, ["export", str(scene_copy), "-o", str(out)])
    assert result.exit_code == 0, strip_ansi(result.output)
    assert out.read_text(encoding="utf-8") == io_list_csv(load_scene(EXAMPLE))
    assert lockfile_path(scene_copy, "float32").is_file()
    stdout = runner.invoke(app, ["export", str(scene_copy)])
    assert strip_ansi(stdout.output).startswith("tag,io_type,dtype")


# --- run --------------------------------------------------------------------------------------


def test_run_fast_headless_prints_register_map_and_summary(scene_copy, strip_ansi):
    result = runner.invoke(
        app,
        [
            "run",
            str(scene_copy),
            "--as-fast-as-possible",
            "--duration",
            "10",
            "--port",
            str(free_port()),
            "--force",
            "CONV_01_RUN_DO=1",
            "--force",
            "EMT_01_EN_DO=true",
            "--force",
            "CONV_01_SPD_AO=100",
        ],
    )
    out = strip_ansi(result.output)
    assert result.exit_code == 0, out
    # the register map is printed at the default (INFO) log level
    assert "Modbus register map (float32, zero-based addresses):" in out
    assert "  CO:0       CONV_01_RUN_DO" in out
    assert "Modbus TCP listening on 127.0.0.1:" in out
    assert "forced CONV_01_RUN_DO = True" in out
    assert "ran 500 ticks (10.00 s simulated)" in out
    assert "viewer:" not in out  # fast mode implies headless
    assert lockfile_path(scene_copy, "float32").is_file()


def test_run_quiet_hides_the_map(scene_copy, strip_ansi):
    result = runner.invoke(
        app,
        [
            "run",
            str(scene_copy),
            "-q",
            "--as-fast-as-possible",
            "--duration",
            "0.1",
            "--port",
            str(free_port()),
        ],
    )
    assert result.exit_code == 0 and "register map" not in strip_ansi(result.output)


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--as-fast-as-possible"], "--as-fast-as-possible needs --duration"),
        (["--duration", "0", "--force", "PE_01_DI=1"], "only PLC-written DO/AO tags"),
        (["--duration", "0", "--force", "NOPE_DO=1"], "unknown tag 'NOPE_DO'"),
        (["--duration", "0", "--force", "CONV_01_RUN_DO=maybe"], "DO value must be 1/0"),
        (["--duration", "0", "--force", "CONV_01_RUN_DO"], "expected TAG=VALUE"),
    ],
)
def test_run_rejects_bad_options(args, message, strip_ansi):
    result = runner.invoke(app, ["run", str(EXAMPLE), "--headless", *args])
    assert result.exit_code == 2
    flat = " ".join(
        re.sub(r"[│╭╮╰╯─]", " ", strip_ansi(result.output)).split()
    )  # unwrap Rich's error panel
    assert message in flat


def test_run_bad_scene_exits_1(tmp_path, strip_ansi):
    bad = tmp_path / "bad.yaml"
    bad.write_text("openfactory: '0.1'\n")
    result = runner.invoke(app, ["run", str(bad), "--headless", "--duration", "0"])
    assert result.exit_code == 1 and "error:" in strip_ansi(result.output)


def test_run_with_analog_encoding_selects_its_own_lockfile(scene_copy, strip_ansi):
    """--analog-encoding int16_scaled writes <scene>.int16_scaled.modbus.yaml; float32 untouched."""
    runner.invoke(app, ["export", str(scene_copy), "-o", str(scene_copy.with_suffix(".csv"))])
    f32 = lockfile_path(scene_copy, "float32")
    f32_text = f32.read_text(encoding="utf-8")
    args = ["run", str(scene_copy), "--as-fast-as-possible", "--duration", "1"]
    result = runner.invoke(
        app, [*args, "--port", str(free_port()), "--analog-encoding", "int16_scaled"]
    )
    assert result.exit_code == 0, strip_ansi(result.output)
    assert "Modbus register map (int16_scaled, zero-based addresses):" in strip_ansi(result.output)
    assert "  HR:0       CONV_01_SPD_AO" in strip_ansi(result.output)
    assert lockfile_path(scene_copy, "int16_scaled").is_file()
    assert f32.read_text(encoding="utf-8") == f32_text
    bad = runner.invoke(app, [*args, "--analog-encoding", "float64"])
    assert bad.exit_code != 0 and "float32, int16_scaled" in re.sub(
        r"[\s│]+", " ", strip_ansi(bad.output)
    )
    csv = runner.invoke(app, ["export", str(scene_copy), "--analog-encoding", "int16_scaled"])
    assert "CONV_01_SPD_AO" in strip_ansi(
        csv.output
    ) and ",HR:0,int16_scaled 0-32767," in strip_ansi(csv.output)
    validated = runner.invoke(app, ["validate", str(scene_copy)])
    assert validated.exit_code == 0 and "Modbus float32, int16_scaled" in strip_ansi(
        validated.output
    )
