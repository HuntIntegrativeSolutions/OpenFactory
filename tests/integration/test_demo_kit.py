"""PLAN 2.14: the sorter's demo kit is current (``tools/build_kit.py --check``), the recording in
it replays with no scene file, and its exports agree with each other."""

from __future__ import annotations

import csv
import importlib.util
import io
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from openfactory.record import read_recording

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("build_kit", REPO / "tools" / "build_kit.py")
build_kit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_kit)

KIT = REPO / "kits" / "sorting-by-height"


def test_the_sorter_kit_is_current():
    problems = build_kit.check("sorting-by-height")
    assert problems == [], problems


def test_the_kit_files_agree():
    rows = list(csv.DictReader(io.StringIO((KIT / "sorting-by-height-io.csv").read_text("utf-8"))))
    root = ET.fromstring((KIT / "sorting-by-height-tags.L5X").read_bytes())
    l5x = [t.get("Name") for t in root.findall("./Controller/Tags/Tag")]
    assert l5x == [r["tag"] for r in rows] and len(l5x) == 12
    doc = json.loads((KIT / "sorting-by-height-ignition-tags.json").read_text("utf-8"))
    named = {
        p["value"]
        for inst in doc["tags"][1]["tags"]
        if inst["tagType"] == "UdtInstance"
        for p in inst["parameters"].values()
    }
    assert set(l5x) - named == {"SIM_HEARTBEAT_DI"}  # the heartbeat is its own atomic tag
    rec = read_recording(KIT / "sorting-by-height.ofrec")
    assert not rec.truncated and rec.end["removed"] == {"short": 9, "tall": 9}
    assert rec.header["scene_yaml"] == (KIT / "sorting-by-height.yaml").read_text("utf-8")
    assert (KIT / "sorting-by-height.ofrec").stat().st_size < 1_000_000  # the repo's binary rule
    readme = (KIT / "README.md").read_text("utf-8")
    assert "pending PE review" in readme and "missing" not in readme  # ruled 2026-10-31
    assert "### Needs Designer load before client use" in readme


def test_a_kit_carries_the_library_composites_its_scene_names(tmp_path):
    """PLAN 2.5b and 2.14: a kit built from the accumulation example holds
    ``_library/accumulation_conveyor.yaml`` beside its scene, so it loads, exports and passes its
    checks with no ``scenes/`` folder above it (``tmp_path`` is outside the repository)."""
    import subprocess
    import sys

    kit = tmp_path / "accumulation-demo"
    build_kit.build("accumulation-demo", kit)  # exports and the recording run on kit copies
    lib = REPO / "scenes" / "_library" / "accumulation_conveyor.yaml"
    assert (kit / "_library" / lib.name).read_bytes() == lib.read_bytes()
    names = build_kit.generated("accumulation-demo")
    assert "_library/accumulation_conveyor.yaml" in names and "accumulation-demo.st" not in names
    assert sorted(p.relative_to(kit).as_posix() for p in kit.rglob("*") if p.is_file()) == sorted(
        names
    )
    readme = (kit / "README.md").read_text("utf-8")
    assert "| `_library/accumulation_conveyor.yaml` |" in readme and "OpenPLC" not in readme
    done = subprocess.run(
        [sys.executable, "-m", "openfactory", "test", str(kit / "accumulation-demo.yaml"),
         "--script", str(kit / "accumulation-demo.py")],
        cwd=tmp_path, capture_output=True, text=True,
    )  # fmt: skip
    assert done.returncode == 0 and "PASS: 2 of 2 checks passed" in done.stdout, done.stdout
    rec = read_recording(kit / "accumulation-demo.ofrec")
    assert not rec.truncated and rec.end["removed"]
    (kit / "_library" / lib.name).unlink()  # without it the kit's scene does not load
    done = subprocess.run(
        [sys.executable, "-m", "openfactory", "validate", str(kit / "accumulation-demo.yaml")],
        cwd=tmp_path, capture_output=True, text=True,
    )  # fmt: skip
    assert (
        done.returncode == 1
        and "no _library/accumulation_conveyor.yaml" in done.stdout + done.stderr
    )
