"""PLAN 5.14i: the program's own state. A stand-in script publishes watch values
(``io.watch("step", 20)``); they show in ``look``, in a recording's frames, in ``explain`` and
(``?labels=tags``) on the viewer's status lines. They are not tags: no address, no export, and
the sorter's lockfiles and I/O CSV are byte for byte what they were. Watch values are data:
names follow a pattern, values are capped and shown as text, and none reaches a path or a tag
name. A script that publishes none runs exactly as before (its snapshot has no ``watch``)."""

from __future__ import annotations

import math
import subprocess
import sys
from pathlib import Path

import pytest

from openfactory.core.loader import load
from openfactory.explain import explain
from openfactory.observe import look
from openfactory.pyplc import MAX_WATCH_VALUE, MAX_WATCHES, PlcIO, ScriptPlc
from openfactory.record import Recorder, read_recording, recording_header
from openfactory.server.runtime import Runtime

REPO = Path(__file__).resolve().parents[2]
SORTER = REPO / "scenes" / "sorting-by-height.yaml"
SCRIPT = REPO / "examples" / "pyplc" / "sorting-by-height.py"
KIT = REPO / "kits" / "sorting-by-height"


def sorter(script: Path = SCRIPT) -> Runtime:
    loaded = load(SORTER)
    rt = Runtime(loaded, realtime=False, controller=ScriptPlc(script, loaded.io))
    rt.recording_header = recording_header(rt, SORTER.read_text("utf-8"), None)
    return rt


def run(rt: Runtime, ticks: int) -> None:
    for _ in range(ticks):
        rt._step(rt.scheduler.dt)


def test_the_sorters_script_publishes_its_step_and_look_prints_it():
    rt = sorter()
    run(rt, 500)
    snap = rt.snapshot
    assert set(snap["watch"]) == {"step", "boxes_in_transit"}
    assert snap["watch"]["step"] in (10, 20, 30, 40)
    text = rt.look()
    section = text.split("## Program (watch values)\n\n", 1)[1].split("\n\n## ", 1)[0]
    print(section)
    assert f"- step = {snap['watch']['step']}" in section
    assert "not tags" in section
    seen = set()
    for _ in range(600):  # the sequence goes through its steps
        run(rt, 5)
        seen.add(rt.snapshot["watch"]["step"])
    assert {20, 30, 40} <= seen


def test_watch_values_are_in_the_recording_and_in_explain(tmp_path):
    rt = sorter()
    rt.recorder = Recorder(tmp_path / "w.ofrec", rt.recording_header, rt.snapshot)
    run(rt, 700)
    rt.recorder.close({})
    rec = read_recording(tmp_path / "w.ofrec")
    assert all("watch" in f for k, f in rec.frames if k > 0)
    story = explain(rec, part="P000002")
    lines = [s for s in story.steps if s.text.startswith("the program's step went")]
    print("\n".join(s.text for s in lines))
    assert lines and all("watch values in the frames" in s.basis for s in lines)
    assert "the program's step went 20 to 30" in story.text  # the tall box arrives at the eye


def test_they_are_not_tags_the_lockfiles_and_the_io_csv_do_not_move(tmp_path):
    """Exported from a copy of the scene and its lockfiles in the test's folder (as the kit
    builder does), the I/O CSVs equal the kit's and the lockfiles are unchanged, byte for byte."""
    import shutil

    locks = ("sorting-by-height.float32.modbus.yaml", "sorting-by-height.int16_scaled.modbus.yaml")
    for name in ("sorting-by-height.yaml", *locks):
        shutil.copyfile(REPO / "scenes" / name, tmp_path / name)
    for encoding, suffix in (("float32", ""), ("int16_scaled", "-int16")):
        csv = tmp_path / f"sorting-by-height-io{suffix}.csv"
        done = subprocess.run(
            [sys.executable, "-m", "openfactory", "export",
             str(tmp_path / "sorting-by-height.yaml"), "--analog-encoding", encoding,
             "-o", str(csv)],
            capture_output=True, text=True, cwd=tmp_path,
        )  # fmt: skip
        assert done.returncode == 0, done.stderr
        assert csv.read_bytes() == (KIT / csv.name).read_bytes(), csv.name
    for name in locks:
        assert (tmp_path / name).read_bytes() == (REPO / "scenes" / name).read_bytes(), name
    rt = sorter()
    run(rt, 300)
    tags = {t.name for t in rt.loaded.io.tags()}
    assert rt.snapshot["watch"] and not tags & set(rt.snapshot["watch"])


def test_watch_values_are_data(tmp_path):
    from openfactory.core.iotable import IOTable

    io = PlcIO(IOTable())
    io.watch("step", 20)
    io.watch("state.mode", "  running  ")
    io.watch("ratio", math.nan)
    io.watch("big", "x" * 10_000)
    io.watch("ctl", "a\x00b\x1b[31mc\nd")
    io.watch("obj", object())
    assert io.watches["step"] == 20 and io.watches["state.mode"] == "  running  "
    assert io.watches["ratio"] == "nan"  # a recording's JSON allows no NaN
    assert len(io.watches["big"]) == MAX_WATCH_VALUE
    assert io.watches["ctl"] == "ab[31mc d"
    assert io.watches["obj"].startswith("<object object")
    for bad in ("../etc/passwd", "a b", "", "x" * 41, "1step", "a/b", "CONV_01_RUN_DO\n", 7):
        with pytest.raises((ValueError, TypeError)):
            io.watch(bad, 1)  # type: ignore[arg-type]
    for i in range(MAX_WATCHES - len(io.watches)):
        io.watch(f"w{i}", i)
    with pytest.raises(ValueError, match=f"at most {MAX_WATCHES}"):
        io.watch("one_more", 1)
    io.watch("step", 30)  # an existing name may always change
    assert io.watches["step"] == 30


def test_a_watch_value_is_shown_as_text_in_look():
    document = {
        "dt": 0.02, "scene": {"name": "s", "description": ""}, "components": [], "tags": [],
    }  # fmt: skip
    snap = {
        "t": 1.0, "tick": 50, "components": {}, "parts": [], "io": {},
        "watch": {"note": "# Heading <script>alert(1)</script> `x` [a](b)"},
    }  # fmt: skip
    text = look(document, snap, [])
    assert "<script>" not in text and "\n# Heading" not in text and "](" not in text
    assert "- note = “" in text


def test_a_script_that_publishes_none_has_no_watch_in_its_snapshot(tmp_path):
    script = tmp_path / "plain.py"
    script.write_text("def step(io, t):\n    io['CONV_01_RUN_DO'] = True\n", encoding="utf-8")
    rt = sorter(script)
    run(rt, 20)
    assert "watch" not in rt.snapshot and "## Program" not in rt.look()
