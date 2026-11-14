"""PLAN 4.1i Done-when, in the browser: while a scene runs, the checks panel lists each check as
pending, passed or failed and updates live, in the ISA-101 palette, read-only; the instructor
view shows the same panel.

The sorter runs in real time with `checks: duration_s` shortened to 20 s (a copy), so the run is
graded inside the test: all four checks go from pending to passed. A program that never pushes
sends a tall box to the short boxes' remover, and `parts_reached REM_02: tall` turns failed the
moment it happens, long before the run is graded."""

from __future__ import annotations

import contextlib
import importlib.util
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "tools" / "screenshot.py"
spec = importlib.util.spec_from_file_location("screenshot_tool", TOOL)
screenshot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(screenshot)

SORTER = REPO / "scenes" / "sorting-by-height.yaml"
SCRIPT = REPO / "examples" / "pyplc" / "sorting-by-height.py"
NO_PUSH = """\
def step(io, t):
    io["CONV_01_RUN_DO"] = True
    io["CONV_02_RUN_DO"] = True
    io["CONV_01_SPD_AO"] = 100.0
    io["CONV_02_SPD_AO"] = 100.0
    io["EMT_01_EN_DO"] = True
"""
ALARM_RED = "rgb(239, 68, 68)"  # ISA-101 alarmHigh, #EF4444
STOPPED_GREY = "rgb(156, 163, 175)"  # ISA-101 stopped, #9CA3AF
NORMAL_TEXT = "rgb(229, 231, 235)"  # ISA-101 normalText, #E5E7EB


@pytest.fixture(autouse=True)
def _viewer_built(prerequisite):
    prerequisite(
        (REPO / "web" / "dist" / "index.html").is_file(), "viewer not built (npm run build)"
    )


@contextlib.contextmanager
def running(tmp_path: Path, script: Path, duration_s: int) -> Iterator[int]:
    text = SORTER.read_text(encoding="utf-8").replace("duration_s: 60", f"duration_s: {duration_s}")
    assert f"duration_s: {duration_s}" in text
    scene = tmp_path / SORTER.name  # `run` writes lockfiles next to the scene: run a copy
    scene.write_text(text, encoding="utf-8")
    web = screenshot.free_port()
    cmd = [
        sys.executable, "-m", "openfactory", "run", str(scene), "-q", "--script", str(script),
        "--port", str(screenshot.free_port()), "--web-port", str(web),
    ]  # fmt: skip
    proc = subprocess.Popen(cmd, cwd=REPO, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        screenshot.wait_for_api(web, proc)
        yield web
    finally:
        proc.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(10)
        if proc.poll() is None:
            proc.kill()


def rows(page) -> list[dict]:
    return page.evaluate(
        """() => [...document.querySelectorAll('#checks-panel .check')].map((r) => ({
            state: r.dataset.state,
            label: r.querySelector('.check-label').textContent,
            name: r.querySelector('.check-name').textContent,
            color: getComputedStyle(r.querySelector('.check-name')).color,
        }))"""
    )


def open_viewer(pw, url: str):
    browser = pw.chromium.launch(args=screenshot.CHROMIUM_ARGS)
    page = browser.new_page(viewport={"width": 1280, "height": 720})
    page.goto(url)
    page.wait_for_function("() => window.__openfactory && window.__openfactory.ready")
    page.wait_for_function("() => document.querySelectorAll('#checks-panel .check').length > 0")
    return browser, page


def test_checks_go_from_pending_to_passed_when_graded(tmp_path):
    from playwright.sync_api import sync_playwright

    out = Path(os.environ.get("OPENFACTORY_EDITOR_OUT", tmp_path))
    with running(tmp_path, SCRIPT, 20) as web, sync_playwright() as pw:
        browser, page = open_viewer(pw, f"http://127.0.0.1:{web}/")
        first = rows(page)
        assert [r["state"] for r in first] == ["pending"] * 4, first
        assert {r["color"] for r in first} == {STOPPED_GREY}
        assert {r["label"] for r in first} == {"—"}
        assert "graded at 20 s" in page.locator("#checks-panel-header").inner_text()
        # read-only: nothing in the panel but its header takes a click
        assert page.locator("#checks-panel button, #checks-panel input").count() == 0
        page.wait_for_function(
            "() => window.__openfactoryChecks.status?.graded === true", timeout=60_000
        )
        page.wait_for_function(
            "() => [...document.querySelectorAll('#checks-panel .check')]"
            ".every((r) => r.dataset.state === 'passed')"
        )
        done = rows(page)
        assert {r["label"] for r in done} == {"PASS"} and {r["color"] for r in done} == {
            NORMAL_TEXT
        }
        page.screenshot(path=str(out / "checks-4.1i-passed.png"))
        # the instructor view (loopback: no token needed) shows the same panel
        teacher_browser, teacher = open_viewer(pw, f"http://127.0.0.1:{web}/instructor")
        teacher.wait_for_function(
            "() => [...document.querySelectorAll('#checks-panel .check')]"
            ".every((r) => r.dataset.state === 'passed')"
        )
        assert [r["name"] for r in rows(teacher)] == [r["name"] for r in done]
        teacher_browser.close()
        browser.close()


def test_a_settled_failure_shows_at_once(tmp_path):
    from playwright.sync_api import sync_playwright

    script = tmp_path / "no_push.py"
    script.write_text(NO_PUSH, encoding="utf-8")
    out = Path(os.environ.get("OPENFACTORY_EDITOR_OUT", tmp_path))
    with running(tmp_path, script, 60) as web, sync_playwright() as pw:
        browser, page = open_viewer(pw, f"http://127.0.0.1:{web}/")
        page.wait_for_function(
            "() => document.querySelector('#checks-panel .check-failed') !== null",
            timeout=45_000,
        )
        now = {r["name"]: r for r in rows(page)}
        tall = now["parts_reached REM_02: tall"]
        assert (tall["state"], tall["label"], tall["color"]) == ("failed", "FAIL", ALARM_RED)
        assert now["none_lost"]["state"] == "pending"  # undecided until it is graded
        assert page.evaluate("() => window.__openfactoryChecks.status.graded") is False
        header = page.locator("#checks-panel-header")
        assert "1 failed" in header.inner_text()
        page.screenshot(path=str(out / "checks-4.1i-failed.png"))
        header.click()  # folds the list: the header is all a trainee can press
        assert page.locator("#checks-panel .check").count() == 0
        browser.close()
