"""Render the sample drawing set's sheets to PNG (PLAN 5.1c, addition C; design note §3.7).

Usage::

    uv run python tools/render_sheets.py            # render every docs/samples/*/*.svg

Each SVG is opened in the Chromium that Playwright installs for the screenshot runner
(``tools/screenshot.py``), at the SVG's own width and height, and saved as a PNG beside it; no
image library is used. The SVG's SHA-256 is written into the PNG as a ``tEXt`` chunk with the
keyword ``openfactory-source``, with the standard library's ``zlib`` and ``struct``, so a test
can tell a picture that was not rendered again after its SVG changed (:func:`source_digest`).
A render is not byte-for-byte the same on every machine (fonts and the Chromium version change
the pixels), so the test compares the recorded digest with the SVG's, not the pixels. Each PNG
must stay under 1 MB, the repository's rule for binaries.

Training and demo tool; not a safety-rated simulation.
"""

from __future__ import annotations

import hashlib
import re
import struct
import sys
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SAMPLES = REPO / "docs" / "samples"
KEYWORD = b"openfactory-source"
MAX_PNG = 1_000_000
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def svg_digest(svg: Path) -> str:
    return hashlib.sha256(svg.read_bytes()).hexdigest()


def chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    """The PNG's chunks, ``(type, data)``; ValueError for a file that is not a PNG."""
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError("not a PNG")
    out, i = [], len(PNG_SIGNATURE)
    while i < len(data):
        (length,) = struct.unpack(">I", data[i : i + 4])
        kind = data[i + 4 : i + 8]
        out.append((kind, data[i + 8 : i + 8 + length]))
        i += 12 + length
    return out


def with_source(png: bytes, digest: str) -> bytes:
    """``png`` with a ``tEXt`` chunk ``openfactory-source`` holding ``digest``, after IHDR."""
    body = KEYWORD + b"\x00" + digest.encode("ascii")
    chunk = struct.pack(">I", len(body)) + b"tEXt" + body
    chunk += struct.pack(">I", zlib.crc32(b"tEXt" + body) & 0xFFFFFFFF)
    ihdr_end = len(PNG_SIGNATURE) + 8 + 13 + 4
    return png[:ihdr_end] + chunk + png[ihdr_end:]


def source_digest(png: Path) -> str | None:
    """The SVG digest recorded in a rendered PNG, or None."""
    for kind, data in chunks(png.read_bytes()):
        if kind == b"tEXt" and data.startswith(KEYWORD + b"\x00"):
            return data[len(KEYWORD) + 1 :].decode("ascii")
    return None


def size_of(svg: Path) -> tuple[int, int]:
    head = svg.read_text(encoding="utf-8")[:600]
    m = re.search(r'width="(\d+)" height="(\d+)"', head)
    if m is None:
        raise ValueError(f"{svg}: no width and height on the svg element")
    return int(m.group(1)), int(m.group(2))


def render(svgs: list[Path]) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            for svg in svgs:
                width, height = size_of(svg)
                page = browser.new_page(viewport={"width": width, "height": height})
                page.goto(svg.resolve().as_uri())
                png = page.screenshot(clip={"x": 0, "y": 0, "width": width, "height": height})
                page.close()
                out = svg.with_suffix(".png")
                data = with_source(png, svg_digest(svg))
                if len(data) >= MAX_PNG:
                    raise SystemExit(f"{out}: {len(data)} bytes; a PNG must stay under 1 MB")
                out.write_bytes(data)
                print(f"wrote {out.relative_to(REPO)} ({len(data)} bytes, {width}x{height})")
        finally:
            browser.close()


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    svgs = [Path(a).resolve() for a in args] or sorted(SAMPLES.glob("*/*.svg"))
    render(svgs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
