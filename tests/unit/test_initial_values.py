"""Housekeeping 2: ``initial`` on DO/AO bindings seeds the I/O table before any PLC write."""

from __future__ import annotations

import copy
import socket
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from openfactory.core.config import Endpoint
from openfactory.core.iotable import Writer
from openfactory.core.loader import build, load
from openfactory.core.schema import Scene, load_scene
from openfactory.protocols.modbus import ModbusServer, allocate

EXAMPLE = Path(__file__).resolve().parents[2] / "scenes" / "sorting-by-height.yaml"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def data() -> dict:
    return yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))


def _io(data: dict, cid: str) -> dict:
    return next(c for c in data["components"] if c["id"] == cid)["io"]


def test_shipped_scene_starts_both_belts_at_full_speed_before_any_plc_write():
    loaded = load(EXAMPLE)
    assert loaded.io.get("CONV_01_SPD_AO") == 100.0
    assert loaded.io.get("CONV_02_SPD_AO") == 100.0
    assert loaded.io.get("CONV_01_RUN_DO") is False  # no initial: the kind's default
    loaded.io.set("CONV_01_RUN_DO", True, Writer.PLC)
    assert loaded.component("CONV_01").target_speed() == pytest.approx(0.5)  # 100 % of 0.5 m/s


def test_plc_write_replaces_the_initial_value():
    loaded = load(EXAMPLE)
    loaded.io.set("CONV_01_SPD_AO", 40.0, Writer.PLC)
    assert loaded.io.get("CONV_01_SPD_AO") == 40.0


@pytest.mark.parametrize("encoding", ["float32", "int16_scaled"])
def test_modbus_reads_the_initial_value_before_any_write(encoding):
    loaded = load(EXAMPLE)
    server = ModbusServer(
        loaded.io, encoding, Endpoint(port=1502), allocate(loaded.io.tags(), encoding)
    )
    words = server._words("hr", loaded.io.values())
    a = server.map.by_tag["CONV_01_SPD_AO"]
    expected = [0x42C8, 0x0000] if encoding == "float32" else [32767]  # 100.0 / 100 %
    assert words[a.address : a.address + a.width] == expected


def test_do_initial_true(data):
    _io(data, "EMT_01")["enable"] = {"tag": "EMT_01_EN_DO", "initial": True}
    loaded = build(Scene.model_validate(data))
    assert loaded.io.get("EMT_01_EN_DO") is True


def test_initial_with_pinned_address(data):
    _io(data, "CONV_01")["run"] = {"tag": "CONV_01_RUN_DO", "address": 9, "initial": True}
    binding = {b.name: b for b in Scene.model_validate(data).tag_bindings()}["CONV_01_RUN_DO"]
    assert (binding.address, binding.initial) == (9, True)


@pytest.mark.parametrize(
    ("cid", "port", "value", "message"),
    [
        ("PE_01", "detect", {"tag": "PE_01_DI", "initial": True}, "Extra inputs"),  # DI: no
        ("CONV_01", "running", {"tag": "CONV_01_RUN_DI", "initial": True}, "Extra inputs"),
        ("CONV_01", "speed", {"tag": "CONV_01_SPD_AO", "initial": 150}, "outside the port's range"),
        ("CONV_01", "speed", {"tag": "CONV_01_SPD_AO", "initial": -1}, "outside the port's range"),
        ("CONV_01", "run", {"tag": "CONV_01_RUN_DO", "initial": "maybe"}, "valid boolean"),
    ],
)
def test_bad_initial_values_are_rejected(data, cid, port, value, message):
    bad = copy.deepcopy(data)
    _io(bad, cid)[port] = value
    with pytest.raises(ValidationError, match=message):
        Scene.model_validate(bad)


def test_force_overrides_initial(tmp_path, strip_ansi):
    from typer.testing import CliRunner

    from openfactory.cli import app

    scene = tmp_path / EXAMPLE.name
    scene.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    result = CliRunner().invoke(
        app,
        [
            "run",
            str(scene),
            "--as-fast-as-possible",
            "--duration",
            "0.1",
            "--port",
            str(_free_port()),
            "--force",
            "CONV_01_SPD_AO=25",
        ],
    )
    assert result.exit_code == 0, strip_ansi(result.output)
    assert "forced CONV_01_SPD_AO = 25.0" in strip_ansi(result.output)


def test_json_schema_offers_initial_on_do_and_ao_only():
    schema = Scene.model_json_schema()["$defs"]
    assert "initial" in schema["DoTagSpec"]["properties"]
    assert "initial" in schema["AoTagSpec"]["properties"]
    assert "initial" not in schema["DiTagSpec"]["properties"]
    assert load_scene(EXAMPLE)  # the shipped scene uses the mapping form and validates
