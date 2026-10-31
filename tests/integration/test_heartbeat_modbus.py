"""SIM_HEARTBEAT_DI over Modbus (ruled 2026-10-30, PLAN §6.1): a client polling the sorter's
heartbeat at its lockfile address sees it change every 0.5 s while the simulation runs in real
time, and sees it stop changing when the simulation stops while the server still answers."""

from __future__ import annotations

import asyncio
import socket
import time
from pathlib import Path

from pymodbus.client import AsyncModbusTcpClient

from openfactory.core.config import Endpoint
from openfactory.core.heartbeat import HEARTBEAT_TAG
from openfactory.core.loader import load
from openfactory.core.schema import load_scene
from openfactory.protocols.modbus import ModbusServer, scene_register_map
from openfactory.server.runtime import Runtime

SORTER = Path(__file__).resolve().parents[2] / "scenes" / "sorting-by-height.yaml"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_a_plc_sees_the_heartbeat_change_and_then_stop():
    loaded = load(SORTER)
    register_map = scene_register_map(load_scene(SORTER), "float32", scene_path=SORTER, write=False)
    address = next(a.address for a in register_map.assignments if a.tag == HEARTBEAT_TAG)
    assert address == 5  # the next free DI after the sorter's five, as locked
    runtime = Runtime(loaded)
    modbus = ModbusServer(loaded.io, "float32", Endpoint(port=free_port()), register_map)
    report: list[str] = []

    async def poll(client: AsyncModbusTcpClient, seconds: float) -> list[tuple[float, bool]]:
        changes, last, end = [], None, time.monotonic() + seconds
        while time.monotonic() < end:
            reply = await client.read_discrete_inputs(address, count=1)
            value = reply.bits[0]
            if value != last:
                changes.append((time.monotonic(), value))
                last = value
            await asyncio.sleep(0.02)
        return changes

    async def session() -> None:
        server_task = asyncio.create_task(modbus.serve_forever())
        await modbus.wait_ready()
        client = AsyncModbusTcpClient("127.0.0.1", port=modbus.endpoint.port, timeout=1)
        assert await client.connect()
        try:
            runtime.start()
            running = await poll(client, 2.6)
            gaps = [round(b[0] - a[0], 2) for a, b in zip(running, running[1:], strict=False)]
            report.append(f"running: {len(running) - 1} changes in 2.6 s, gaps {gaps} s")
            assert len(running) - 1 >= 4  # changes every 0.5 s of simulated (= real) time
            assert all(0.3 <= g <= 0.7 for g in gaps), gaps
            runtime.stop()
            stopped = await poll(client, 1.5)
            report.append(f"stopped: {len(stopped) - 1} changes in 1.5 s, server still answering")
            assert len(stopped) == 1  # the first read only: it never changes again
        finally:
            client.close()
            await modbus.shutdown()
            server_task.cancel()

    try:
        asyncio.run(session())
    finally:
        runtime.stop()
    print("\n".join(report))
