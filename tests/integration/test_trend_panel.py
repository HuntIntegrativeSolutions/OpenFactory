"""PLAN 3.6: the viewer's trend panel. Opened with ?trend=TAG on a running tank-level-pid, it
draws the level from GET /api/history, and offers only the scene's AI tags."""

from __future__ import annotations

import contextlib
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "tools" / "screenshot.py"
spec = importlib.util.spec_from_file_location("screenshot_tool", TOOL)
screenshot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(screenshot)

PID = REPO / "scenes" / "tank-level-pid.yaml"
SCRIPT = REPO / "examples" / "pyplc" / "tank-level-pid.py"


@pytest.fixture(autouse=True)
def _viewer_built(prerequisite):
    prerequisite(
        (REPO / "web" / "dist" / "index.html").is_file(), "viewer not built (npm run build)"
    )


def test_the_trend_panel_draws_the_pid_level_from_the_history_endpoint(tmp_path):
    from playwright.sync_api import sync_playwright

    scene = tmp_path / PID.name  # `run` writes lockfiles next to the scene: run a copy
    scene.write_text(PID.read_text(encoding="utf-8"), encoding="utf-8")
    web_port = screenshot.free_port()
    cmd = [
        sys.executable, "-m", "openfactory", "run", str(scene), "-q", "--script", str(SCRIPT),
        "--port", str(screenshot.free_port()), "--web-port", str(web_port),
    ]  # fmt: skip
    proc = subprocess.Popen(cmd, cwd=REPO, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        screenshot.wait_for_api(web_port, proc, timeout=60)  # a hang guard, not a speed check
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=screenshot.CHROMIUM_ARGS)
            page = browser.new_page(viewport={"width": 1280, "height": 720})
            page.goto(f"http://127.0.0.1:{web_port}/?trend=TK_01_LT_AI")
            page.wait_for_function("() => window.__openfactory && window.__openfactory.ready")
            # a few seconds of history: the core samples at 2 Hz of simulated time
            page.wait_for_function(
                "() => (window.__openfactoryTrend.last?.t.length ?? 0) >= 4", timeout=60_000
            )
            assert page.locator("#trend-panel").is_visible()
            assert page.locator("#trend-button").is_visible()
            options = page.locator("#trend-panel select option").all_inner_texts()
            assert [o.split(":")[0] for o in options] == ["FV_01_ZT_AI", "TK_01_LT_AI"]
            last = page.evaluate("() => window.__openfactoryTrend.last")
            assert last["tag"] == "TK_01_LT_AI" and last["units"] == "m"
            assert all(0.9 < v < 1.1 for v in last["v"][2:])  # the level held near 1.0 m
            points = page.locator("#trend-panel polyline").get_attribute("points")
            assert len(points.split()) == len(last["t"])
            page.screenshot(path=str(tmp_path / "trend.png"))
            page.locator("#trend-button").click()
            assert not page.locator("#trend-panel").is_visible()
            browser.close()
    finally:
        proc.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(10)
        if proc.poll() is None:
            proc.kill()
