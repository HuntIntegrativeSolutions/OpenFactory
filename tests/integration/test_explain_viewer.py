"""PLAN 5.14g in the browser: clicking a part draws its trail and lists the same steps
``explain`` gives. A replay of the late-pusher sorter run (the program fires the pusher 0.6 s
late, recorded in the test's folder) is paused at tick 560, after P000002 was pushed too late;
a click on P000002 opens the panel with the late command in it and draws the trail in the
highlight blue (pixel sample on the trail). A live run answers a click the same way, from its
last 60 s. Pictures go to ``$OPENFACTORY_EXPLAIN_OUT`` (else the test's folder) to be looked at."""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("screenshot_tool", REPO / "tools" / "screenshot.py")
screenshot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(screenshot)
SORTER = REPO / "scenes" / "sorting-by-height.yaml"
SCRIPT = REPO / "examples" / "pyplc" / "sorting-by-height.py"
HIGHLIGHT = (0x60, 0xA5, 0xFA)


@pytest.fixture(autouse=True)
def _viewer_built(prerequisite):
    prerequisite(
        (REPO / "web" / "dist" / "index.html").is_file(), "viewer not built (npm run build)"
    )


def out_dir(tmp_path: Path) -> Path:
    return Path(os.environ.get("OPENFACTORY_EXPLAIN_OUT", tmp_path))


def late_script(folder: Path) -> Path:
    text = re.sub(
        r"^DIVERT_DELAY_S = TRAVEL_S.*$", "DIVERT_DELAY_S = TRAVEL_S + 0.6",
        SCRIPT.read_text(encoding="utf-8"), flags=re.M,
    )  # fmt: skip
    path = folder / "late.py"
    path.write_text(text, encoding="utf-8")
    return path


def post(port: int, path: str, body: dict) -> dict:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )  # fmt: skip
    with urllib.request.urlopen(req, timeout=10) as res:
        return json.load(res)


def click_part(page, pid: str) -> None:  # noqa: ANN001
    x, y = page.evaluate(
        "id => { const p = window.__openfactoryPartTargets()[id];"
        " return window.__openfactoryProject(p[0], p[1], p[2]); }",
        pid,
    )
    assert page.evaluate("([x, y]) => window.__openfactoryPickPart(x, y)", [x, y]) == pid
    page.mouse.click(x, y)
    page.wait_for_function("() => window.__openfactory.explained", timeout=20_000)


def test_a_click_on_the_late_pushed_part_draws_its_trail_and_lists_its_story(tmp_path):
    from playwright.sync_api import sync_playwright

    rec = tmp_path / "late.ofrec"
    done = subprocess.run(
        [sys.executable, "-m", "openfactory", "test", str(SORTER), "--script",
         str(late_script(tmp_path)), "--record", str(rec)],
        cwd=tmp_path, capture_output=True, text=True, timeout=300,
    )  # fmt: skip
    assert done.returncode == 1 and "FAIL  parts_reached REM_02: tall" in done.stdout
    port = screenshot.free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "openfactory", "replay", str(rec), "-q", "--paused",
         "--web-port", str(port)],
        cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )  # fmt: skip
    try:
        screenshot.wait_for_api(port, proc)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=screenshot.CHROMIUM_ARGS)
            page = browser.new_page(viewport={"width": 1280, "height": 720})
            page.goto(f"http://127.0.0.1:{port}/?quality=low&panels=off")
            page.wait_for_function("() => window.__openfactory && window.__openfactory.ready")
            assert post(port, "/api/replay/seek", {"tick": 560})["tick"] == 560
            page.wait_for_function(
                "() => window.__openfactory.tick === 560 && window.__openfactoryPartsSettled()"
            )
            click_part(page, "P000002")
            explained = page.evaluate("() => window.__openfactory.explained")
            assert explained["subject"] == "P000002" and explained["meshes"] > 10, explained
            panel = page.locator("#explain-panel")
            assert panel.is_visible()
            rows = panel.locator("li").all_inner_texts()
            print("\n".join(rows))
            same = post(port, "/api/explain", {"part": "P000002"})
            assert len(rows) == len(same["steps"])
            assert any(r.startswith("9.20 s: CYL_01_EXT_DO went true") for r in rows), rows
            assert any("0.21 m past CYL_01" in r for r in rows), rows
            shot = out_dir(tmp_path) / "explain-trail-P000002.png"
            page.screenshot(path=str(shot))
            # the trail is drawn: the highlight blue where the part was at tick 400, on the belt
            k, x, y, z = next(p for p in same["trail"] if p[0] == 400)
            sx, sy = page.evaluate("p => window.__openfactoryProject(...p)", [x, y, z])
            width, _, channels, pixels = screenshot.read_png(shot)
            at = (round(sy) * width + round(sx)) * channels
            r, g, b = pixels[at : at + 3]
            print(f"trail pixel at tick {k}: {(r, g, b)}")
            assert b > r + 60 and b > g + 20, (r, g, b)
            page.keyboard.press("Escape")
            assert not panel.is_visible()
            assert page.evaluate("() => window.__openfactory.explained") is None
            browser.close()
    finally:
        proc.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(10)


def test_a_live_run_answers_a_click_from_its_last_60_s(tmp_path):
    with screenshot.running_viewer(SORTER, forces=[], script=SCRIPT) as (page, port, console):
        screenshot.wait_for_viewer(page, console, seconds=6.0, timeout=90)
        parts = screenshot.get_json(port, "/api/state")["parts"]
        pid = next(p["id"] for p in parts if p["carrier"] == "CONV_01")
        page.wait_for_function(
            "id => !!window.__openfactoryPartTargets()[id]", arg=pid, timeout=10_000
        )
        page.evaluate("id => window.__openfactoryExplain(id)", pid)
        page.wait_for_function("() => window.__openfactory.explained", timeout=20_000)
        explained = page.evaluate("() => window.__openfactory.explained")
        assert explained["subject"] == pid and explained["trail"] >= 2, (explained, console)
        assert page.locator("#explain-panel li").first.inner_text().endswith("where EMT_01 stands")
