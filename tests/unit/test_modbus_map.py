"""Register allocation and analog encoding (PLAN §6.1, ADR-0003), no network."""

from __future__ import annotations

from pathlib import Path

import pytest

from openfactory.core.iotable import Tag
from openfactory.core.loader import load
from openfactory.protocols.modbus import allocate, decode, encode

EXAMPLE = Path(__file__).resolve().parents[2] / "scenes" / "sorting-by-height.yaml"


def test_allocation_sorted_by_kind_then_name_and_packed():
    tags = [
        Tag.new("B_AO", "AO"),
        Tag.new("Z_DI", "DI"),
        Tag.new("A_AO", "AO"),
        Tag.new("A_DI", "DI"),
        Tag.new("M_DO", "DO"),
        Tag.new("K_AI", "AI"),
    ]
    m = allocate(tags, "float32")
    assert [a.label for a in m.assignments] == [
        "DI:0",
        "DI:1",
        "CO:0",
        "IR:0-1",
        "HR:0-1",
        "HR:2-3",
    ]
    assert [a.tag for a in m.assignments] == ["A_DI", "Z_DI", "M_DO", "K_AI", "A_AO", "B_AO"]
    assert m.size == {"di": 2, "co": 1, "ir": 2, "hr": 4}
    m16 = allocate(tags, "int16_scaled")
    assert [a.label for a in m16.assignments][-2:] == ["HR:0", "HR:1"]


def test_allocation_is_stable_across_runs_and_input_order():
    a = allocate(load(EXAMPLE).io.tags(), "float32").assignments
    b = allocate(reversed(load(EXAMPLE).io.tags()), "float32").assignments
    assert a == b


def test_describe_lists_every_tag():
    text = allocate(load(EXAMPLE).io.tags(), "int16_scaled").describe()
    assert text.splitlines()[0] == "Modbus register map (int16_scaled, zero-based addresses):"
    assert "HR:0       CONV_01_SPD_AO" in text


@pytest.mark.parametrize("value", [0.0, 12.5, -3.75, 1e6])
def test_float32_round_trip(value):
    tag = Tag.new("X_AO", "AO", range=(0.0, 100.0))
    assert decode(tag, encode(tag, value, "float32"), "float32") == pytest.approx(value)


def test_int16_scaled_scaling_and_clamping():
    tag = Tag.new("LT_01_AI", "AI", range=(-1.0, 3.0))
    assert encode(tag, -1.0, "int16_scaled") == [0]
    assert encode(tag, 3.0, "int16_scaled") == [32767]
    assert encode(tag, 1.0, "int16_scaled") == [round(32767 / 2)]
    assert encode(tag, 9.0, "int16_scaled") == [32767]
    assert encode(tag, -9.0, "int16_scaled") == [0]
    assert decode(tag, [32767], "int16_scaled") == pytest.approx(3.0)
    assert decode(tag, [65535], "int16_scaled") == pytest.approx(3.0)  # unsigned over range
    assert decode(tag, [0], "int16_scaled") == pytest.approx(-1.0)


def test_non_finite_float_rejected():
    tag = Tag.new("X_AO", "AO")
    with pytest.raises(ValueError, match="non-finite"):
        decode(tag, [0x7F80, 0x0000], "float32")  # +inf


def test_hook_never_raises():
    import asyncio

    from pymodbus.constants import ExcCodes

    from openfactory.core.config import Endpoint
    from openfactory.protocols.modbus import ModbusServer

    server = ModbusServer(load(EXAMPLE).io, "float32", Endpoint(port=1502))
    # FC23 (read/write multiple registers) is routed to the hook by pymodbus but not supported here
    assert asyncio.run(server._hook(23, 0, 0, 1, [0, 0], None)) == ExcCodes.ILLEGAL_FUNCTION
    # an internal failure is reported to the client as a device failure, not raised
    assert asyncio.run(server._hook(3, 0, 0, 1, None, None)) == ExcCodes.DEVICE_FAILURE
