"""Viewer resilience: a server restarted with a different scene while the page is open.

The viewer refetches /api/scene when its state socket reconnects and rebuilds the plant if the
document changed, so the page shows the new scene without a reload (batch 2 housekeeping 4).
"""

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
QUEUE = REPO / "tests" / "scenes" / "queue-parity.yaml"


@pytest.fixture(autouse=True)
def _viewer_built(prerequisite):
    prerequisite(
        (REPO / "web" / "dist" / "index.html").is_file(), "viewer not built (npm run build)"
    )


def copy_scene(src: Path, tmp_path: Path) -> Path:
    """`openfactory run` writes lockfiles next to the scene, so run a copy."""
    path = tmp_path / src.name
    path.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return path


@contextlib.contextmanager
def server(scene: Path, web_port: int, *forces: str) -> Iterator[None]:
    cmd = [
        sys.executable, "-m", "openfactory", "run", str(scene), "-q",
        "--port", str(screenshot.free_port()), "--web-port", str(web_port),
    ]  # fmt: skip
    for force in forces:
        cmd += ["--force", force]
    proc = subprocess.Popen(
        cmd, cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    try:
        screenshot.wait_for_api(web_port, proc)
        yield
    finally:
        proc.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(10)
        if proc.poll() is None:
            proc.kill()


def test_restarted_server_with_another_scene_is_shown_without_a_page_reload(tmp_path):
    from playwright.sync_api import sync_playwright

    out = Path(os.environ.get("OPENFACTORY_RECONNECT_OUT", tmp_path))
    web_port = screenshot.free_port()
    status = "() => window.__openfactory"
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=screenshot.CHROMIUM_ARGS)
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        console: list[str] = []
        page.on("pageerror", lambda e: console.append(f"pageerror: {e}"))
        with server(copy_scene(SORTER, tmp_path), web_port, "CONV_01_RUN_DO=1", "EMT_01_EN_DO=1"):
            page.goto(f"http://127.0.0.1:{web_port}/")
            page.wait_for_function(
                "() => window.__openfactory?.ready && window.__openfactory.simTime > 1.5"
            )
            before = page.evaluate(status)
            page.evaluate("() => { window.__notReloaded = true; }")  # gone if the page reloads
            page.screenshot(path=str(out / "reconnect-before.png"))
        with server(copy_scene(QUEUE, tmp_path), web_port):
            page.wait_for_function(
                "() => window.__openfactory.scene === 'queue-parity'"
                " && window.__openfactory.simTime > 1",
                timeout=30_000,
            )
            after = page.evaluate(status)
            page.screenshot(path=str(out / "reconnect-after.png"))
            labels = page.eval_on_selector_all(  # component labels (CSS2D divs) on the page
                "div",
                "els => els.map(e => e.textContent).filter(t => /^[A-Z]+_[A-Z0-9_]+$/.test(t))",
            )
            not_reloaded = page.evaluate("() => window.__notReloaded === true")
        browser.close()
    print(
        f"before: {before['scene']}, {before['components']} components; after: "
        f"{after['scene']}, {after['components']} components, reloads {after['reloads']}, "
        f"labels {sorted(set(labels))}"
    )
    assert console == []
    assert before["scene"] == "sorting-by-height" and before["components"] == 7
    assert not_reloaded, "the page reloaded"
    assert after["reloads"] == 1 and after["error"] is None
    assert after["components"] == 9 and after["physics"].startswith("rapier")
    assert "CONV_01" not in labels and {"CONV_A", "CONV_B", "PE_B"} <= set(labels)
    for name in ("reconnect-before.png", "reconnect-after.png"):
        ok, verdict = screenshot.check_image(out / name)
        assert ok, verdict
