"""PLAN 4.3, the classroom: one core, many browsers. Ten Playwright pages on one server, each with
its own camera, sharing the core's faults and exam switches, each HUD showing the viewer count.
Done when (ruled 2026-11-09): ten viewers, zero dropped ticks, state frames serialized once."""

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
VIEWERS = 10


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


def test_ten_viewers_share_one_core(tmp_path):
    from playwright.sync_api import sync_playwright

    scene = tmp_path / SORTER.name  # `run` writes lockfiles next to the scene: run a copy
    scene.write_text(SORTER.read_text(encoding="utf-8"), encoding="utf-8")
    web = screenshot.free_port()
    cmd = [
        sys.executable, "-m", "openfactory", "run", str(scene), "-q", "--exam",
        "--script", str(SCRIPT), "--port", str(screenshot.free_port()), "--web-port", str(web),
    ]  # fmt: skip
    proc = subprocess.Popen(cmd, cwd=REPO, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        screenshot.wait_for_api(web, proc, timeout=60)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=screenshot.CHROMIUM_ARGS)
            pages = [
                browser.new_page(viewport={"width": 640, "height": 360}) for _ in range(VIEWERS)
            ]
            for page in pages:
                page.goto(f"http://127.0.0.1:{web}/")
            for page in pages:
                page.wait_for_function("() => window.__openfactory && window.__openfactory.ready")
            until(lambda: api(web, "/api/status")["viewers"] == VIEWERS)

            # the HUD of every browser says ten viewers
            for page in pages:
                page.wait_for_function(f"() => window.__openfactory.viewers === {VIEWERS}")
                assert f"viewers {VIEWERS}" in page.locator("body").inner_text()

            # state frames serialized once: over 5 s, one serialization per broadcast, its text
            # sent to all ten (a send still running when the next frame is due is a skip)
            before = api(web, "/api/status")
            time.sleep(5)
            after = api(web, "/api/status")
            serialized = after["frames_serialized"] - before["frames_serialized"]
            sent = after["frames_sent"] - before["frames_sent"]
            skipped = after["frames_skipped"] - before["frames_skipped"]
            assert serialized >= 90  # 20 Hz for 5 s
            assert sent + skipped >= VIEWERS * serialized - VIEWERS  # each to every viewer
            assert sent <= VIEWERS * (serialized + 1)  # sends of the frame just before the window
            print(f"5 s: {serialized} serialized, {sent} sent, {skipped} skipped", end="; ")

            # each browser has its own camera: orbit one, the others stay where they were
            cams = [p.evaluate("() => window.__openfactoryCamera()") for p in pages[:2]]
            pages[0].mouse.move(320, 180)
            pages[0].mouse.down()
            pages[0].mouse.move(420, 200, steps=10)
            pages[0].mouse.up()
            pages[0].wait_for_timeout(500)
            assert pages[0].evaluate("() => window.__openfactoryCamera()") != cams[0]
            assert pages[1].evaluate("() => window.__openfactoryCamera()") == cams[1]

            # faults are the core's: one injected through the API is the active fault in every
            # browser's fault menu (in exam mode the 3D labels hide faults from trainees, by
            # design, so the instructor's menu is where every viewer sees it)
            api(web, "/api/fault", {"id": "CYL_01", "kind": "jammed"})
            for page in pages:
                opened = page.evaluate(
                    "() => { const [x, y] = window.__openfactoryScreenOf('CYL_01');"
                    " return window.__openfactoryFaultMenu.openAt(x, y); }"
                )
                assert opened == "CYL_01"
                assert "● jammed" in page.locator("#fault-menu").inner_text()
                page.keyboard.press("Escape")

            # exam switches too: one viewer turns PE_01_DI on, every panel shows it on
            pages[3].get_by_text("PE_01_DI: OFF").click()
            for page in pages:
                page.get_by_text("PE_01_DI: ON").wait_for(timeout=30_000)

            # zero dropped ticks: the core kept its 50 Hz with ten viewers connected
            status = api(web, "/api/status")
            assert status["dropped_ticks"] == 0, status
            drops = [p.evaluate("() => window.__openfactory.droppedTicks") for p in pages]
            print(f"core dropped 0; viewer physics dropped {drops}")
            pages[0].screenshot(path=str(tmp_path / "viewer-0.png"))
            browser.close()
        until(lambda: api(web, "/api/status")["viewers"] == 0)  # every viewer gone, counted
    finally:
        proc.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(10)
        if proc.poll() is None:
            proc.kill()
