"""PLAN 5.14c: aimed views and a plan view, in the browser. ``?look=CYL_01`` frames the pusher;
``?view=top`` of the sorter is a plan with no perspective; ``?view=`` that is not six finite numbers
or ``top`` is the default view with a console warning and no page error; "Copy view" gives a value
that reopens the same camera. Screenshots are written for a person to look at (to
``$OPENFACTORY_VIEWS_OUT``, else the test's own folder, never the repository)."""

from __future__ import annotations

import importlib.util
import os
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "tools" / "screenshot.py"
spec = importlib.util.spec_from_file_location("screenshot_tool", TOOL)
screenshot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(screenshot)
SORTER = REPO / "scenes" / "sorting-by-height.yaml"
SCRIPT = REPO / "examples" / "pyplc" / "sorting-by-height.py"
W, H = 1280, 720


@pytest.fixture(autouse=True)
def _viewer_built(prerequisite):
    prerequisite(
        (REPO / "web" / "dist" / "index.html").is_file(), "viewer not built (npm run build)"
    )


def out_dir(tmp_path: Path) -> Path:
    return Path(os.environ.get("OPENFACTORY_VIEWS_OUT", tmp_path))


def ready(page, console) -> dict:  # noqa: ANN001
    return screenshot.wait_for_viewer(page, console, seconds=1.0, timeout=60)


def test_look_frames_the_pusher_and_a_wrong_view_is_the_default_with_a_warning(tmp_path):
    with screenshot.running_viewer(SORTER, forces=[], size=(W, H), script=SCRIPT,
                                   view={"look": "CYL_01"}) as (page, _, console):  # fmt: skip
        status = ready(page, console)
        assert status["view"] == "look CYL_01" and status["look"] == ["CYL_01"]
        left, top, right, bottom = page.evaluate("() => window.__openfactoryScreenBox('CYL_01')")
        print(f"CYL_01 screen box: {left:.0f}..{right:.0f} x {top:.0f}..{bottom:.0f} px, "
              f"{(right - left) / W:.1%} of the width")  # fmt: skip
        assert 0 <= left < right <= W and 0 <= top < bottom <= H  # inside the window
        assert right - left >= W / 5  # at least a fifth of its width
        page.screenshot(path=str(out_dir(tmp_path) / "sorter-look-CYL_01.png"))
        # Copy view: the value reopens the same camera
        page.locator("#copy-view").click()
        copied = page.evaluate("() => window.__openfactory.copiedView")
        assert re.fullmatch(r"view=(-?[\d.]+,){5}-?[\d.]+", copied), copied
        camera = page.evaluate("() => window.__openfactoryCamera()")
        page.goto(page.url.split("?")[0] + "?quality=low&" + copied)
        ready(page, console)
        again = page.evaluate("() => window.__openfactoryCamera()")
        assert all(abs(a - b) < 2e-3 for a, b in zip(camera, again, strict=True))
        assert page.evaluate("() => window.__openfactory.view") == "pose"

        default = None
        for bad in ("1,2,3", "1,2,3,4,5,NaN", "side", "1e999,0,0,0,0,0", "1,1,1,1,1,1"):
            console.clear()
            page.goto(page.url.split("?")[0] + f"?quality=low&view={bad}")
            ready(page, console)
            assert page.evaluate("() => window.__openfactory.view") == "default"
            warnings = [c for c in console if c.startswith("warning:") and "?view=" in c]
            assert warnings and "the default view is shown" in warnings[0], (bad, console)
            assert not [c for c in console if c.startswith("pageerror")], console
            here = page.evaluate("() => window.__openfactoryCamera()")
            default = default or here
            assert here == pytest.approx(default, abs=1e-6)  # the default view, each time
        console.clear()
        page.goto(page.url.split("?")[0] + "?quality=low&look=NOPE")
        ready(page, console)
        assert any("?look=NOPE: no such component" in c for c in console), console
        assert page.evaluate("() => window.__openfactoryCamera()") == pytest.approx(default)


def test_the_plan_view_of_the_sorter_has_no_perspective_and_shows_everything(tmp_path):
    with screenshot.running_viewer(SORTER, forces=[], size=(W, H), script=SCRIPT,
                                   view={"view": "top"}) as (page, _, console):  # fmt: skip
        status = ready(page, console)
        assert status["view"] == "top"
        doc = screenshot.get_json(_port(page), "/api/scene")
        ids = [c["id"] for c in doc["components"]]
        for cid in ids:  # every component on screen
            box = page.evaluate(f"() => window.__openfactoryScreenBox('{cid}')")
            left, top, right, bottom = box
            assert left >= 0 and right <= W and top >= 0 and bottom <= H, (cid, box)
        xs = [c["position"][0] for c in doc["components"]]
        zs = [c["position"][2] for c in doc["components"]]
        lo, hi, z = min(xs), max(xs), sum(zs) / len(zs)

        def metre(x: float, y: float, z_: float, dx: float, dz: float) -> float:
            a = page.evaluate(f"() => window.__openfactoryProject({x}, {y}, {z_})")
            b = page.evaluate(f"() => window.__openfactoryProject({x + dx}, {y}, {z_ + dz})")
            return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5

        west, east = metre(lo, 0, z, 1, 0), metre(hi, 0, z, 1, 0)
        high = metre(hi, 1.5, z, 1, 0)  # higher up: still the same (no perspective)
        across = metre(lo, 0, min(zs), 0, 1)
        print(f"1 m at the west end {west:.3f} px, at the east end {east:.3f} px, "
              f"1.5 m up {high:.3f} px, across {across:.3f} px")  # fmt: skip
        assert abs(west - east) < 0.01 and abs(west - high) < 0.01 and abs(west - across) < 0.01
        # north (-z) up, +x to the right
        o = page.evaluate(f"() => window.__openfactoryProject({lo}, 0, {z})")
        n = page.evaluate(f"() => window.__openfactoryProject({lo}, 0, {z - 1})")
        e = page.evaluate(f"() => window.__openfactoryProject({lo + 1}, 0, {z})")
        assert n[1] < o[1] and abs(n[0] - o[0]) < 0.01 and e[0] > o[0] and abs(e[1] - o[1]) < 0.01
        bar = page.evaluate("() => window.__openfactory.scaleBar")
        assert bar["pxPerMetre"] == pytest.approx(west, rel=1e-3)
        assert page.locator("#scale-bar").is_visible()
        page.locator("#copy-view").click()
        assert page.evaluate("() => window.__openfactory.copiedView") == "view=top"
        page.screenshot(path=str(out_dir(tmp_path) / "sorter-top.png"))


def _port(page) -> int:  # noqa: ANN001
    return int(page.url.split(":")[2].split("/")[0])


WASH = REPO / "scenes" / "plants" / "wash-plant" / "wash-plant.yaml"
WASH_SCRIPT = REPO / "scenes" / "plants" / "wash-plant" / "wash-plant.py"


def _overlaps(a: list[float], b: dict) -> bool:  # noqa: ANN001
    return a[0] < b["right"] and b["left"] < a[2] and a[1] < b["bottom"] and b["top"] < a[3]


def test_panels_off_leaves_the_panels_out_of_the_wash_plants_plan(tmp_path):
    """Ruled 2026-11-18: the checks panel covered P_201 and DR_201 in the plan view.
    ``?panels=off`` (``tools/screenshot.py --no-panels``) leaves the panels out; the scale bar
    and north mark stay."""
    args = screenshot.view_params(view="top", panels=False)
    assert args == {"view": "top", "panels": "off"}
    with screenshot.running_viewer(WASH, forces=[], size=(W, H), script=WASH_SCRIPT,
                                   view=args) as (page, _, console):  # fmt: skip
        status = ready(page, console)
        assert status["view"] == "top" and status["panels"] is False
        page.wait_for_timeout(1500)  # the checks panel polls; it must stay out of the picture
        assert not page.locator("#checks-panel").is_visible()
        assert not page.locator("#trend-button").is_visible()
        assert page.locator("#scale-bar").is_visible()
        assert "N ↑" in page.locator("#scale-bar").inner_text()
        for cid in ("P_201", "DR_201"):
            box = page.evaluate(f"() => window.__openfactoryScreenBox('{cid}')")
            assert box[0] >= 0 and box[2] <= W and box[1] >= 0 and box[3] <= H, (cid, box)
        page.screenshot(path=str(out_dir(tmp_path) / "wash-plant-top-no-panels.png"))
        # with the panels, the checks panel is shown over that corner (what the option is for)
        page.goto(page.url.replace("panels=off", "panels=on"))
        ready(page, console)
        page.wait_for_selector("#checks-panel", state="visible", timeout=10000)
        panel = page.evaluate(
            "() => document.getElementById('checks-panel').getBoundingClientRect().toJSON()"
        )
        boxes = [page.evaluate(f"() => window.__openfactoryScreenBox('{c}')")
                 for c in ("P_201", "DR_201")]  # fmt: skip
        assert any(_overlaps(b, panel) for b in boxes), (boxes, panel)
