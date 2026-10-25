"""PLAN 1.13: tools/screenshot.py end to end: server, headless Chromium, non-blank PNG."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "tools" / "screenshot.py"
spec = importlib.util.spec_from_file_location("screenshot_tool", TOOL)
screenshot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(screenshot)


@pytest.fixture
def scene(tmp_path) -> Path:
    """A copy of the shipped scene: `openfactory run` writes its lockfile next to the scene file."""
    path = tmp_path / "sorting-by-height.yaml"
    path.write_text((REPO / "scenes" / path.name).read_text(encoding="utf-8"), encoding="utf-8")
    return path


pytestmark = pytest.mark.skipif(
    not (REPO / "web" / "dist" / "index.html").is_file(), reason="viewer not built (npm run build)"
)


def test_screenshot_of_running_sorter_is_not_blank(tmp_path, scene, capsys):
    out = tmp_path / "sorter.png"
    code = screenshot.main(
        [
            str(scene),
            "--seconds", "1.5",
            "--out", str(out),
            "--force", "CONV_01_RUN_DO=1",
            "--force", "EMT_01_EN_DO=1",
        ]
    )  # fmt: skip
    printed = capsys.readouterr().out
    print(printed)
    assert code == 0, printed
    assert out.is_file()
    assert "viewer: scene sorting-by-height, 7 components" in printed
    assert "OK: 1280x720" in printed


def test_viewer_physics_carries_a_box_and_its_photo_eye_event_reaches_the_core(
    tmp_path, scene, capsys
):
    """PLAN 1.12 Done-when: coil forced, a box rides the belt in Rapier, the PE_01 event arrives."""
    out = tmp_path / "physics.png"
    code = screenshot.main(
        [
            str(scene),
            "--seconds", "5",
            "--min-sensor-events", "1",
            "--out", str(out),
            "--force", "CONV_01_RUN_DO=1",
            "--force", "CONV_01_SPD_AO=100",
            "--force", "EMT_01_EN_DO=1",
        ]
    )  # fmt: skip
    printed = capsys.readouterr().out
    print(printed)
    assert code == 0, printed
    assert "viewer physics: rapier 0.21.0, sensor events sent: " in printed
    assert "'sensor:PE_01': " in printed  # the core's /api/status counted the viewer's event
