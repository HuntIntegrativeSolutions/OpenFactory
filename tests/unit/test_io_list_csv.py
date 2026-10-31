"""PLAN 1.8: I/O list CSV export. Mirrors the /io-validate checks so CI enforces them."""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path

import pytest

from openfactory.core.config import Endpoint
from openfactory.core.loader import load
from openfactory.core.schema import load_scene
from openfactory.export.io_list_csv import COLUMNS, io_list_csv, io_list_rows, main
from openfactory.protocols.modbus import ModbusServer, lockfile_path, scene_register_map

EXAMPLE = Path(__file__).resolve().parents[2] / "scenes" / "sorting-by-height.yaml"

# Column names /io-validate accepts (~/.claude/skills/io-validate/SKILL.md, Step 3)
IO_VALIDATE_ALIASES = {
    "tag_name": {"tag_name", "tag", "tagname", "tag name"},
    "io_type": {"io_type", "type", "io type", "signal_type"},
    "address": {"address", "io_address", "physical_address", "rack_address"},
    "description": {"description", "desc", "tag_description"},
}
TAG_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,39}$")


def parse(text: str) -> tuple[list[str], list[dict[str, str]]]:
    reader = csv.DictReader(io.StringIO(text))
    return list(reader.fieldnames or []), list(reader)


@pytest.fixture
def scene():
    return load_scene(EXAMPLE)


def test_header_and_row_count(scene):
    header, rows = parse(io_list_csv(scene))
    assert tuple(header) == COLUMNS
    assert len(scene.tag_bindings()) == 11
    assert len(rows) == 11 + 1  # every bound tag, plus the simulator's SIM_HEARTBEAT_DI
    [beat] = [r for r in rows if r["tag"] == "SIM_HEARTBEAT_DI"]
    assert (beat["io_type"], beat["modbus_address"], beat["component"]) == ("DI", "DI:5", "")


def test_passes_io_validate_checks(scene):
    header, rows = parse(io_list_csv(scene))
    lower = {h.lower() for h in header}
    for required, aliases in IO_VALIDATE_ALIASES.items():
        assert lower & aliases, f"/io-validate requires a {required!r} column"
    tags = [r["tag"] for r in rows]
    assert len(tags) == len(set(tags)), "E001 duplicate tag"
    assert all(TAG_RE.match(t) for t in tags), "E002 tag format"
    assert {r["io_type"] for r in rows} <= {"DI", "DO", "AI", "AO"}, "E003 io_type"
    assert all(r["tag"].strip() and r["io_type"].strip() and r["description"].strip() for r in rows)
    assert all(r["address"] == "" for r in rows)  # blank: no W002/W003 address warnings


def test_rows_are_decodable(scene):
    rows = {r["tag"]: r for r in io_list_rows(scene, scene_path=EXAMPLE, write_lock=False)}
    speed = rows["CONV_01_SPD_AO"]
    assert speed["io_type"] == "AO" and speed["dtype"] == "float32"
    assert speed["modbus_address"] == "HR:0-1"
    assert speed["modbus_encoding"] == "float32 high word first"
    assert (speed["units"], speed["range_min"], speed["range_max"]) == ("%", "0", "100")
    assert speed["description"] == "Infeed Belt Conveyor — Sorting Line — Speed command"
    pe = rows["PE_01_DI"]
    assert (pe["modbus_address"], pe["modbus_encoding"], pe["units"]) == ("DI:3", "bit", "")
    assert pe["component"] == "PE_01"


def test_int16_scaled_encoding_column(scene):
    rows = {r["tag"]: r for r in io_list_rows(scene, "int16_scaled")}
    assert rows["CONV_01_SPD_AO"]["modbus_address"] == "HR:0"
    assert rows["CONV_01_SPD_AO"]["modbus_encoding"] == "int16_scaled 0-32767"


@pytest.mark.parametrize("encoding", ["float32", "int16_scaled"])
def test_csv_addresses_match_the_server_allocation(scene, encoding):
    server = ModbusServer(load(EXAMPLE).io, encoding, Endpoint(port=1502))
    csv_map = {r["tag"]: r["modbus_address"] for r in io_list_rows(scene, encoding)}
    assert csv_map == {a.tag: a.label for a in server.map.assignments}


def test_cli_writes_file(tmp_path, capsys):
    scene_path = tmp_path / EXAMPLE.name  # a copy: the CLI writes the lockfile next to the scene
    scene_path.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    out = tmp_path / "io.csv"
    assert main([str(scene_path), str(out)]) == 0
    assert out.read_text(encoding="utf-8") == io_list_csv(load_scene(EXAMPLE))
    assert lockfile_path(scene_path, "float32").is_file()
    assert main([str(scene_path)]) == 0
    assert capsys.readouterr().out.endswith(io_list_csv(load_scene(EXAMPLE)))
    assert main([]) == 2


def test_csv_honors_the_lockfile_like_the_server(tmp_path):
    """Adding a tag that sorts early: CSV and server both keep the locked addresses (ADR-0004)."""
    scene_path = tmp_path / EXAMPLE.name
    text = EXAMPLE.read_text(encoding="utf-8")
    scene_path.write_text(text, encoding="utf-8")
    first = io_list_rows(load_scene(scene_path), scene_path=scene_path)  # writes the lockfile
    before = {r["tag"]: r["modbus_address"] for r in first}
    scene_path.write_text(
        text + "  - id: PE_00\n    type: photo_eye\n    description: Infeed Entry Photo Eye\n"
        "    position: [0.6, 0.15, 0]\n    beam_length: 0.6\n    io:\n      detect: PE_00_DI\n",
        encoding="utf-8",
    )
    scene = load_scene(scene_path)
    rows = {r["tag"]: r["modbus_address"] for r in io_list_rows(scene, scene_path=scene_path)}
    assert {tag: rows[tag] for tag in before} == before  # nothing renumbered
    assert rows["PE_00_DI"] == f"DI:{len([t for t in before if t.endswith('_DI')])}"
    loaded = load(scene_path)
    register_map = scene_register_map(scene, scene_path=scene_path, write=False)
    server = ModbusServer(loaded.io, "float32", Endpoint(port=1502), register_map)
    assert rows == {a.tag: a.label for a in server.map.assignments}
