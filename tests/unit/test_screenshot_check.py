"""PLAN 1.13: the screenshot tool's PNG decoder and blank-image check (no browser)."""

from __future__ import annotations

import importlib.util
import struct
import zlib
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[2] / "tools" / "screenshot.py"
spec = importlib.util.spec_from_file_location("screenshot_tool", TOOL)
screenshot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(screenshot)

BG = (0x1F, 0x2A, 0x36)


def write_png(path: Path, pixels: list[list[tuple[int, int, int]]], filters=(0,)) -> Path:
    """An RGB PNG; row filter types cycle through ``filters`` (encoded naively for type 0/2)."""
    height, width = len(pixels), len(pixels[0])
    raw = bytearray()
    prev = bytes(width * 3)
    for y, row in enumerate(pixels):
        line = bytes(c for px in row for c in px)
        kind = filters[y % len(filters)]
        raw.append(kind)
        if kind == 0:
            raw += line
        elif kind == 2:  # Up
            raw += bytes((line[i] - prev[i]) & 0xFF for i in range(len(line)))
        prev = line

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw)))
        + chunk(b"IEND", b"")
    )
    return path


def image(fill, box=None, size=20):
    px = [[fill] * size for _ in range(size)]
    if box:
        color, n = box
        for y in range(n):
            for x in range(n):
                px[y][x] = color
    return px


def test_decoder_round_trips_filters(tmp_path):
    px = [[(x * 10, y * 10, (x + y) * 5) for x in range(8)] for y in range(6)]
    path = write_png(tmp_path / "a.png", px, filters=(0, 2))
    w, h, ch, data = screenshot.read_png(path)
    assert (w, h, ch) == (8, 6, 3)
    assert data == bytes(c for row in px for p in row for c in p)


@pytest.mark.parametrize(
    ("pixels", "ok", "word"),
    [
        (image(BG), False, "BLANK"),  # background only: the 1.13 scaffold failure mode
        (image((0, 0, 0)), False, "BLANK"),  # black WebGL canvas
        (image(BG, ((156, 163, 175), 6)), True, "OK"),  # 9 % grey component on background
    ],
)
def test_check_image(tmp_path, pixels, ok, word):
    passed, summary = screenshot.check_image(write_png(tmp_path / "x.png", pixels))
    assert passed is ok and summary.startswith(word)


def test_real_playwright_png_decodes(prerequisite):
    shots = sorted((TOOL.parent / "screenshots").glob("*.png"))
    prerequisite(bool(shots), "no screenshot taken yet (tools/ci_local.sh takes one before pytest)")
    stats = screenshot.image_stats(shots[0])
    assert stats["width"] > 0 and 0 <= stats["foreground"] <= 1
