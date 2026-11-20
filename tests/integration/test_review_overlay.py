"""PLAN 5.6 Done-when, the picture half: removing one item from a copy of the sample scene shows it
in the overlay picture, drawn by headless Chromium from the page ``openfactory review`` writes
(the drawing loaded as an image). The table half is ``tests/unit/test_review.py``."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

pytest.importorskip("playwright")

from openfactory import takeoff as tk  # noqa: E402
from openfactory.core.sceneedit import apply_ops  # noqa: E402
from openfactory.review import drawing_data_url, overlay_html, render_png, review  # noqa: E402
from openfactory.scaffold import scaffold  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
SAMPLES = REPO / "docs" / "samples" / "wash-skid"


def red_pixels(page_html: str, at: list[float], reach: int = 26) -> int:
    """How many pixels within ``reach`` of ``at`` the page's canvas draws in the missing red."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1500, "height": 1200})
            page.set_content(page_html)
            page.wait_for_function("window.__overlayReady === true", timeout=20_000)
            return int(page.evaluate(
                """([x, y, r]) => {
                  const d = document.getElementById("overlay").getContext("2d")
                    .getImageData(x - r, y - r, 2 * r + 1, 2 * r + 1).data;
                  let n = 0;
                  for (let i = 0; i < d.length; i += 4)
                    n += d[i] > 150 && d[i + 1] < 110 && d[i + 2] < 100 ? 1 : 0;
                  return n;
                }""",
                [round(at[0]), round(at[1]), reach],
            ))  # fmt: skip
        finally:
            browser.close()


@pytest.mark.parametrize("drawing", ["L-001.png", "L-001.svg"])
def test_a_removed_item_shows_in_the_picture(tmp_path, drawing):
    takeoff = tmp_path / "wash-skid.takeoff.yaml"
    shutil.copyfile(SAMPLES / "wash-skid.takeoff.yaml", takeoff)
    scene = tmp_path / "wash-skid.yaml"
    scene.write_text(scaffold(tk.load(takeoff)).text, encoding="utf-8")
    text, _ = apply_ops(scene.read_text(encoding="utf-8"), [{"op": "delete", "id": "FT_101"}])
    minus = tmp_path / "minus.yaml"
    minus.write_text(text, encoding="utf-8")
    url = drawing_data_url(SAMPLES / drawing)
    pages = {}
    for name, path in (("whole", scene), ("minus", minus)):
        rev = review(tk.load(takeoff), path, None, (120.0, 930.0), 0.14)
        page = overlay_html(rev, url)
        render_png(page, tmp_path / f"{name}-{Path(drawing).suffix[1:]}.png")  # to look at
        pages[name] = (page, rev)
    page, rev = pages["minus"]
    gone = next(s for s in rev.shapes if s["kind"] == "missing")
    assert gone["label"] == "MISSING FT-101" and gone["at"] == [792.0, 370.0]
    shown, before = red_pixels(page, gone["at"]), red_pixels(pages["whole"][0], gone["at"])
    print(tmp_path, shown, before)
    assert shown > 40  # the red ring and outline at FT-101's place
    assert before == 0  # with nothing missing, no red there
