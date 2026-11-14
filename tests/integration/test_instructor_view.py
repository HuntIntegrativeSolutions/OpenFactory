"""PLAN 4.12, the instructor view: /instructor takes the fault token, shows fault markers in exam
mode and shows the exam switches read-only, on the 4.3 broadcaster. Done when: a Playwright test
with one instructor page and two trainee pages shows the marker only on the instructor page, the
instructor cannot turn a switch, and a wrong token is refused."""

from __future__ import annotations

import contextlib
import importlib.util
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "tools" / "screenshot.py"
spec = importlib.util.spec_from_file_location("screenshot_tool", TOOL)
screenshot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(screenshot)

SORTER = REPO / "scenes" / "sorting-by-height.yaml"
SCRIPT = REPO / "examples" / "pyplc" / "sorting-by-height.py"
TOKEN = "classroom-token-4-12"


@pytest.fixture(autouse=True)
def _viewer_built(prerequisite):
    prerequisite(
        (REPO / "web" / "dist" / "index.html").is_file(), "viewer not built (npm run build)"
    )


def api(port: int, path: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=10) as res:
        return json.load(res)


def until(check, timeout: float = 60.0) -> None:  # noqa: ANN001
    deadline = time.monotonic() + timeout  # a hang guard, not a speed check
    while not check():
        assert time.monotonic() < deadline, "condition not met in time"
        time.sleep(0.2)


def test_the_instructor_sees_faults_and_switches_but_cannot_turn_them(tmp_path):
    from playwright.sync_api import sync_playwright

    scene = tmp_path / SORTER.name  # `run` writes lockfiles next to the scene: run a copy
    scene.write_text(SORTER.read_text(encoding="utf-8"), encoding="utf-8")
    web = screenshot.free_port()
    cmd = [
        sys.executable, "-m", "openfactory", "run", str(scene), "-q", "--exam",
        "--script", str(SCRIPT), "--port", str(screenshot.free_port()), "--web-port", str(web),
        "--fault-token", TOKEN,
    ]  # fmt: skip
    proc = subprocess.Popen(cmd, cwd=REPO, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        screenshot.wait_for_api(web, proc, timeout=60)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=screenshot.CHROMIUM_ARGS)
            size = {"width": 640, "height": 360}
            teacher = browser.new_page(viewport=size)
            trainees = [browser.new_page(viewport=size) for _ in range(2)]
            intruder = browser.new_page(viewport=size)
            teacher.goto(f"http://127.0.0.1:{web}/instructor#token={TOKEN}")
            for page in trainees:
                page.goto(f"http://127.0.0.1:{web}/")
            intruder.goto(f"http://127.0.0.1:{web}/instructor#token=not-the-token")
            for page in [teacher, *trainees]:
                page.wait_for_function("() => window.__openfactory && window.__openfactory.ready")

            # a wrong token is refused: no frame, the page says so, the server counts no instructor
            intruder.wait_for_function(
                "() => String(window.__openfactory.instructor).startsWith('refused')"
            )
            assert intruder.evaluate("() => window.__openfactory.frames") == 0
            assert "INSTRUCTOR VIEW REFUSED" in intruder.locator("body").inner_text()
            assert teacher.evaluate("() => window.__openfactory.instructor") == "on"
            assert "#token" not in teacher.url  # the token left the address bar
            until(lambda: api(web, "/api/status")["instructors"] == 1)
            assert api(web, "/api/status")["viewers"] == 3

            # a fault injected through the API: a marker on the instructor page only
            api(web, "/api/fault", {"id": "CYL_01", "kind": "jammed"})
            teacher.wait_for_function(
                "() => window.__openfactory.faultMarkers.includes('CYL_01')", timeout=30_000
            )
            assert "CYL_01 ⚠ jammed" in teacher.locator("body").inner_text()

            # at 640 px the instructor's exam panel starts collapsed and covers no fault label:
            # the label is on screen and is what the page shows at its own centre (ruled
            # 2026-11-10)
            assert teacher.locator("#exam-panel").get_attribute("data-collapsed") == "true"
            for page in trainees:
                assert page.locator("#exam-panel").get_attribute("data-collapsed") == "false"
            teacher.wait_for_timeout(500)
            label = teacher.get_by_text("CYL_01 ⚠ jammed")
            box = label.bounding_box()
            assert box and box["x"] >= 0 and box["y"] >= 0
            assert box["x"] + box["width"] <= 640 and box["y"] + box["height"] <= 360
            # the live checks panel (PLAN 4.1i) folds to one line there as well
            teacher.wait_for_selector("#checks-panel-header")
            assert teacher.locator("#checks-panel-header").inner_text().startswith("▸")
            for cover in ("#exam-panel", "#hud", "#checks-panel"):  # no box overlaps the label
                other = teacher.locator(cover).bounding_box()
                assert other is not None
                apart = (
                    other["x"] >= box["x"] + box["width"]
                    or box["x"] >= other["x"] + other["width"]
                    or other["y"] >= box["y"] + box["height"]
                    or box["y"] >= other["y"] + other["height"]
                )
                assert apart, (cover, other, box)
            # and nothing else is on top: the labels layer and the HUD ignore the pointer, so the
            # probe makes the label and the HUD hit-testable for one elementFromPoint
            on_top = teacher.evaluate(
                """([x, y]) => {
                     const label = [...document.querySelectorAll('div')]
                       .find((d) => d.textContent === 'CYL_01 ⚠ jammed');
                     const hud = document.getElementById('hud');
                     const keep = [label.style.pointerEvents, hud.style.pointerEvents];
                     label.style.pointerEvents = 'auto'; hud.style.pointerEvents = 'auto';
                     const hit = document.elementFromPoint(x, y);
                     [label.style.pointerEvents, hud.style.pointerEvents] = keep;
                     return hit === label || label.contains(hit); }""",
                [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2],
            )
            assert on_top, "the CYL_01 fault label is covered"
            teacher.screenshot(path=str(tmp_path / "instructor-collapsed.png"))
            for page in trainees:  # well after the fault: forty more frames
                seen = page.evaluate("() => window.__openfactory.frames")
                page.wait_for_function(f"() => window.__openfactory.frames > {seen + 40}")
                assert page.evaluate("() => window.__openfactory.faultMarkers") == []
                assert "⚠" not in page.locator("body").inner_text()

            # the instructor's switches are read-only: disabled, and a forced click does nothing
            teacher.locator("#exam-panel-header").click()  # open the collapsed panel
            assert teacher.locator("#exam-panel").get_attribute("data-collapsed") == "false"
            buttons = teacher.locator("#exam-panel button")
            assert buttons.count() >= 1
            assert all(buttons.nth(i).is_disabled() for i in range(buttons.count()))
            assert "(read-only)" in teacher.locator("#exam-panel").inner_text()
            switch = teacher.locator("#exam-panel button", has_text="PE_01_DI")
            switch.click(force=True)
            teacher.wait_for_timeout(1000)
            exam = {s["tag"]: s["on"] for s in api(web, "/api/exam")["switches"]}
            assert exam["PE_01_DI"] is False
            assert "PE_01_DI: OFF" in switch.inner_text()

            # ...while a trainee's turn shows on the instructor's panel
            trainees[0].get_by_text("PE_01_DI: OFF").click()
            teacher.get_by_text("PE_01_DI: ON").wait_for(timeout=30_000)

            # at 640 px the exam panel does not cover the status lines (review of 2026-11-10)
            for page in [teacher, *trainees]:
                page.wait_for_timeout(300)  # a few frames: the panel follows the HUD's height
                hud, panel = (
                    page.locator("#hud").bounding_box(),
                    page.locator("#exam-panel").bounding_box(),
                )
                assert hud and panel and panel["width"] > 0
                apart = (
                    panel["y"] >= hud["y"] + hud["height"] or panel["x"] >= hud["x"] + hud["width"]
                )
                assert apart, (hud, panel)
            teacher.screenshot(path=str(tmp_path / "instructor.png"))
            trainees[1].screenshot(path=str(tmp_path / "trainee.png"))
            browser.close()
        until(lambda: api(web, "/api/status")["viewers"] == 0)
    finally:
        proc.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(10)
        if proc.poll() is None:
            proc.kill()
