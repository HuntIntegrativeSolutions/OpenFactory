"""PLAN 5.6 Review overlay (ruled 2026-11-19). The table lists every takeoff row against its scene
component; removing one item from a copy of the sample scene shows it in the table (and, in
``tests/integration/test_review_overlay.py``, in the picture). The drawing is loaded as an image
only, PNG or SVG, anything else refused by name; nothing is fetched; the review is written beside
the takeoff or to a named folder, never into the repository by default. The top view lines up
with the sample sheet's own drawn symbols, measured here in millimetres."""

from __future__ import annotations

import math
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openfactory import takeoff as tk
from openfactory.cli import app
from openfactory.core.sceneedit import apply_ops
from openfactory.review import POLICY, DrawingRefused, drawing_data_url, overlay_html, review
from openfactory.scaffold import scaffold

REPO = Path(__file__).resolve().parents[2]
SAMPLES = REPO / "docs" / "samples" / "wash-skid"
SAMPLE = SAMPLES / "wash-skid.takeoff.yaml"
PNG, SVG = SAMPLES / "L-001.png", SAMPLES / "L-001.svg"
#: The sample sheet's registration (tools/make_sample_sheets.py: X_ORIGIN, Y_ORIGIN, SCALE).
DATUM_PX, PX_PER_MM = (120.0, 930.0), 0.14
runner = CliRunner()


@pytest.fixture
def copy(tmp_path):  # noqa: ANN201
    """A copy of the sample takeoff and its scaffolded scene, outside the repository."""
    takeoff = tmp_path / SAMPLE.name
    shutil.copyfile(SAMPLE, takeoff)
    scene = tmp_path / "wash-skid.yaml"
    scene.write_text(scaffold(tk.load(takeoff)).text, encoding="utf-8")
    return takeoff, scene


def without(scene: Path, cid: str) -> Path:
    text, _ = apply_ops(scene.read_text(encoding="utf-8"), [{"op": "delete", "id": cid}])
    out = scene.with_name(f"without-{cid}.yaml")
    out.write_text(text, encoding="utf-8")
    return out


def table(takeoff: Path, scene: Path) -> dict[str, tuple[str, str]]:
    rev = review(tk.load(takeoff), scene, None, DATUM_PX, PX_PER_MM)
    return {r.row if r.row != "-" else r.scene: (r.scene, r.status) for r in rev.rows}


def test_the_table_lists_every_row_and_a_removed_item_shows_as_missing(copy):
    """Done-when (the table half) and ruling 4."""
    takeoff, scene = copy
    whole = table(takeoff, scene)
    rows = [r.row for r in tk.load(takeoff).model.rows()]
    assert list(whole) == rows and len(rows) == 32  # every takeoff row, in order
    assert {s for _, s in whole.values()} == {"present"}
    gone = table(takeoff, without(scene, "FT_101"))
    missing = {k for k, (_, s) in gone.items() if s == "missing"}
    print(missing)
    assert missing == {"EQ-7", "IO-9"}  # the transmitter and its I/O row
    assert gone["EQ-7"] == ("FT_101", "missing")


def test_a_component_with_no_row_is_listed_and_a_laid_out_one_says_so(copy):
    takeoff, scene = copy
    extra = {"id": "SC_900", "type": "scenery", "description": "Stray Platform",
             "position": [6.0, 0.5, 9.0], "size": [1.0, 1.0, 1.0]}  # fmt: skip
    text, _ = apply_ops(scene.read_text(encoding="utf-8"), [{"op": "add", "component": extra}])
    stray = scene.with_name("stray.yaml")
    stray.write_text(text, encoding="utf-8")
    rows = table(takeoff, stray)
    assert rows["SC_900"] == ("SC_900", "in the scene with no row")
    rev = review(tk.load(takeoff), scene, None, DATUM_PX, PX_PER_MM)
    pipe = next(r for r in rev.rows if r.row == "CN-2")
    assert pipe.position == "routed by openfactory layout"  # never read as off a sheet
    assert "| EQ-3 | TK-101 (tank) | TK_101 | present | L-001 dimensions | 0 mm |" in rev.table()


def test_the_drawing_is_loaded_as_an_image_only(tmp_path):
    """Ruling 1: a data: URL in an <img>, drawn on a canvas; the drawing's markup is never in
    the page, so a script in an SVG cannot run; the page loads nothing else."""
    hostile = tmp_path / "L-001.svg"
    hostile.write_text(SVG.read_text("utf-8").replace(
        "</svg>", '<script>window.top.stolen = 1</script><text>INJECTED</text></svg>'),
        encoding="utf-8")  # fmt: skip
    url = drawing_data_url(hostile)
    assert url.startswith("data:image/svg+xml;base64,")
    rev = review(tk.load(SAMPLE), _scene(tmp_path), None, DATUM_PX, PX_PER_MM)
    page = overlay_html(rev, url)
    assert "<script>window.top" not in page and "INJECTED" not in page and "<svg" not in page
    assert f'content="{POLICY}"' in page and "default-src 'none'" in POLICY
    assert "http://" not in page and "https://" not in page  # nothing to fetch
    assert drawing_data_url(PNG).startswith("data:image/png;base64,")


@pytest.mark.parametrize(
    ("name", "said"),
    [("L-001.pdf", "PDF"), ("L-001.jpg", "JPG"), ("L-001.dxf", "DXF"), ("L-001", "no suffix")],
)
def test_any_other_type_is_refused_by_name(tmp_path, name, said):
    path = tmp_path / name
    path.write_bytes(b"%PDF-1.7 not a drawing review loads")
    with pytest.raises(DrawingRefused, match=said):
        drawing_data_url(path)


def test_a_file_that_is_not_what_its_suffix_says_and_a_url_are_refused(tmp_path):
    fake = tmp_path / "L-001.png"
    fake.write_bytes(b"<svg/>")
    with pytest.raises(DrawingRefused, match="not a PNG"):
        drawing_data_url(fake)
    with pytest.raises(DrawingRefused, match="fetches nothing"):
        drawing_data_url(Path("https://example.invalid/L-001.png"))


def _scene(tmp_path: Path) -> Path:
    scene = tmp_path / "s.yaml"
    scene.write_text(scaffold(tk.load(SAMPLE)).text, encoding="utf-8")
    return scene


def test_written_beside_the_takeoff_or_a_named_folder_never_the_repository(copy, tmp_path):
    """Ruling 2 (and ruling 3: the drawing's path is given; a URL is refused)."""
    takeoff, scene = copy
    args = ["--drawing", str(PNG), "--datum-px", "120,930", "--px-per-unit", "0.14"]
    done = runner.invoke(app, ["review", str(takeoff), str(scene), *args])
    assert done.exit_code == 0, done.output
    assert (takeoff.parent / "wash-skid.review.md").is_file()  # beside the takeoff
    assert (takeoff.parent / "wash-skid.review.html").is_file()
    named = tmp_path / "private"
    done = runner.invoke(app, ["review", str(takeoff), str(scene), *args, "--out", str(named)])
    assert done.exit_code == 0 and (named / "wash-skid.review.md").is_file()
    before = sorted(p.name for p in SAMPLES.iterdir())
    done = runner.invoke(app, ["review", str(SAMPLE), str(scene), *args])  # beside: the repo
    assert done.exit_code == 1 and "inside the OpenFactory repository" in done.output
    assert sorted(p.name for p in SAMPLES.iterdir()) == before  # nothing written there
    url = ["--drawing", "https://example.invalid/L-001.png", *args[2:]]
    done = runner.invoke(app, ["review", str(takeoff), str(scene), *url])
    assert done.exit_code == 1 and "fetches nothing" in done.output
    docs = " ".join((REPO / "docs" / "review.md").read_text(encoding="utf-8").split())
    assert "as private as the drawing" in docs and "never into the repository" in docs


def _svg_symbols() -> dict[str, tuple[float, float]]:
    """Where L-001.svg itself draws each symbol, in pixels (read from the sheet, for the test
    only: the overlay never reads a drawing's markup)."""
    svg = "{http://www.w3.org/2000/svg}"
    root = ET.parse(SVG).getroot()
    circles = [(float(c.get("cx")), float(c.get("cy")), float(c.get("r")))
               for c in root.iter(f"{svg}circle")]  # fmt: skip
    rects = [(float(r.get("x")), float(r.get("y")), float(r.get("width")),
              float(r.get("height"))) for r in root.iter(f"{svg}rect")]  # fmt: skip
    line_y = 370.0  # the skid's process line, Y 4000 on the sheet
    small = sorted((x + w / 2, y + h / 2) for x, y, w, h in rects
                   if w == h == 14 and abs(y + h / 2 - line_y) < 0.01)  # fmt: skip
    polys = [[tuple(map(float, q.split(","))) for q in p.get("points").split()]
             for p in root.iter(f"{svg}polygon")]  # fmt: skip
    valve = next(p for p in polys if len(p) == 4 and abs(sum(q[1] for q in p) / 4 - line_y) < 1)
    tank = next(c for c in circles if abs(c[2] - 84) < 0.01)
    pump = next(c for c in circles if abs(c[2] - 21) < 0.01 and c[0] > tank[0])
    return {
        "SUP_101": small[0], "LT_101": small[1], "FT_101": small[2],
        "LV_101": (sum(p[0] for p in valve) / 4, sum(p[1] for p in valve) / 4),
        "TK_101": tank[:2], "P_101": pump[:2],
        "TK_101_r": (tank[2], 0.0),
        "CV_201": next((x, y, w, h) for x, y, w, h in rects if w == 420),  # type: ignore[misc]
    }  # fmt: skip


def test_the_top_view_lines_up_with_the_sheet_in_millimetres(copy):
    """Through the sheet's frame and the picture's registration, each item's outline sits on
    the symbol L-001 draws for it: centres within 1 mm; the tank's radius within 1 mm; the
    belt's ends within 1 mm and its sides 30 mm outside the drawn belt (its rails, which the
    sheet leaves out)."""
    takeoff, scene = copy
    rev = review(tk.load(takeoff), scene, None, DATUM_PX, PX_PER_MM)
    shapes = {s["id"]: s for s in rev.shapes}
    drawn = _svg_symbols()
    worst = 0.0
    for sid in ("SUP_101", "LV_101", "TK_101", "LT_101", "P_101", "FT_101"):
        off = math.dist(shapes[sid]["at"], drawn[sid]) / PX_PER_MM
        print(f"{sid}: {off:.2f} mm")
        worst = max(worst, off)
    assert worst <= 1.0
    assert abs(shapes["TK_101"]["circle"][2] - drawn["TK_101_r"][0]) / PX_PER_MM <= 1.0
    x, y, w, h = drawn["CV_201"]
    poly = shapes["CV_201"]["polys"][0]
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    assert abs(min(xs) - x) / PX_PER_MM <= 1.0 and abs(max(xs) - (x + w)) / PX_PER_MM <= 1.0
    assert (y - min(ys)) / PX_PER_MM == pytest.approx(30.0, abs=1.0)
    assert (max(ys) - (y + h)) / PX_PER_MM == pytest.approx(30.0, abs=1.0)


@pytest.mark.parametrize("turn", [0.0, 90.0, 30.0])
def test_the_registration_inverts_the_frame_at_any_rotation(tmp_path, turn):
    """Scene metres back to the sheet's printed dimensions through a frame turned ``turn``
    degrees: every layout row's (x, y) within 1 mm, and its pixel where the datum and scale
    put it."""
    import yaml

    from openfactory.review import Registration

    data = yaml.safe_load(SAMPLE.read_text(encoding="utf-8"))
    data["drawings"][1]["frame"]["rotation_deg"] = turn
    data.pop("checked")
    path = tmp_path / "t.takeoff.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    model = tk.load(path).model
    sheet = model.sheets()["L-001"]
    reg = Registration(sheet.frame, DATUM_PX, PX_PER_MM)
    rows = [e for e in model.equipment if e.layout is not None]
    assert len(rows) == 9
    for e in rows:
        (x, _, z), _ = tk.position_of(e, model)
        sx, sy = reg.sheet(x, z)
        assert (sx, sy) == pytest.approx((e.layout.x, e.layout.y), abs=1.0), e.tag
        px, py = reg.px(x, z)
        assert px == pytest.approx(DATUM_PX[0] + e.layout.x * PX_PER_MM, abs=0.01)
        assert py == pytest.approx(DATUM_PX[1] - e.layout.y * PX_PER_MM, abs=0.01)
