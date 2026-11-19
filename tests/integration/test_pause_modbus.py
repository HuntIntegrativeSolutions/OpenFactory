"""PLAN 5.14h: what a protocol client sees while the simulation is paused. A Modbus client on the
sorter's server reads the simulator heartbeat change while the run goes, then, paused for two
seconds, reads it hold (it stops as it does when the simulation stops) while the server still
answers; a coil it writes while paused is taken into the I/O table at once but acts only at the
next tick (a step); resumed, the heartbeat changes again."""

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


def test_a_paused_run_holds_the_heartbeat_and_takes_a_plc_write_at_the_next_tick():
    loaded = load(SORTER)
    register_map = scene_register_map(load_scene(SORTER), "float32", scene_path=SORTER, write=False)
    beat = next(a.address for a in register_map.assignments if a.tag == HEARTBEAT_TAG)
    run_coil = next(a.address for a in register_map.assignments if a.tag == "CONV_01_RUN_DO")
    runtime = Runtime(loaded)
    modbus = ModbusServer(loaded.io, "float32", Endpoint(port=free_port()), register_map)
    runtime.protocols = ["Modbus TCP"]
    report: list[str] = []

    async def poll(client: AsyncModbusTcpClient, seconds: float) -> list[bool]:
        changes, last, end = [], None, time.monotonic() + seconds
        while time.monotonic() < end:
            value = (await client.read_discrete_inputs(beat, count=1)).bits[0]
            if value != last:
                changes.append(value)
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
            running = await poll(client, 1.6)
            report.append(f"running: {len(running) - 1} heartbeat changes in 1.6 s")
            assert len(running) - 1 >= 2
            status = await asyncio.to_thread(runtime.pause, True)
            assert status["paused"] and status["plc_attached"]
            held = loaded.tick
            paused = await poll(client, 2.0)
            report.append(f"paused: {len(paused) - 1} changes in 2.0 s, server still answering")
            assert len(paused) == 1 and loaded.tick == held  # the first read only
            # a PLC write while paused: in the table at once, acting only at the next tick
            assert loaded.io.get("CONV_01_RUN_DO") is False
            assert not (await client.write_coil(run_coil, True)).isError()
            assert loaded.io.get("CONV_01_RUN_DO") is True and loaded.tick == held
            belt = loaded.component("CONV_01")
            travel = belt.travel
            await asyncio.sleep(0.3)  # no tick: the belt does not move, its feedback stays off
            assert belt.travel == travel and loaded.io.get("CONV_01_RUN_DI") is False
            await asyncio.to_thread(runtime.step, 1)
            assert loaded.tick == held + 5 and loaded.io.get("CONV_01_RUN_DI") is True
            assert belt.travel > travel
            report.append(f"coil written at tick {held}; the belt ran from the step to {held + 5}")
            await asyncio.to_thread(runtime.pause, False)
            resumed = await poll(client, 1.6)
            report.append(f"resumed: {len(resumed) - 1} heartbeat changes in 1.6 s")
            assert len(resumed) - 1 >= 2
        finally:
            client.close()
            await modbus.shutdown()
            server_task.cancel()

    try:
        asyncio.run(session())
    finally:
        runtime.stop()
    print("\n".join(report))
