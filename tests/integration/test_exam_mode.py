"""PLAN 2.11 Done-when, exam mode: the sorter runs under its script with `--exam`; PE_01 is bound
`exam: switch`, so tall boxes passing the real eye divert nothing, and clicking PE_01's switch in
the viewer's exam panel makes the pusher extend as if a tall box had tripped the eye. Fault markers
are left out of state frames while the fault API still shows the instructor what is active."""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import subprocess
import sys
import time
import urllib.request
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


@pytest.fixture(autouse=True)
def _viewer_built(prerequisite):
    prerequisite(
        (REPO / "web" / "dist" / "index.html").is_file(), "viewer not built (npm run build)"
    )


@contextlib.contextmanager
def exam_server(tmp_path: Path) -> Iterator[int]:
    scene = tmp_path / SORTER.name  # `run` writes lockfiles next to the scene: run a copy
    scene.write_text(SORTER.read_text(encoding="utf-8"), encoding="utf-8")
    web_port = screenshot.free_port()
    cmd = [
        sys.executable, "-m", "openfactory", "run", str(scene), "-q", "--exam",
        "--script", str(SCRIPT),
        "--port", str(screenshot.free_port()), "--web-port", str(web_port),
    ]  # fmt: skip
    proc = subprocess.Popen(
        cmd, cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    try:
        screenshot.wait_for_api(web_port, proc)
        yield web_port
    finally:
        proc.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(10)
        if proc.poll() is None:
            proc.kill()


def get(port: int, path: str) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as res:
        return json.load(res)


def post(port: int, path: str, body: dict) -> dict:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )  # fmt: skip
    with urllib.request.urlopen(req, timeout=5) as res:
        return json.load(res)


def watch(port: int, seconds: float, until=None) -> list[dict]:
    """Sample /api/state every 50 ms for up to `seconds` (or until `until(state)`)."""
    samples, deadline = [], time.monotonic() + seconds
    while time.monotonic() < deadline:
        state = get(port, "/api/state")
        samples.append(state)
        if until and until(state):
            break
        time.sleep(0.05)
    return samples


def test_exam_mode_a_switch_in_the_viewer_drives_the_divert(tmp_path):
    from playwright.sync_api import sync_playwright

    out = Path(os.environ.get("OPENFACTORY_EXAM_OUT", tmp_path))
    with exam_server(tmp_path) as port, sync_playwright() as pw:
        browser = pw.chromium.launch(args=screenshot.CHROMIUM_ARGS)
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{port}/")
        page.wait_for_function("() => window.__openfactory?.ready && window.__openfactory.exam")
        switch = page.locator('#exam-panel button[data-tag="PE_01_DI"]')
        assert switch.text_content() == "PE_01_DI: OFF"

        # 1. Switch off: a real tall box blocks the real beam, and the PLC sees nothing.
        seen = watch(port, 30, until=lambda s: s["components"]["PE_01"]["blocked"])
        assert seen[-1]["components"]["PE_01"]["blocked"], "no tall box reached PE_01 in 30 s"
        seen += watch(port, 3.0)  # past the divert delay the eye would have started
        assert not any(s["io"]["PE_01_DI"] for s in seen)
        # Count extensions with the statistics block, not by sampling: a server whose sim stalls
        # behind a software-rendered browser catches up in bursts faster than 50 ms polling.
        before = get(port, "/api/stats")["cycles"]["CYL_01"]["count"]
        assert before == 0, "diverted with the switch off"
        print(f"switch off: tall box under PE_01 at t = {seen[0]['t']:.1f}..; PLC saw PE_01_DI "
              "false; CYL_01 cycles 0")  # fmt: skip

        # 2. The instructor clicks the switch on, holds it, and clicks it off: the PLC sees the
        #    eye, and the script extends the pusher DIVERT_DELAY_S (2.08 s) later, once.
        switch.click()
        page.wait_for_function(
            "() => document.querySelector('#exam-panel button[data-tag=\"PE_01_DI\"]')"
            ".dataset.on === 'true'"
        )
        # Hold it as a person would: until the core has stepped 10 ticks seeing it (wall time is
        # no guide while the sim catches up).
        first = watch(port, 10.0, until=lambda s: s["io"]["PE_01_DI"])[-1]
        assert first["io"]["PE_01_DI"], "the PLC side never saw the switch"
        watch(port, 10.0, until=lambda s: s["tick"] >= first["tick"] + 10)
        page.screenshot(path=str(out / "exam-mode.png"))
        switch.click()
        page.wait_for_function(
            "() => document.querySelector('#exam-panel button[data-tag=\"PE_01_DI\"]')"
            ".dataset.on === 'false'"
        )
        watch(port, 20.0, until=lambda s: s["t"] >= first["t"] + 3.0)  # past the divert delay
        after = get(port, "/api/stats")["cycles"]["CYL_01"]
        print(f"switch on at t = {first['t']:.2f} s (PLC side), held 10+ ticks, off; "
              f"CYL_01 cycles {after['count']} (mean {after['mean_s']} s)")  # fmt: skip
        assert after["count"] == 1, "the divert did not follow the switch exactly once"

        # 3. Fault markers are hidden from the viewer; the instructor still sees the fault.
        post(port, "/api/fault", {"id": "CYL_01", "kind": "slow"})
        frame = watch(port, 0.3)[-1]
        assert "fault" not in frame["components"]["CYL_01"]
        assert [f["id"] for f in get(port, "/api/fault")["active"]] == ["CYL_01"]
        events = get(port, "/api/events")
        assert [e["value"] for e in events if e["type"] == "exam"] == [True, False]
        browser.close()
    assert errors == []
    ok, verdict = screenshot.check_image(out / "exam-mode.png")
    assert ok, verdict
