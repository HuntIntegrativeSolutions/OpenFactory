"""PLAN §9 rule 13: every scene under scenes/ runs as it did, and a change to its run fails a
test that names the scene and the tick where the run first moved.

A scene with a demo kit is held by the kit's 60 s recording (``tools/build_kit.py --check`` in
CI, which now names the tick); a scene with no kit by a committed run reference,
``tests/references/<scene>.run`` (``tools/run_reference.py``)."""

from __future__ import annotations

import gzip
import importlib.util
import json
import shutil
from pathlib import Path

import pytest

from openfactory.record import first_difference, run_lines

REPO = Path(__file__).resolve().parents[2]


def _tool(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run_reference = _tool("run_reference")
build_kit = run_reference.build_kit


def test_every_scene_is_held_by_a_kit_or_a_reference():
    kits, refs = set(build_kit.kit_names()), set(run_reference.reference_names())
    scenes = set(run_reference.shipped())
    assert len(scenes) == 13
    assert scenes - kits - refs == set(), "a scene no kit or reference holds"
    assert kits & refs == set(), "a scene held twice: drop its reference, the kit holds it"
    assert (kits | refs) - scenes == set(), "a kit or reference with no scene"
    assert refs == {"accumulation-demo"}
    for ref in refs:  # a reference is text, far under the 1 MB rule
        assert (run_reference.REFERENCES / f"{ref}.run").stat().st_size < 100_000


@pytest.mark.parametrize("scene", run_reference.reference_names())
def test_a_scene_without_a_kit_runs_as_its_reference(scene):
    assert run_reference.check(scene) is None


def test_a_changed_run_names_the_scene_and_the_tick(tmp_path):
    """The check fails on a real change: the infeed belt of a copy runs at 0.6 m/s, not 0.5."""
    scene = "accumulation-demo"
    text = build_kit.source(scene).read_text(encoding="utf-8")
    assert text.count("speed_mps: 0.5\n") == 1
    changed = tmp_path / f"{scene}.yaml"
    changed.write_text(text.replace("speed_mps: 0.5\n", "speed_mps: 0.6\n"), encoding="utf-8")
    committed = (run_reference.REFERENCES / f"{scene}.run").read_text(encoding="utf-8")
    problem = run_reference.compare(scene, committed, run_reference.digest(scene, changed))
    print(problem)
    assert problem is not None and problem.startswith(f"{scene}: the run moved: it first differs")
    tick = int(problem.split("tick ")[1].split(" ")[0])
    assert 0 < tick < 3000  # within the 60 s run at dt 0.02


def test_a_stale_kit_recording_names_the_tick(tmp_path):
    """build_kit --check's message for a kit recording that moved: one frame changed at tick 500;
    a recording whose header alone differs is said to be the same run."""
    kit = REPO / "kits" / "sorting-by-height" / "sorting-by-height.ofrec"
    lines = gzip.decompress(kit.read_bytes()).splitlines()
    moved = []
    for raw in lines:
        obj = json.loads(raw)
        if obj.get("k") == 500 and "frame" in obj:
            obj["frame"]["t"] += 0.5
            raw = json.dumps(obj, separators=(",", ":")).encode()
        moved.append(raw)
    fresh = tmp_path / "moved.ofrec"
    fresh.write_bytes(gzip.compress(b"\n".join(moved) + b"\n"))
    assert build_kit.moved(kit, fresh) == ": the run first differs at tick 500 (t = 10.00 s)"
    header = json.loads(lines[0])
    header["header"]["openfactory"] = "9.9.9"
    renamed = tmp_path / "renamed.ofrec"
    renamed.write_bytes(gzip.compress(b"\n".join([json.dumps(header).encode(), *lines[1:]])))
    assert build_kit.moved(kit, renamed) == ": the header differs, the run is the same"
    shutil.copyfile(kit, tmp_path / "same.ofrec")
    assert first_difference(run_lines(kit), run_lines(tmp_path / "same.ofrec"), 0.02) is None


def test_first_difference_says_which_run_is_longer_or_where_the_summary_differs():
    a = [(0, "x"), (5, "y"), (None, "e")]
    assert first_difference(a, [(0, "x"), (5, "y"), (None, "f")], 0.02) == (
        "the run summary (the end record) differs"
    )
    assert first_difference(a, [(0, "x"), (None, "e")], 0.02) == "one run ends before tick 5"
    assert first_difference(a, [(0, "x"), (7, "y"), (None, "e")], 0.02) == (
        "first differs at tick 5 (t = 0.10 s)"
    )
    assert first_difference(a[:2], a, 0.02) == "one run has more lines, first after its end record"
