"""PLAN 1.7: Modbus round trip through a real pymodbus client against the live-bound server."""

from __future__ import annotations

import asyncio
import socket
import struct
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from pymodbus.client import AsyncModbusTcpClient

from openfactory.core.config import Endpoint
from openfactory.core.iotable import IOTable, Tag, Writer
from openfactory.core.loader import LoadedScene, load
from openfactory.protocols.modbus import ModbusServer

EXAMPLE = Path(__file__).resolve().parents[2] / "scenes" / "sorting-by-height.yaml"
DT = 0.02


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run_against(
    io: IOTable,
    encoding: str,
    scenario: Callable[[AsyncModbusTcpClient, ModbusServer], Awaitable[None]],
) -> None:
    """Start the server on an ephemeral loopback port, run the scenario with a client, stop."""

    async def main() -> None:
        server = ModbusServer(io, encoding, Endpoint(bind="127.0.0.1", port=free_port()))
        task = asyncio.create_task(server.serve_forever())
        try:
            await server.wait_ready()
            client = AsyncModbusTcpClient("127.0.0.1", port=server.endpoint.port)
            assert await client.connect()
            try:
                await scenario(client, server)
            finally:
                client.close()
        finally:
            await server.shutdown()
            task.cancel()

    asyncio.run(main())


@pytest.fixture
def scene() -> LoadedScene:
    return load(EXAMPLE)


# --- Done when: write a coil, see CONV_01_RUN_DO change; read a DI after the sim trips it --------


def test_coil_write_reaches_iotable_and_sim_driven_di_reads_back(scene):
    async def scenario(client, server):
        co = server.map.by_tag["CONV_01_RUN_DO"].address
        di = server.map.by_tag["CONV_01_RUN_DI"].address
        pe = server.map.by_tag["PE_01_DI"].address

        assert scene.io.get("CONV_01_RUN_DO") is False
        wr = await client.write_coil(co, True, device_id=1)
        assert not wr.isError()
        assert scene.io.get("CONV_01_RUN_DO") is True  # coil landed in the I/O table

        rr = await client.read_discrete_inputs(di, count=1, device_id=1)
        assert rr.bits[0] is False  # the simulation has not stepped yet
        scene.step(DT)  # conveyor component sets its running feedback
        rr = await client.read_discrete_inputs(di, count=1, device_id=1)
        assert rr.bits[0] is True

        scene.world.spawn("short", (0.3, 0.2, 0.3), (1.5, 0.1, 0.0))  # box at the photo eye
        scene.step(DT)
        scene.step(DT)  # 20 ms debounce
        rr = await client.read_discrete_inputs(0, count=4, device_id=1)
        assert rr.bits[pe] is True
        print(f"coil CO:{co} -> CONV_01_RUN_DO=True; DI:{di} running=True; DI:{pe} PE_01=True")

    run_against(scene.io, "float32", scenario)


def test_fc05_is_idempotent_and_out_of_range_writes_are_rejected(scene):
    changes = []
    scene.io.on_change(lambda name, *_: changes.append(name))

    async def scenario(client, server):
        for _ in range(3):
            assert not (await client.write_coil(0, True, device_id=1)).isError()
        assert changes.count("CONV_01_RUN_DO") == 1
        wr = await client.write_coil(server.map.size["co"], True, device_id=1)  # one past the end
        assert wr.isError() and wr.exception_code == 2
        wr = await client.write_coils(1, [True, True, True], device_id=1)  # runs past the end
        assert wr.isError() and wr.exception_code == 2
        assert scene.io.get("CYL_01_EXT_DO") is False  # all or nothing
        rr = await client.read_input_registers(0, count=1, device_id=1)  # no AI tags in scene
        assert rr.isError()

    run_against(scene.io, "float32", scenario)


# --- analog encodings (ADR-0003) -----------------------------------------------------------------


def test_float32_holding_register_high_word_first(scene):
    async def scenario(client, server):
        hr = server.map.by_tag["CONV_01_SPD_AO"].address
        hi, lo = struct.unpack(">HH", struct.pack(">f", 75.5))
        assert not (await client.write_registers(hr, [hi, lo], device_id=1)).isError()
        assert scene.io.get("CONV_01_SPD_AO") == pytest.approx(75.5)
        rr = await client.read_holding_registers(hr, count=2, device_id=1)
        assert rr.registers == [hi, lo]
        value = client.convert_from_registers(
            rr.registers, data_type=client.DATATYPE.FLOAT32, word_order="big"
        )
        assert value == pytest.approx(75.5)

        # FC06 to the low word only: merged with the current high word
        lo2 = 0x8000
        merged = struct.unpack(">f", struct.pack(">HH", hi, lo2))[0]
        assert not (await client.write_register(hr + 1, lo2, device_id=1)).isError()
        assert scene.io.get("CONV_01_SPD_AO") == pytest.approx(merged, rel=1e-6)
        assert merged != pytest.approx(75.5, abs=1e-6)
        # FC16 starting mid-pair (low word only) behaves the same
        assert not (await client.write_registers(hr + 1, [lo], device_id=1)).isError()
        assert scene.io.get("CONV_01_SPD_AO") == pytest.approx(75.5)
        assert not (await client.write_register(hr + 1, lo2, device_id=1)).isError()

        # NaN bit pattern rejected with ILLEGAL DATA VALUE, value unchanged
        wr = await client.write_registers(hr, [0x7FC0, 0x0000], device_id=1)
        assert wr.isError() and wr.exception_code == 3
        assert scene.io.get("CONV_01_SPD_AO") == pytest.approx(merged, rel=1e-6)

    run_against(scene.io, "float32", scenario)


def test_int16_scaled_holding_register_clamps_over_range(scene):
    async def scenario(client, server):
        hr = server.map.by_tag["CONV_01_SPD_AO"].address
        assert server.map.by_tag["CONV_01_SPD_AO"].width == 1
        await client.write_register(hr, 16384, device_id=1)
        assert scene.io.get("CONV_01_SPD_AO") == pytest.approx(16384 / 32767 * 100)
        await client.write_register(hr, 40000, device_id=1)  # unsigned %QW above 32767
        assert scene.io.get("CONV_01_SPD_AO") == pytest.approx(100.0)
        rr = await client.read_holding_registers(hr, count=1, device_id=1)
        assert rr.registers == [32767]
        await client.write_register(hr, 0, device_id=1)
        assert scene.io.get("CONV_01_SPD_AO") == 0.0

    run_against(scene.io, "int16_scaled", scenario)


@pytest.mark.parametrize(
    ("encoding", "expected"),
    [
        ("float32", list(struct.unpack(">HH", struct.pack(">f", 1.25)))),  # high word first
        ("int16_scaled", [round(1.25 / 2.0 * 32767)]),  # 0-2 m range
    ],
)
def test_input_register_encodings(encoding, expected):
    io = IOTable(
        [
            Tag.new("LT_01_AI", "AI", range=(0.0, 2.0), units="m"),
            Tag.new("LT_02_AI", "AI", range=(0.0, 2.0), units="m"),
        ]
    )
    io.set("LT_01_AI", 1.25, Writer.SIM)

    async def scenario(client, server):
        a = server.map.by_tag["LT_01_AI"]
        rr = await client.read_input_registers(a.address, count=a.width, device_id=1)
        assert rr.registers == expected
        io.set("LT_01_AI", 2.5, Writer.SIM)  # over range
        rr = await client.read_input_registers(a.address, count=a.width, device_id=1)
        if encoding == "int16_scaled":
            assert rr.registers == [32767]  # clamped
        else:
            assert client.convert_from_registers(
                rr.registers, data_type=client.DATATYPE.FLOAT32, word_order="big"
            ) == pytest.approx(2.5)

    run_against(io, encoding, scenario)


def test_server_binds_the_endpoint_it_is_given(scene):
    async def scenario(client, server):
        assert server.endpoint.bind == "127.0.0.1"
        assert client.connected

    run_against(scene.io, "float32", scenario)
