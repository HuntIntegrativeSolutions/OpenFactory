"""PLAN 5.14d: values in the picture. With ``?labels=tags`` CYL_01's label lists
``CYL_01_EXT_DO`` with the value ``/api/state`` gives at the tick the labels were drawn from; no
two labels overlap in the sorter at 1280 px, nor in the wash plant's plan view (where the two
fans' labels overlapped in 5.14c), boxes compared. Screenshots are written for a person to look
at (to ``$OPENFACTORY_LABELS_OUT``, else the test's own folder, never the repository)."""

from __future__ import annotations

import contextlib
import importlib.util
import os
import subprocess
import sys
from itertools import combinations
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("screenshot_tool", REPO / "tools" / "screenshot.py")
screenshot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(screenshot)

SORTER = REPO / "scenes" / "sorting-by-height.yaml"
SCRIPT = REPO / "examples" / "pyplc" / "sorting-by-height.py"
KIT_RECORDING = REPO / "kits" / "sorting-by-height" / "sorting-by-height.ofrec"
WASH = REPO / "scenes" / "plants" / "wash-plant" / "wash-plant.yaml"
WASH_SCRIPT = REPO / "scenes" / "plants" / "wash-plant" / "wash-plant.py"
W, H = 1280, 720


@pytest.fixture(autouse=True)
def _viewer_built(prerequisite):
    prerequisite(
        (REPO / "web" / "dist" / "index.html").is_file(), "viewer not built (npm run build)"
    )


def out_dir(tmp_path: Path) -> Path:
    return Path(os.environ.get("OPENFACTORY_LABELS_OUT", tmp_path))


def labels_on_screen(page) -> list[dict]:  # noqa: ANN001
    page.wait_for_timeout(400)  # a stacking pass (every 100 ms) after the last text change
    return page.evaluate("() => window.__openfactoryLabels()")


def overlapping(labels: list[dict]) -> list[tuple[str, str]]:
    out = []
    for a, b in combinations(labels, 2):
        p, q = a["box"], b["box"]
        apart = (
            p["right"] <= q["left"]
            or q["right"] <= p["left"]
            or p["bottom"] <= q["top"]
            or q["bottom"] <= p["top"]
        )
        if not apart:
            out.append((a["id"], b["id"]))
    return out


def cyl_label(labels: list[dict]) -> list[str]:
    return next(lab["text"] for lab in labels if lab["id"] == "CYL_01").split("\n")


def test_a_label_shows_the_value_api_state_gives_at_that_tick(tmp_path):
    """A replay that has ended holds its last frame, so ``/api/state`` and the labels are at the
    same tick for as long as the test reads them; then a live run, where the label's tick is read
    with its text and the value at that tick is read back from the change log."""
    from playwright.sync_api import sync_playwright

    web_port = screenshot.free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "openfactory", "replay", str(KIT_RECORDING), "-q",
         "--web-port", str(web_port), "--speed", "20"],
        cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )  # fmt: skip
    try:
        screenshot.wait_for_api(web_port, proc)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=screenshot.CHROMIUM_ARGS)
            page = browser.new_page(viewport={"width": W, "height": H})
            page.goto(f"http://127.0.0.1:{web_port}/?quality=low&labels=tags")
            page.wait_for_function("() => window.__openfactory && window.__openfactory.ready")
            for _ in range(200):
                if screenshot.get_json(web_port, "/api/status")["finished"]:
                    break
                page.wait_for_timeout(100)
            state = screenshot.get_json(web_port, "/api/state")
            page.wait_for_function(
                "t => window.__openfactory.labelsTick === t", arg=state["tick"], timeout=10_000
            )
            lines = cyl_label(labels_on_screen(page))
            want = "true" if state["io"]["CYL_01_EXT_DO"] else "false"
            print(f"replay end, tick {state['tick']}: CYL_01's label {lines}")
            assert lines[0] == "CYL_01"
            assert f"CYL_01_EXT_DO {want}" in lines
            assert page.evaluate("() => window.__openfactory.labels") == "tags"
            browser.close()
    finally:
        proc.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(10)

    with screenshot.running_viewer(SORTER, forces=[], size=(W, H), script=SCRIPT,
                                   view={"labels": "tags"}) as (page, port, console):  # fmt: skip
        screenshot.wait_for_viewer(page, console, seconds=1.0, timeout=60)
        seen = {}
        for _ in range(600):  # until the label has shown the pusher's command both ways
            text, tick = page.evaluate(
                "() => [window.__openfactoryLabels().find(l => l.id === 'CYL_01').text,"
                " window.__openfactory.labelsTick]"
            )
            value = next(ln.split()[1] for ln in text.split("\n") if ln.startswith("CYL_01_EXT_DO"))
            seen.setdefault(value, tick)
            if len(seen) == 2:
                break
            page.wait_for_timeout(50)
        assert set(seen) == {"true", "false"}, seen
        log = screenshot.get_json(port, "/api/iolog?tags=CYL_01_EXT_DO")["changes"]
        for value, tick in seen.items():
            at = [c["value"] for c in log if c["tick"] <= tick][-1]  # its value at that tick
            print(f"live: label CYL_01_EXT_DO {value} at tick {tick}; the log has {at}")
            assert ("true" if at else "false") == value


def test_no_two_labels_overlap_in_the_sorter_at_1280_px(tmp_path):
    with screenshot.running_viewer(SORTER, forces=[], size=(W, H), script=SCRIPT,
                                   view={"labels": "tags"}) as (page, _, console):  # fmt: skip
        screenshot.wait_for_viewer(page, console, seconds=6.0, timeout=90)
        for mode in ("tags", "ids"):
            page.evaluate(f"() => window.__openfactorySetLabels('{mode}')")
            labels = labels_on_screen(page)
            print(f"sorter, {mode}: {len(labels)} labels, overlapping {overlapping(labels)}")
            assert len(labels) == 7
            assert overlapping(labels) == []
            if mode == "tags":
                assert cyl_label(labels)[1].startswith("CYL_01_EXT_DO ")
            page.screenshot(path=str(out_dir(tmp_path) / f"sorter-labels-{mode}.png"))
        page.evaluate("() => window.__openfactorySetLabels('off')")
        assert labels_on_screen(page) == []


@pytest.mark.parametrize("mode", ["tags", "ids"])
def test_the_wash_plants_fans_labels_do_not_overlap_in_the_plan(tmp_path, mode):
    view = screenshot.view_params(view="top", panels=False, labels=mode)
    with screenshot.running_viewer(WASH, forces=[], size=(W, H), script=WASH_SCRIPT,
                                   view=view) as (page, _, console):  # fmt: skip
        screenshot.wait_for_viewer(page, console, seconds=4.0, timeout=90)
        labels = labels_on_screen(page)
        ids = {lab["id"] for lab in labels}
        print(f"wash plant plan, {mode}: {len(labels)} labels, overlapping {overlapping(labels)}")
        assert {"FAN_101_BODY", "FAN_101_HOA", "FAN_201_BODY", "FAN_201_HOA"} <= ids
        assert overlapping(labels) == []
        page.screenshot(path=str(out_dir(tmp_path) / f"wash-plant-top-labels-{mode}.png"))
