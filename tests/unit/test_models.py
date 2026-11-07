"""PLAN 4.2: the viewer's default component models (``web/public/housings/``), built by
``tools/make_models.py``. Every committed model rebuilds byte for byte, carries its license
sidecar, stays under the 1 MB rule, and is a sound GLB whose parts are named nodes; no file there
is left over from a model the tool no longer builds. The viewer side (each builder's parts exist
in its model; a default model yields to a scene mesh) is ``web/src/housings.test.ts``."""

from __future__ import annotations

import importlib.util
import json
import struct
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("make_models", REPO / "tools" / "make_models.py")
make_models = importlib.util.module_from_spec(spec)
sys.modules["make_models"] = make_models
spec.loader.exec_module(make_models)


def gltf_json(data: bytes) -> dict:
    magic, version, length = struct.unpack("<4sII", data[:12])
    assert (magic, version, length) == (b"glTF", 2, len(data))
    size, kind = struct.unpack("<I4s", data[12:20])
    assert kind == b"JSON"
    return json.loads(data[20 : 20 + size])


def test_every_model_rebuilds_byte_for_byte_with_its_sidecar():
    assert make_models.main(["--check"]) == 0, "run: uv run python tools/make_models.py"


def test_the_housings_folder_holds_only_built_models_and_their_sidecars():
    expected = set()
    for name in make_models.MODELS:
        expected |= {f"{name}.glb", f"{name}.glb.license.txt"}
    assert {p.name for p in make_models.OUT_DIR.iterdir()} == expected


def test_each_model_is_small_and_its_parts_are_named_nodes():
    for name in make_models.MODELS:
        data = (make_models.OUT_DIR / f"{name}.glb").read_bytes()
        assert len(data) < 1_000_000, name  # the 1 MB binary rule (CLAUDE.md)
        doc = gltf_json(data)
        parts = list(make_models.MODELS[name]())
        assert [n["name"] for n in doc["nodes"]] == parts
        assert doc["scenes"][0]["nodes"] == list(range(len(parts)))
        for accessor in doc["accessors"]:
            if accessor["type"] == "VEC3" and "min" in accessor:
                bounds = zip(accessor["min"], accessor["max"], strict=True)
                assert all(lo <= hi for lo, hi in bounds)
        sidecar = (make_models.OUT_DIR / f"{name}.glb.license.txt").read_text(encoding="utf-8")
        assert "License: CC-BY-4.0" in sidecar and "Author: HIS Controls" in sidecar


def test_status_colors_stay_off_the_models():
    """ISA-101: a housing is equipment grey; status colors stay on the primitives that show them."""
    status = {0x22C55E, 0x9CA3AF, 0xEF4444, 0xFACC15, 0x60A5FA}  # ignition-conventions.md
    for name in make_models.MODELS:
        for geometry in make_models.MODELS[name]().values():
            assert not set(geometry.by_color) & status, name
