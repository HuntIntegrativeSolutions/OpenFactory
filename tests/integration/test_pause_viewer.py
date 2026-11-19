"""PLAN 5.14h in the browser: Space pauses the sorter's live run (served on Modbus TCP, so the
banner says the PLC keeps running), every viewer of the run shows the banner, the tick holds,
Step runs 0.1 s (5 ticks), Space typed in a text field does nothing, and Space resumes. The
picture goes to ``$OPENFACTORY_PAUSE_OUT`` (else the test's folder) to be looked at."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("screenshot_tool", REPO / "tools" / "screenshot.py")
screenshot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(screenshot)
SORTER = REPO / "scenes" / "sorting-by-height.yaml"
SCRIPT = REPO / "examples" / "pyplc" / "sorting-by-height.py"


@pytest.fixture(autouse=True)
def _viewer_built(prerequisite):
    prerequisite(
        (REPO / "web" / "dist" / "index.html").is_file(), "viewer not built (npm run build)"
    )


def test_space_pauses_every_viewer_shows_the_banner_and_step_runs_a_tenth(tmp_path):
    with screenshot.running_viewer(SORTER, forces=[], script=SCRIPT) as (page, port, console):
        screenshot.wait_for_viewer(page, console, seconds=3.0, timeout=90)
        assert screenshot.get_json(port, "/api/pause")["protocols"] == ["Modbus TCP"]
        page.keyboard.press("Space")
        page.wait_for_function("() => window.__openfactory.simPaused === true", timeout=10_000)
        banner = page.locator("#pause-banner")
        assert banner.is_visible()
        assert "Simulation paused, PLC still running (Modbus TCP)" in banner.inner_text()
        assert page.locator("#pause").inner_text() == "Resume"
        # every viewer of the run shows it
        other = page.context.browser.new_page()
        other.goto(f"http://127.0.0.1:{port}/?quality=low")
        other.wait_for_function("() => window.__openfactory.simPaused === true", timeout=30_000)
        assert other.locator("#pause-banner").is_visible()
        other.close()
        held = screenshot.get_json(port, "/api/pause")["tick"]
        page.wait_for_timeout(1000)
        assert screenshot.get_json(port, "/api/state")["tick"] == held
        page.screenshot(
            path=str(Path(os.environ.get("OPENFACTORY_PAUSE_OUT", tmp_path)) / "paused.png")
        )
        page.locator("#step").click()
        page.wait_for_function(
            "t => (window.__openfactory.pauseAnswer || {}).tick === t", arg=held + 5
        )
        assert screenshot.get_json(port, "/api/state")["tick"] == held + 5
        # Space in a text field is text, not a pause
        page.evaluate(
            "() => { const i = document.createElement('input'); i.id = 'f';"
            " document.body.append(i); }"
        )
        page.locator("#f").click(force=True)
        page.keyboard.press("Space")
        page.wait_for_timeout(300)
        assert screenshot.get_json(port, "/api/pause")["paused"] is True
        page.evaluate("() => document.activeElement.blur()")
        page.keyboard.press("Space")
        page.wait_for_function("() => window.__openfactory.simPaused === false", timeout=10_000)
        assert not banner.is_visible()
        page.wait_for_timeout(500)
        assert screenshot.get_json(port, "/api/state")["tick"] > held + 5
