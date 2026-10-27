"""PLAN 2.5: each new component's ports work over the wire. A Modbus client writes the component's
commands as a PLC would, the scene steps, and the client reads the feedback back."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from pymodbus.client import AsyncModbusTcpClient

from openfactory.core.config import Endpoint
from openfactory.core.loader import LoadedScene, load
from openfactory.protocols.modbus import ModbusServer, allocate

SCENES = Path(__file__).resolve().parents[1] / "scenes"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Plc:
    """A Modbus client addressing tags by name through the server's register map."""

    def __init__(self, client: AsyncModbusTcpClient, server: ModbusServer) -> None:
        self.client, self.map = client, server.map

    async def coil(self, tag: str, value: bool) -> None:
        await self.client.write_coil(self.map.by_tag[tag].address, value)

    async def di(self, tag: str) -> bool:
        return (await self.client.read_discrete_inputs(self.map.by_tag[tag].address, count=1)).bits[
            0
        ]

    async def ir(self, tag: str) -> float:
        a = self.map.by_tag[tag]
        words = (await self.client.read_input_registers(a.address, count=a.width)).registers
        return self.client.convert_from_registers(words, self.client.DATATYPE.FLOAT32)


def over_modbus(loaded: LoadedScene, session: Callable[[Plc], Awaitable[Any]]) -> Any:
    async def main() -> Any:
        server = ModbusServer(
            loaded.io, "float32", Endpoint(port=free_port()), allocate(loaded.io.tags(), "float32")
        )
        task = asyncio.create_task(server.serve_forever())
        try:
            await server.wait_ready()
            client = AsyncModbusTcpClient("127.0.0.1", port=server.endpoint.port)
            assert await client.connect()
            try:
                return await session(Plc(client, server))
            finally:
                client.close()
        finally:
            await server.shutdown()
            task.cancel()

    return asyncio.run(main())


def steps(loaded: LoadedScene, n: int) -> None:
    for _ in range(n):
        loaded.step(0.02)


def test_stopper_raise_coil_and_limit_inputs():
    loaded = load(SCENES / "stopper-queue.yaml")

    async def session(plc: Plc) -> list[tuple[bool, bool]]:
        steps(loaded, 1)  # limit switches are written from the first step, as on the pusher
        seen = [(await plc.di("STP_01_UP_DI"), await plc.di("STP_01_DN_DI"))]
        await plc.coil("STP_01_RAISE_DO", True)
        steps(loaded, 10)  # raise_time_s 0.2
        seen.append((await plc.di("STP_01_UP_DI"), await plc.di("STP_01_DN_DI")))
        await plc.coil("STP_01_RAISE_DO", False)
        steps(loaded, 5)
        seen.append((await plc.di("STP_01_UP_DI"), await plc.di("STP_01_DN_DI")))
        return seen

    seen = over_modbus(loaded, session)
    print(f"STP_01 (raised, lowered) over Modbus: first tick, raised 0.2 s, half down: {seen}")
    assert seen == [(False, True), (True, False), (False, False)]
