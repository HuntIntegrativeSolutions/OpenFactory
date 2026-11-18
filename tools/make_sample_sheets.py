"""Write the sample drawing set's sheets, ``docs/samples/wash-skid/P-001.svg`` and ``L-001.svg``
(PLAN 5.1c; design note §3.7).

Usage::

    uv run python tools/make_sample_sheets.py            # rewrite both SVGs
    uv run python tools/make_sample_sheets.py --check    # exit 1 if a committed SVG differs

The sheets are ours (HIS Controls, CC-BY-4.0; ``docs/samples/wash-skid/LICENSE.txt``): a plant of
our own invention, drawn here from SVG primitives only (lines, rectangles, circles, polygons and
text), with no symbol library and no template or title block from anyone's drawing. Instrument
bubbles follow the public ISA-5.1 convention: a circle with the function letters over the loop
number (a plain circle is field-mounted). They name no real plant, customer, vendor or product.

- **P-001**, the P&ID: a make-up supply through level control valve LV-101 into tank TK-101 with
  level transmitter LT-101, high-level switch LSH-101 and agitator AG-101; transfer pump P-101
  through flow transmitter FT-101 to the washer, off this drawing. Lines carry line numbers; a
  zone grid runs A to E across and 1 to 4 down.
- **L-001**, the layout, in plan, north up: the skid and the carton reject belt CV-201 with its
  photo eye PE-201 and pusher CYL-201, dimensioned in millimetres from a datum at a building
  column (ordinates from the datum, elevations as ``EL``), the belt's devices from its infeed
  end. The datum and the sheet's axes are drawn, so a reader can write the sheet's ``frame``.

The output is deterministic. ``tools/render_sheets.py`` renders each to a PNG.
Training and demo tool; not a safety-rated simulation.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "docs" / "samples" / "wash-skid"
INK = "#1a1a1a"
FAINT = "#8a8a8a"
LICENSE_LINE = (
    "CC-BY-4.0 HIS Controls (Hunt Integrative Solutions, LLC). OpenFactory sample drawing."
)


class Svg:
    def __init__(self, width: int, height: int, title: str) -> None:
        self.w, self.h = width, height
        self.parts = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" font-family="sans-serif" fill="none" '
            f'stroke="{INK}" stroke-width="1.5">',
            f"<title>{_esc(title)}</title>",
            f"<!-- {_esc(LICENSE_LINE)} A plant of our own invention. -->",
            f'<rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff" stroke="none"/>',
        ]

    def add(self, text: str) -> None:
        self.parts.append(text)

    def line(self, x1: float, y1: float, x2: float, y2: float, **kw: str) -> None:
        self.add(f'<line x1="{_n(x1)}" y1="{_n(y1)}" x2="{_n(x2)}" y2="{_n(y2)}"{_kw(kw)}/>')

    def poly(self, points: list[tuple[float, float]], closed: bool = False, **kw: str) -> None:
        tag = "polygon" if closed else "polyline"
        pts = " ".join(f"{_n(x)},{_n(y)}" for x, y in points)
        self.add(f'<{tag} points="{pts}"{_kw(kw)}/>')

    def rect(self, x: float, y: float, w: float, h: float, **kw: str) -> None:
        self.add(f'<rect x="{_n(x)}" y="{_n(y)}" width="{_n(w)}" height="{_n(h)}"{_kw(kw)}/>')

    def circle(self, cx: float, cy: float, r: float, **kw: str) -> None:
        self.add(f'<circle cx="{_n(cx)}" cy="{_n(cy)}" r="{_n(r)}"{_kw(kw)}/>')

    def text(self, x: float, y: float, s: str, size: int = 12, anchor: str = "start",
             weight: str = "normal", rotate: float = 0) -> None:  # fmt: skip
        turn = f' transform="rotate({_n(rotate)} {_n(x)} {_n(y)})"' if rotate else ""
        self.add(
            f'<text x="{_n(x)}" y="{_n(y)}" font-size="{size}" text-anchor="{anchor}" '
            f'font-weight="{weight}" fill="{INK}" stroke="none"{turn}>{_esc(s)}</text>'
        )

    def arrow(self, x1: float, y1: float, x2: float, y2: float, size: float = 8) -> None:
        self.line(x1, y1, x2, y2)
        dx, dy = x2 - x1, y2 - y1
        length = (dx * dx + dy * dy) ** 0.5 or 1.0
        ux, uy = dx / length, dy / length
        bx, by = x2 - ux * size, y2 - uy * size
        self.poly([(x2, y2), (bx - uy * size / 2, by + ux * size / 2),
                   (bx + uy * size / 2, by - ux * size / 2)], closed=True, fill=INK)  # fmt: skip

    def bubble(self, cx: float, cy: float, letters: str, loop: str, r: float = 22) -> None:
        """An ISA-5.1 instrument bubble: field-mounted (a plain circle)."""
        self.circle(cx, cy, r, fill="#ffffff")
        self.line(cx - r, cy, cx + r, cy, **{"stroke-width": "1"})
        self.text(cx, cy - 6, letters, 11, "middle", "bold")
        self.text(cx, cy + 15, loop, 11, "middle")

    def done(self) -> str:
        return "\n".join([*self.parts, "</svg>", ""])


def _n(v: float) -> str:
    r = round(float(v), 2)
    return str(int(r)) if r == int(r) else f"{r:g}"


def _kw(kw: dict[str, str]) -> str:
    return "".join(f' {k.replace("_", "-")}="{v}"' for k, v in kw.items())


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _zones(svg: Svg, x0: float, y0: float, cols: int, rows: int, cw: float, rh: float) -> None:
    """The zone grid: letters across the top and bottom, numbers down both sides."""
    x1, y1 = x0 + cols * cw, y0 + rows * rh
    svg.rect(x0, y0, x1 - x0, y1 - y0, **{"stroke-width": "2"})
    for c in range(cols):
        cx = x0 + c * cw + cw / 2
        for y in (y0 - 8, y1 + 18):
            svg.text(cx, y, "ABCDEFGH"[c], 13, "middle", "bold")
        if c:
            for ya, yb in ((y0, y0 + 10), (y1 - 10, y1)):
                svg.line(x0 + c * cw, ya, x0 + c * cw, yb, **{"stroke-width": "1"})
    for r in range(rows):
        cy = y0 + r * rh + rh / 2 + 5
        for x in (x0 - 14, x1 + 14):
            svg.text(x, cy, str(r + 1), 13, "middle", "bold")
        if r:
            for xa, xb in ((x0, x0 + 10), (x1 - 10, x1)):
                svg.line(xa, y0 + r * rh, xb, y0 + r * rh, **{"stroke-width": "1"})


def _title_block(svg: Svg, x: float, y: float, title: str, sheet: str, scale: str) -> None:
    """Our own title block: plant, title, sheet, revision, licence."""
    w, h = 560, 120
    svg.rect(x, y, w, h, **{"stroke-width": "2"})
    svg.line(x, y + 34, x + w, y + 34, **{"stroke-width": "1"})
    svg.line(x, y + 70, x + w, y + 70, **{"stroke-width": "1"})
    svg.line(x + 380, y + 70, x + 380, y + h, **{"stroke-width": "1"})
    svg.line(x + 470, y + 70, x + 470, y + h, **{"stroke-width": "1"})
    plant = "WASH-SKID SAMPLE PLANT (A PLANT OF OUR OWN INVENTION)"
    svg.text(x + 10, y + 23, plant, 12, weight="bold")
    svg.text(x + 10, y + 58, title, 16, weight="bold")
    svg.text(x + 10, y + 90, scale, 11)
    svg.text(x + 10, y + 110, "OPENFACTORY SAMPLE. NOT FOR CONSTRUCTION. CC-BY-4.0", 10)
    svg.text(x + 388, y + 88, "SHEET", 10)
    svg.text(x + 388, y + 110, sheet, 15, weight="bold")
    svg.text(x + 478, y + 88, "REV", 10)
    svg.text(x + 478, y + 110, "A", 15, weight="bold")


# --- P-001, the P&ID ---------------------------------------------------------------------------


def p001() -> str:
    svg = Svg(1400, 1020, "P-001 Wash Water Skid P&ID")
    x0, y0, cw, rh = 50, 50, 260, 200
    _zones(svg, x0, y0, 5, 4, cw, rh)
    thick = {"stroke-width": "2.5"}

    # make-up supply, A1, down to the main line in A2
    svg.rect(110, 110, 140, 40, fill="#ffffff")
    svg.text(180, 128, "MAKE-UP WATER", 11, "middle", "bold")
    svg.text(180, 143, "SUP-101", 11, "middle")
    svg.line(180, 150, 180, 280, **thick)
    svg.line(180, 280, 405, 280, **thick)
    svg.text(195, 272, "25-W-100", 11)

    # LV-101, B2: a control valve, two triangles and a diaphragm actuator
    vx, vy = 440, 280
    svg.poly([(vx - 30, vy - 15), (vx - 30, vy + 15), (vx, vy)], closed=True, fill="#ffffff")
    svg.poly([(vx + 30, vy - 15), (vx + 30, vy + 15), (vx, vy)], closed=True, fill="#ffffff")
    svg.line(vx, vy, vx, vy - 34)
    svg.add(f'<path d="M {vx - 18} {vy - 34} A 18 14 0 0 1 {vx + 18} {vy - 34} Z" fill="#ffffff"/>')
    svg.text(vx, vy + 34, "LV-101", 12, "middle", "bold")
    svg.line(vx + 30, vy, 690, vy, **thick)
    svg.line(690, vy, 690, 310, **thick)
    svg.text(510, 272, "25-W-101", 11)

    # TK-101, C2, with its agitator AG-101 from C1
    svg.rect(630, 310, 140, 120, fill="#ffffff", **{"stroke-width": "2.5"})
    svg.text(700, 380, "TK-101", 13, "middle", "bold")
    svg.text(700, 397, "WASH WATER TANK", 9, "middle")
    svg.circle(745, 170, 22, fill="#ffffff")
    svg.text(745, 175, "M", 14, "middle", "bold")
    svg.line(745, 192, 745, 418)
    svg.poly([(728, 412), (762, 424)], **{"stroke-width": "3"})
    svg.poly([(728, 424), (762, 412)], **{"stroke-width": "3"})
    svg.text(775, 165, "AG-101", 12, weight="bold")
    svg.text(775, 180, "AGITATOR", 9)

    # LSH-101 at the tank's top (C2) and LT-101 below it (C3), signal lines dashed
    svg.line(770, 330, 800, 330, **{"stroke-dasharray": "6 4"})
    svg.bubble(822, 330, "LSH", "101", r=20)
    svg.line(630, 410, 600, 410)
    svg.line(600, 410, 600, 545, **{"stroke-dasharray": "6 4"})
    svg.line(600, 545, 668, 545, **{"stroke-dasharray": "6 4"})
    svg.bubble(690, 545, "LT", "101")
    svg.text(690, 590, "0 - 1.8 m", 10, "middle")

    # P-101, D2: suction from the tank's bottom, line 25-W-102
    svg.line(700, 430, 700, 440, **thick)
    svg.line(700, 440, 935, 440, **thick)
    svg.text(845, 433, "25-W-102", 11)
    svg.circle(960, 430, 25, fill="#ffffff", **{"stroke-width": "2"})
    svg.poly([(946, 445), (960, 409), (974, 445)], closed=True)
    svg.text(960, 480, "P-101", 12, "middle", "bold")
    svg.text(960, 494, "TRANSFER PUMP", 9, "middle")

    # discharge 25-W-103 through FE/FT-101 (D3) to the washer, off this drawing (E2)
    svg.line(985, 430, 1170, 430, **thick)
    svg.rect(1020, 420, 22, 20, fill="#ffffff")
    svg.text(1031, 435, "FE", 8, "middle")
    svg.line(1031, 440, 1031, 528, **{"stroke-dasharray": "6 4"})
    svg.bubble(1031, 550, "FT", "101")
    svg.text(1031, 595, "0 - 5 m3/h", 10, "middle")
    svg.text(1060, 422, "25-W-103", 11)
    svg.poly([(1170, 415), (1260, 415), (1280, 430), (1260, 445), (1170, 445)], closed=True,
             fill="#ffffff")  # fmt: skip
    svg.text(1222, 434, "TO WASHER", 10, "middle", "bold")
    svg.text(1222, 465, "DR-101, OFF THIS", 9, "middle")
    svg.text(1222, 477, "DRAWING", 9, "middle")

    # notes, row 4
    for i, note in enumerate([
        "NOTES",
        "1. LINE NUMBERS ARE SIZE (MM) - SERVICE - NUMBER. W: WASH WATER.",
        "2. INSTRUMENT BUBBLES AFTER ISA-5.1: FUNCTION LETTERS OVER LOOP NUMBER, FIELD MOUNTED.",
        "3. DASHED LINES ARE SIGNALS. I/O IS LISTED ON IO-001; POSITIONS ON L-001.",
        "4. MAKE-UP PRESSURE IS NOT SHOWN ON THIS DRAWING.",
    ]):  # fmt: skip
        svg.text(80, 700 + 22 * i, note, 12, weight="bold" if i == 0 else "normal")
    # below the zone letters along the bottom edge (y 868), not over D and E
    _title_block(svg, 790, 884, "WASH WATER SKID P&ID", "P-001", "NOT TO SCALE")
    return svg.done()


# --- L-001, the layout -------------------------------------------------------------------------

SCALE = 0.14  # px per mm
X_ORIGIN, Y_ORIGIN = 120, 930  # the datum, in px


def _px(x_mm: float, y_mm: float) -> tuple[float, float]:
    return X_ORIGIN + x_mm * SCALE, Y_ORIGIN - y_mm * SCALE


def l001() -> str:
    svg = Svg(1400, 1100, "L-001 Wash Water Skid and Reject Belt Layout")
    # the zone grid: columns 1600 mm wide from x 700 mm (A to E), rows 1300 mm from y 6100 mm
    zx, zy = _px(700, 6100)
    _zones(svg, zx, zy, 5, 4, 1600 * SCALE, 1300 * SCALE)
    faint = {"stroke": FAINT, "stroke-width": "1"}

    # the datum: a building column, the axes from it
    cx, cy = _px(0, 0)
    svg.rect(cx - 14, cy - 14, 28, 28, **{"stroke-width": "2"})
    svg.line(cx - 14, cy - 14, cx + 14, cy + 14, **{"stroke-width": "1"})
    svg.line(cx - 14, cy + 14, cx + 14, cy - 14, **{"stroke-width": "1"})
    svg.circle(cx, cy, 22, **{"stroke-width": "1"})
    svg.arrow(cx + 22, cy, cx + 120, cy)
    svg.text(cx + 126, cy + 5, "+X", 13, weight="bold")
    svg.arrow(cx, cy - 22, cx, cy - 120)
    svg.text(cx - 6, cy - 128, "+Y", 13, weight="bold")
    svg.text(cx + 30, cy + 34, "DATUM: BUILDING COLUMN, X 0, Y 0, EL 0", 12, weight="bold")
    svg.arrow(cx - 60, cy - 220, cx - 60, cy - 300)
    svg.text(cx - 60, cy - 306, "N", 14, "middle", "bold")

    # the skid at Y 4000: items on a process line, each with its elevation
    items = [
        (1000, "SUP-101", "EL +2400", "sup"), (1800, "LV-101", "EL +2400", "valve"),
        (3000, "TK-101", "EL +500", "tank"), (3700, "LT-101", "EL +1400", "inst"),
        (4200, "P-101", "EL +300", "pump"), (4800, "FT-101", "EL +600", "inst"),
        (5600, "DR-101", "EL +300", "drain"),
    ]  # fmt: skip
    sx0, sy0 = _px(800, 5100)
    sx1, sy1 = _px(6700, 3000)
    svg.rect(sx0, sy0, sx1 - sx0, sy1 - sy0, **{"stroke-dasharray": "10 5", "stroke-width": "1"})
    svg.text(sx0 + 6, sy0 + 16, "WASH WATER SKID", 11, weight="bold")
    lx0, ly = _px(1000, 4000)
    lx1, _ = _px(5600, 4000)
    svg.line(lx0, ly, lx1, ly, **{"stroke-width": "2"})
    for x_mm, tag, el, kind in items:
        px, py = _px(x_mm, 4000)
        if kind == "tank":
            svg.circle(px, py, 600 * SCALE, fill="#ffffff", **{"stroke-width": "2.5"})
            svg.circle(px, py, 150 * SCALE, fill="#ffffff")
            svg.text(px, py - 600 * SCALE - 8, "AG-101 EL +2400 (ON TK-101 TOP)", 10, "middle")
            svg.text(px, py + 4, "AG", 9, "middle")
        elif kind == "pump":
            svg.rect(px - 300 * SCALE, py - 200 * SCALE, 600 * SCALE, 400 * SCALE, fill="#ffffff")
            svg.circle(px, py, 150 * SCALE)
        elif kind == "valve":
            svg.poly([(px - 14, py - 9), (px - 14, py + 9), (px + 14, py - 9), (px + 14, py + 9)],
                     closed=True, fill="#ffffff")  # fmt: skip
        elif kind == "drain":
            svg.arrow(px - 10, py, px + 40, py)
        else:
            svg.rect(px - 7, py - 7, 14, 14, fill="#ffffff")
        if kind == "inst":  # above the line, beside the item
            svg.text(px - 6, py - 50, tag, 11, "start", "bold")
            svg.text(px - 6, py - 36, el, 10, "start")
            svg.line(px, py - 32, px, py - 8, **{"stroke-width": "1"})
            continue
        below = {"tank": 600 * SCALE + 16, "pump": 200 * SCALE + 16}.get(kind, 26)
        svg.text(px, py + below, tag, 11, "middle", "bold")
        svg.text(px, py + below + 14, el, 10, "middle")
    svg.text(_px(5600, 4000)[0] + 46, ly + 4, "TO WASHER", 10)

    # ordinate dimensions in X, from the datum, along a baseline above the skid
    bx0, by = _px(0, 5550)
    _, stop = _px(0, 5150)  # the extension lines stop above the skid
    svg.line(bx0, by, _px(5600, 5550)[0], by, **faint)
    svg.text(bx0, by - 8, "0", 10, "middle")
    for x_mm, *_ in items:
        px, _ = _px(x_mm, 0)
        svg.line(px, by - 5, px, stop, **faint)
        svg.text(px + 4, by - 8, str(x_mm), 10, "start", rotate=-90)
    # and in Y: the skid's line and the belt's centreline
    ox, _ = _px(-500, 0)
    for y_mm in (4000, 1500):
        _, py = _px(0, y_mm)
        svg.line(ox, py, ox + 70, py, **faint)
        svg.text(ox - 4, py + 4, str(y_mm), 10, "end")
    svg.line(ox + 20, _px(0, 0)[1], ox + 20, _px(0, 4000)[1], **faint)

    # CV-201: 3000 long, 500 wide, infeed at X 3000, centreline Y 1500, top of belt EL 0
    bx, btop = _px(3000, 1750)
    bx2, bbot = _px(6000, 1250)
    svg.rect(bx, btop, bx2 - bx, bbot - btop, fill="#ffffff", **{"stroke-width": "2"})
    for k in range(1, 12):
        x = bx + k * (bx2 - bx) / 12
        svg.line(x, btop, x, bbot, **faint)
    _, cl = _px(0, 1500)
    svg.arrow(bx + 30, cl, bx2 - 30, cl, 10)
    svg.text((bx + bx2) / 2, bbot + 18, "CV-201 CARTON REJECT BELT, 3000 LG x 500 W, TOB EL 0", 11,
             "middle", "bold")  # fmt: skip
    svg.text((bx + bx2) / 2, bbot + 32, "FLOW +X. INFEED END AT X 3000.", 10, "middle")
    svg.text(bx - 6, cl + 4, "INFEED", 10, "end")
    # PE-201 on the left (north) rail 1200 from the infeed, its beam across the belt
    pex, _ = _px(4200, 0)
    svg.rect(pex - 7, btop - 20, 14, 14, fill="#ffffff")
    svg.line(pex, btop - 6, pex, bbot + 6, **{"stroke-dasharray": "4 3", "stroke-width": "1"})
    svg.text(pex - 10, btop - 26, "PE-201", 11, "end", "bold")
    # CYL-201 on the left (north) rail 2000 from the infeed, pushing across
    cyx, _ = _px(5000, 0)
    svg.rect(cyx - 10, btop - 70, 20, 56, fill="#ffffff")
    svg.rect(cyx - 30, btop - 14, 60, 8, fill="#ffffff")
    svg.text(cyx + 18, btop - 50, "CYL-201", 11, "start", "bold")
    svg.text(cyx + 18, btop - 36, "PUSHER, STROKE 600", 9)
    # the belt's own dimension chain from its infeed end
    dy = btop - 100
    for x_mm, label in ((4200, "1200"), (5000, "2000")):
        px, _ = _px(x_mm, 0)
        svg.line(bx, dy, px, dy, **faint)
        svg.line(px, dy - 5, px, btop - 22, **faint)
        svg.text((bx + px) / 2, dy - 4 - (16 if label == "2000" else 0), label, 10, "middle")
        dy -= 16
    svg.line(bx, btop - 116, bx, btop, **faint)

    for i, note in enumerate([
        "NOTES",
        "1. PLAN VIEW, NORTH UP. DIMENSIONS IN MILLIMETRES FROM THE DATUM.",
        "2. EL: ELEVATION OF THE ITEM'S REFERENCE POINT ABOVE THE DATUM.",
        "3. BELT DEVICES ARE DIMENSIONED FROM THE BELT'S INFEED END, ON ITS LEFT (NORTH) RAIL.",
        "4. CARTON SOURCE AND DISCHARGE ARE NOT SHOWN.",
    ]):  # fmt: skip
        svg.text(60, 990 + 18 * i, note, 11, weight="bold" if i == 0 else "normal")
    _title_block(svg, 800, 965, "WASH WATER SKID AND REJECT BELT LAYOUT", "L-001",
                 "NOT TO SCALE. DIMENSIONS IN MM.")  # fmt: skip
    return svg.done()


SHEETS = {"P-001.svg": p001, "L-001.svg": l001}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if a committed SVG differs")
    args = parser.parse_args(argv)
    stale = []
    for name, make in SHEETS.items():
        path, text = OUT / name, make()
        if args.check:
            if not path.is_file() or path.read_text(encoding="utf-8") != text:
                stale.append(name)
            continue
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path.relative_to(REPO)} ({len(text.encode('utf-8'))} bytes)")
    for name in stale:
        print(f"{name} is not current; run tools/make_sample_sheets.py", file=sys.stderr)
    return 1 if stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
