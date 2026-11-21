"""PLAN 5.5, review of 2026-11-20 (gap 2a): a device mounted on a belt that layout placed is
placed by scaffold through ``place_on_rail``; running ``openfactory layout`` on that scene
again must change no byte. Every type ``place_on_rail`` accepts, on each rail, and the types
whose origin sits on the belt's centre (a photo eye, a colour sensor, a stopper, a distance
sensor looking down)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from openfactory import takeoff as tk
from openfactory.core.mounts import MOUNT_TYPES
from openfactory.layout import relayout_scene
from openfactory.scaffold import scaffold

#: The parameters each type needs, and its required I/O port and kind.
NEEDS = {
    "photo_eye": ({"beam_length": 0.6}, ("detect", "DI")),
    "distance_sensor": ({}, ("distance", "AI")),
    "color_sensor": ({"teach": "red"}, ("match", "DI")),
    "pusher": ({"stroke": 0.6, "extend_time_s": 0.4}, ("extend", "DO")),
    "stopper": ({}, ("raise", "DO")),
    "diverter_arm": ({"length": 0.4}, ("extend", "DO")),
}
CASES = [(t, r, {}) for t in MOUNT_TYPES for r in ("left", "right")]
CASES += [("distance_sensor", r, {"axis": "-y"}) for r in ("left", "right")]  # on the centre


def takeoff(tmp_path: Path, kind: str, rail: str, extra: dict) -> Path:
    params, (port, signal) = NEEDS[kind]
    data = {
        "takeoff": "0.1",
        "plant": {"name": "mounts", "description": "A laid-out belt with a mounted device."},
        "drawings": [{"id": "L-1", "kind": "layout", "title": "Layout", "rev": "A",
                      "frame": {"units": "mm", "datum": [0, 0, 0], "rotation_deg": 0}}],
        "equipment": [
            {"row": "EQ-1", "tag": "CV-1", "type": "belt_conveyor", "description": "Belt",
             "params": {"length": 3.0, "width": 0.5, "speed_mps": 0.4},
             "confidence": "read", "questions": []},
            {"row": "EQ-2", "tag": "DV-1", "type": kind, "description": "Device",
             "params": {**params, **extra},
             "mount": {"conveyor": "CV-1", "sheet": "L-1", "along": 1200, "rail": rail},
             "confidence": "read", "questions": []},
        ],
        "io": [
            {"row": "IO-1", "equipment": "CV-1", "port": "run", "signal": "DO",
             "confidence": "read", "questions": []},
            {"row": "IO-2", "equipment": "DV-1", "port": port, "signal": signal,
             "confidence": "read", "questions": []},
        ],
    }  # fmt: skip
    path = tmp_path / "m.takeoff.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


@pytest.mark.parametrize(("kind", "rail", "extra"), CASES,
                         ids=[f"{k}-{r}{'-down' if e else ''}" for k, r, e in CASES])  # fmt: skip
def test_layout_again_changes_no_byte_of_a_laid_out_belts_mounted_device(
    tmp_path, kind, rail, extra
):
    text = scaffold(tk.load(takeoff(tmp_path, kind, rail, extra))).text
    scene = tmp_path / "m.yaml"
    scene.write_text(text, encoding="utf-8")
    again, moved, _ = relayout_scene(text, tmp_path)
    if again != text:
        import difflib

        print("".join(difflib.unified_diff(text.splitlines(True), again.splitlines(True))))
    assert again == text
    assert "DV_1" in moved  # it was laid out (it rests on a laid-out belt) and placed again
