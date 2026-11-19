"""PLAN 5.1c: the sample drawing set, ``docs/samples/wash-skid/`` (design note §3.7)."""

from __future__ import annotations

import csv
import importlib.util
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml
from typer.testing import CliRunner

from openfactory import takeoff as tk
from openfactory.cli import app as cli
from openfactory.export.io_list_csv import COLUMNS

REPO = Path(__file__).resolve().parents[2]
SET = REPO / "docs" / "samples" / "wash-skid"
SHEETS = ("P-001", "L-001")
#: SVG elements a sheet may use: primitives and text only, no image, symbol, use or script.
PRIMITIVES = {"svg", "title", "rect", "line", "polyline", "polygon", "circle", "path", "text"}


def _tool(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_files_are_there_with_their_license_and_sizes() -> None:
    for name in ("P-001.svg", "P-001.png", "L-001.svg", "L-001.png", "IO-001.csv",
                 "wash-skid.takeoff.yaml", "LICENSE.txt"):  # fmt: skip
        assert (SET / name).is_file(), name
    license_text = (SET / "LICENSE.txt").read_text(encoding="utf-8")
    assert "License: CC-BY-4.0" in license_text and "our own invention" in license_text
    for sheet in SHEETS:
        svg = (SET / f"{sheet}.svg").read_bytes()
        assert len(svg) < 100_000 and b"CC-BY-4.0" in svg
        assert (SET / f"{sheet}.png").stat().st_size < 1_000_000


def test_the_svgs_are_current_and_drawn_from_primitives_only() -> None:
    assert _tool("make_sample_sheets").main(["--check"]) == 0
    for sheet in SHEETS:
        root = ET.parse(SET / f"{sheet}.svg").getroot()
        tags = {el.tag.split("}")[-1] for el in root.iter()}
        assert tags <= PRIMITIVES, (sheet, tags - PRIMITIVES)
        text = (SET / f"{sheet}.svg").read_text(encoding="utf-8")
        assert "href" not in text and "<script" not in text


def test_each_png_records_its_svg_digest_and_size() -> None:
    """A render is not byte-for-byte the same on every machine (fonts, the Chromium version),
    so this checks the digest the renderer recorded, and the PNG's size, not its pixels."""
    render = _tool("render_sheets")
    for sheet in SHEETS:
        svg, png = SET / f"{sheet}.svg", SET / f"{sheet}.png"
        assert render.source_digest(png) == render.svg_digest(svg), f"render {sheet} again"
        kind, ihdr = render.chunks(png.read_bytes())[0]
        assert kind == b"IHDR"
        assert struct.unpack(">II", ihdr[:8]) == render.size_of(svg)


def test_the_io_list_and_the_takeoff_agree() -> None:
    """The I/O list's rows and plc_tag column against the takeoff's I/O rows: equal, except an
    inferred row, whose question quotes what the list prints."""
    with (SET / "IO-001.csv").open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == [*COLUMNS, "plc_tag"]
        rows = list(reader)
    model = tk.load(SET / "wash-skid.takeoff.yaml").model
    by_tag = model.by_tag()
    assert len(rows) == len(model.io)
    for io in model.io:
        ref = next(r for r in io.ref if r.sheet == "IO-001")
        listed = rows[ref.row - 1]
        assert listed["tag"] == tk.scene_tag(io, by_tag), io.row
        assert listed["io_type"] == io.signal, io.row
        if io.confidence == "inferred":
            assert listed["plc_tag"] != io.plc_tag
            assert f"reads '{listed['plc_tag']}'" in " ".join(io.questions), io.row
        else:
            assert listed["plc_tag"] == io.plc_tag, io.row
        if io.range is not None:
            assert (float(listed["range_min"]), float(listed["range_max"])) == tuple(io.range)


def test_the_sample_checks_and_with_its_questions_answered_passes_strict(tmp_path) -> None:
    runner = CliRunner()
    path = SET / "wash-skid.takeoff.yaml"
    assert runner.invoke(cli, ["takeoff", "check", str(path)]).exit_code == 0
    assert runner.invoke(cli, ["takeoff", "check", str(path), "--strict"]).exit_code == 1
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    for key in ("equipment", "connections", "io"):
        for row in data[key]:
            row["questions"] = []
    answered = tmp_path / "answered.takeoff.yaml"
    answered.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    stale = runner.invoke(cli, ["takeoff", "check", str(answered), "--strict"])
    assert stale.exit_code == 1 and "rows changed since checking" in stale.output
    assert runner.invoke(cli, ["takeoff", "check", str(answered), "--mark", "QA"]).exit_code == 0
    done = runner.invoke(cli, ["takeoff", "check", str(answered), "--strict"])
    assert done.exit_code == 0, done.output
    assert "OK (--strict)" in done.output


#: A text's width is taken as 0.66 em per character (the sheets' capitals are about 0.65 em
#: wide as rendered), and it keeps 4 px clear of a vertical line on each side.
TEXT_EM, TEXT_CLEAR = 0.66, 4.0


def test_no_text_touches_a_vertical_line() -> None:
    """The review of 2026-11-17: "WASH WATER TANK" on P-001 touched the agitator's shaft."""
    touching = []
    for sheet in SHEETS:
        root = ET.fromstring((SET / f"{sheet}.svg").read_text(encoding="utf-8"))
        texts, lines = [], []
        for el in root.iter():
            tag = el.tag.rsplit("}", 1)[-1]
            if tag == "text":
                x, y, size = (float(el.get(k)) for k in ("x", "y", "font-size"))
                width = len(el.text) * TEXT_EM * size
                left = {"start": x, "middle": x - width / 2, "end": x - width}[
                    el.get("text-anchor", "start")
                ]
                texts.append((el.text, left - TEXT_CLEAR, left + width + TEXT_CLEAR,
                              y - 0.75 * size, y))  # fmt: skip
            elif tag == "line" and el.get("x1") == el.get("x2"):
                y1, y2 = sorted((float(el.get("y1")), float(el.get("y2"))))
                lines.append((float(el.get("x1")), y1, y2))
        touching += [(sheet, text, x) for text, x0, x1, top, base in texts
                     for x, y1, y2 in lines if x0 < x < x1 and y1 < base and y2 > top]  # fmt: skip
    assert touching == []
