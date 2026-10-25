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
from openfactory.protocols.modbus import ModbusServer

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
    assert len(rows) == len(scene.tag_bindings()) == 8


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
    rows = {r["tag"]: r for r in io_list_rows(scene)}
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
    out = tmp_path / "io.csv"
    assert main([str(EXAMPLE), str(out)]) == 0
    assert out.read_text(encoding="utf-8") == io_list_csv(load_scene(EXAMPLE))
    assert main([str(EXAMPLE)]) == 0
    assert capsys.readouterr().out.endswith(io_list_csv(load_scene(EXAMPLE)))
    assert main([]) == 2
