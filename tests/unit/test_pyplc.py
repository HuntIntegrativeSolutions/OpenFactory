"""PLAN 2.10: scripted stand-in PLC (`openfactory run --script`, openfactory.pyplc)."""

from __future__ import annotations

import re
import socket
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openfactory.cli import app
from openfactory.core.iotable import TagWriteError
from openfactory.core.loader import load
from openfactory.pyplc import R_TRIG, TON, PlcIO, load_script

REPO = Path(__file__).resolve().parents[2]
SCENE = REPO / "scenes" / "sorting-by-height.yaml"
SCRIPT = REPO / "examples" / "pyplc" / "sorting-by-height.py"
ST = REPO / "examples" / "openplc" / "sorting-by-height.st"
TALL_PAIRS = REPO / "tests" / "scenes" / "sorter-tall-pairs.yaml"
runner = CliRunner()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def scene_copy(tmp_path: Path) -> Path:
    path = tmp_path / SCENE.name
    path.write_text(SCENE.read_text(encoding="utf-8"), encoding="utf-8")
    return path


def test_plc_io_obeys_the_writer_side_rules():
    io = PlcIO(load(SCENE).io)
    io["CONV_01_RUN_DO"] = True
    io["CONV_01_SPD_AO"] = 40.0
    assert io["CONV_01_RUN_DO"] is True and io["CONV_01_SPD_AO"] == 40.0
    with pytest.raises(TagWriteError):
        io["PE_01_DI"] = True  # a PLC cannot write a sensor, as over Modbus
    with pytest.raises(KeyError, match="NOPE_DO"):
        io["NOPE_DO"] = True
    with pytest.raises(KeyError, match="NOPE_DI"):
        _ = io["NOPE_DI"]


def test_ton_and_r_trig_follow_iec_semantics():
    ton, trig = TON(0.1), R_TRIG()
    assert [ton(True, t / 100) for t in range(12)] == [False] * 10 + [True, True]
    assert ton(False, 0.2) is False and ton.et_s == 0.0
    assert [trig(c) for c in (False, True, True, False, True)] == [False, True, False, False, True]


def test_sorter_runs_60_s_under_the_script_with_the_1_14_result(tmp_path, strip_ansi):
    result = runner.invoke(
        app,
        [
            "run", str(scene_copy(tmp_path)), "--as-fast-as-possible", "--duration", "60",
            "--port", str(free_port()), "--script", str(SCRIPT),
        ],
    )  # fmt: skip
    out = strip_ansi(result.output)
    assert result.exit_code == 0, out
    summary = next(line for line in out.splitlines() if "ran 3000 ticks" in line)
    print(summary)
    # 1.14 under OpenPLC: spawned 20, REM_01 {short: 9}, REM_02 {tall: 9}, lost {}
    assert "spawned 20" in summary and "lost {}" in summary
    assert "by remover {'REM_01': {'short': 9}, 'REM_02': {'tall': 9}}" in summary
    assert "scripted PLC:" in out and "would fight it" in out  # warns about a Modbus PLC


def test_every_tall_box_is_diverted_when_two_are_in_transit(tmp_path, strip_ansi):
    """Boxes 1.5 s apart, under the divert delay: each needs its own timer (the divert ring)."""
    for name in (TALL_PAIRS.name, "sorter-tall-pairs.float32.modbus.yaml"):
        (tmp_path / name).write_text((TALL_PAIRS.parent / name).read_text(encoding="utf-8"))
    result = runner.invoke(
        app,
        [
            "run", str(tmp_path / TALL_PAIRS.name), "--as-fast-as-possible", "--duration", "60",
            "--port", str(free_port()), "--script", str(SCRIPT),
        ],
    )  # fmt: skip
    out = strip_ansi(result.output)
    assert result.exit_code == 0, out
    summary = next(line for line in out.splitlines() if "ran 3000 ticks" in line)
    print(summary)
    # One shared flag and timer sent 17 of these to REM_01 (OpenPLC and script alike).
    assert "by remover {'REM_01': {}, 'REM_02': {'tall': 35}}" in summary and "lost {}" in summary


def test_a_faulting_script_stops_the_run_with_exit_1(tmp_path, strip_ansi):
    bad = tmp_path / "bad.py"
    bad.write_text("def step(io, t):\n    if t > 0.5:\n        io['PE_01_DI'] = True\n")
    result = runner.invoke(
        app,
        [
            "run", str(scene_copy(tmp_path)), "--as-fast-as-possible", "--duration", "10",
            "--port", str(free_port()), "--script", str(bad),
        ],
    )  # fmt: skip
    out = strip_ansi(result.output)
    assert result.exit_code == 1
    assert re.search(r"faulted at t = 0\.5[02] s", out) and "TagWriteError" in out
    assert "ran 500 ticks" not in out


def test_a_script_without_step_is_rejected(tmp_path, strip_ansi):
    empty = tmp_path / "empty.py"
    empty.write_text("X = 1\n")
    result = runner.invoke(app, ["run", str(scene_copy(tmp_path)), "--headless", "--duration",
                                 "0", "--script", str(empty)])  # fmt: skip
    assert result.exit_code == 1 and "must define step(io, t)" in strip_ansi(result.output)


def test_script_stays_in_step_with_the_openplc_program():
    """Same constants and the same tags as examples/openplc/sorting-by-height.st."""
    st = ST.read_text(encoding="utf-8")
    script = load_script(SCRIPT)
    ms = {name: int(v) for name, v in re.findall(r"(\w+) : TIME := T#(\d+)ms", st)}
    s = {name: int(v) for name, v in re.findall(r"(\w+) : TIME := T#(\d+)s", st)}
    assert ms["DIVERT_DELAY"] / 1000 == pytest.approx(
        script.DIVERT_DELAY_S - script.OPENPLC_POLL_LATENCY_S
    )
    assert s["BELTS_OK_DELAY"] == script.BELTS_OK_DELAY_S
    assert len(re.findall(r"DIVERT_TMR_\d+ : TON;", st)) == script.DIVERT_SLOTS
    assert f"MOD {script.DIVERT_SLOTS};" in st
    raw = int(re.search(r"SPEED_RAW : INT := (\d+)", st).group(1))
    assert raw / 32767 * 100 == script.SPEED_PCT
    logic = st.split("END_VAR")[-1]  # the program body after the declarations
    st_tags = set(re.findall(r"\b([A-Z][A-Z0-9_]*_(?:DI|DO|AO))\b", logic))
    py_tags = set(re.findall(r'io\["([A-Z0-9_]+)"\]', SCRIPT.read_text(encoding="utf-8")))
    assert st_tags == py_tags
