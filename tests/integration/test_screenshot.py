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

pytestmark = pytest.mark.skipif(
    not (REPO / "web" / "dist" / "index.html").is_file(), reason="viewer not built (npm run build)"
)


def test_screenshot_of_running_sorter_is_not_blank(tmp_path, capsys):
    out = tmp_path / "sorter.png"
    code = screenshot.main(
        [
            str(REPO / "scenes" / "sorting-by-height.yaml"),
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
