"""PLAN 2.13b: the viewer's comms menu. Right-clicking empty space in a run that serves Modbus TCP
and OPC UA opens one menu for both; choosing "Drop every request" sets the shared comms fault
(GET /api/fault), and "Clear comms fault" clears it."""

from __future__ import annotations

import contextlib
import importlib.util
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

pytest.importorskip("asyncua")  # the opcua extra; CI installs --all-extras

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "tools" / "screenshot.py"
spec = importlib.util.spec_from_file_location("screenshot_tool", TOOL)
screenshot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(screenshot)

SORTER = REPO / "scenes" / "sorting-by-height.yaml"


@pytest.fixture(autouse=True)
def _viewer_built(prerequisite):
    prerequisite(
        (REPO / "web" / "dist" / "index.html").is_file(), "viewer not built (npm run build)"
    )


def faults(port: int) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/fault", timeout=5) as res:
        return json.load(res)


def test_the_comms_menu_drops_and_clears_both_protocol_servers(tmp_path):
    from playwright.sync_api import sync_playwright

    scene = tmp_path / SORTER.name  # `run` writes lockfiles next to the scene: run a copy
    scene.write_text(SORTER.read_text(encoding="utf-8"), encoding="utf-8")
    web_port = screenshot.free_port()
    cmd = [
        sys.executable, "-m", "openfactory", "run", str(scene), "-q", "--opcua",
        "--port", str(screenshot.free_port()), "--opcua-port", str(screenshot.free_port()),
        "--web-port", str(web_port),
    ]  # fmt: skip
    proc = subprocess.Popen(
        cmd, cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    try:
        screenshot.wait_for_api(web_port, proc)
        assert faults(web_port)["protocols"] == ["Modbus TCP", "OPC UA"]
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=screenshot.CHROMIUM_ARGS)
            page = browser.new_page(viewport={"width": 1280, "height": 720})
            page.goto(f"http://127.0.0.1:{web_port}/")
            page.wait_for_function("() => window.__openfactory && window.__openfactory.ready")
            # the bottom-left corner looks at the floor, beside every component
            opened = page.evaluate("() => window.__openfactoryFaultMenu.openAt(8, 712)")
            assert opened == "comms"
            menu = page.locator("#fault-menu")
            assert menu.inner_text().splitlines() == [
                "Comms · Modbus TCP and OPC UA", "Drop every request",
                "Delay every reply 250 ms", "Delay every reply 1000 ms",
            ]  # fmt: skip
            page.screenshot(path=str(tmp_path / "comms-menu.png"))
            menu.get_by_text("Drop every request").click()
            page.wait_for_function(
                "() => document.getElementById('fault-menu').style.display === 'none'"
            )
            assert faults(web_port)["comms"] == {"drop": True, "latency_ms": 0.0}
            page.evaluate("() => window.__openfactoryFaultMenu.openAt(8, 712)")
            assert "● Drop every request" in menu.inner_text()
            menu.get_by_text("Clear comms fault (drop)").click()
            page.wait_for_timeout(300)
            assert faults(web_port)["comms"] == {"drop": False, "latency_ms": 0.0}
            browser.close()
    finally:
        proc.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(10)
        if proc.poll() is None:
            proc.kill()
