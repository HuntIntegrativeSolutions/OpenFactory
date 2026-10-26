"""PLAN 2.17 Done-when, over the wire: an inverted photo eye reads true with no part present."""

from __future__ import annotations

import asyncio
import socket
from pathlib import Path

import yaml
from pymodbus.client import AsyncModbusTcpClient

from openfactory.core.config import Endpoint
from openfactory.core.loader import build
from openfactory.core.schema import Scene
from openfactory.protocols.modbus import ModbusServer, allocate

EXAMPLE = Path(__file__).resolve().parents[2] / "scenes" / "sorting-by-height.yaml"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_inverted_photo_eye_reads_true_over_modbus_with_no_part():
    data = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    pe = next(c for c in data["components"] if c["id"] == "PE_01")
    pe["io"]["detect"] = {"tag": "PE_01_DI", "invert": True}
    loaded = build(Scene.model_validate(data))
    seen: list[bool] = []

    async def main() -> None:
        server = ModbusServer(
            loaded.io,
            "float32",
            Endpoint(port=free_port()),
            allocate(loaded.io.tags(), "float32"),
        )
        task = asyncio.create_task(server.serve_forever())
        try:
            await server.wait_ready()
            client = AsyncModbusTcpClient("127.0.0.1", port=server.endpoint.port)
            assert await client.connect()
            address = server.map.by_tag["PE_01_DI"].address
            seen.append((await client.read_discrete_inputs(address, count=1)).bits[0])  # at load
            for _ in range(25):
                loaded.step(0.02)
            seen.append((await client.read_discrete_inputs(address, count=1)).bits[0])  # 0.5 s
            client.close()
        finally:
            await server.shutdown()
            task.cancel()

    asyncio.run(main())
    assert loaded.world.parts == {} or all(p.position[0] < 1.2 for p in loaded.world.parts.values())
    print(f"PE_01_DI (inverted) over Modbus FC02, no part at the eye: {seen}")
    assert seen == [True, True]
