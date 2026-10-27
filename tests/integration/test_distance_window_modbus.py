"""PLAN 2.5 / 2.17 Done-when, over the wire: a distance sensor's windowed switching output.

tests/scenes/distance-window.yaml runs with its belt and emitter on. A Modbus client polls, every
5 ticks for 20 s, the windowed DI (FC02) and the distance AI (FC04, int16_scaled over 0 to 0.8 m).
The DI must be true exactly while a tall box is under the sensor, and the AI must be the core's
distance scaled to raw counts.
"""

from __future__ import annotations

import asyncio
import socket
from pathlib import Path

from pymodbus.client import AsyncModbusTcpClient

from openfactory.core.config import Endpoint
from openfactory.core.iotable import Writer
from openfactory.core.loader import load
from openfactory.protocols.modbus import ModbusServer, scene_register_map

SCENE = Path(__file__).resolve().parents[1] / "scenes" / "distance-window.yaml"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_windowed_switching_output_tracks_tall_boxes_over_modbus():
    loaded = load(SCENE)
    register_map = scene_register_map(loaded.scene, scene_path=SCENE, write=False)
    assert register_map.encoding == "int16_scaled"
    assert register_map.ranges == {"DS_01_DIST_AI": (0.0, 0.8)}
    for tag in ("CONV_01_RUN_DO", "EMT_01_EN_DO"):
        loaded.io.set(tag, True, Writer.PLC)
    sensor = loaded.component("DS_01")
    samples: list[tuple[bool, int, float, str | None]] = []

    def under() -> str | None:
        hits = [p.name for p in loaded.world.parts.values() if abs(p.position[0] - 1.5) < 0.15]
        return hits[0] if hits else None

    async def main() -> None:
        server = ModbusServer(loaded.io, "int16_scaled", Endpoint(port=free_port()), register_map)
        task = asyncio.create_task(server.serve_forever())
        try:
            await server.wait_ready()
            client = AsyncModbusTcpClient("127.0.0.1", port=server.endpoint.port)
            assert await client.connect()
            di = register_map.by_tag["DS_01_TALL_DI"].address
            ir = register_map.by_tag["DS_01_DIST_AI"].address
            for _ in range(200):  # 20 s
                for _ in range(5):
                    loaded.step(0.02)
                tall = (await client.read_discrete_inputs(di, count=1)).bits[0]
                raw = (await client.read_input_registers(ir, count=1)).registers[0]
                samples.append((tall, raw, sensor.distance, under()))
            client.close()
        finally:
            await server.shutdown()
            task.cancel()

    asyncio.run(main())
    for tall, raw, distance, _ in samples:
        assert raw == round(distance / 0.8 * 32767)  # int16_scaled over the sensor's range
        assert tall == (0.1 <= distance <= 0.3)  # the window
    seen = {name for tall, _, _, name in samples if tall}
    readings = sorted({round(d, 3) for _, _, d, _ in samples})
    edges = sum(1 for a, b in zip(samples, samples[1:], strict=False) if b[0] and not a[0])
    talls = sum(1 for p in loaded.world.removed.elements() if p == "tall")
    print(f"distance readings (m): {readings}")
    print(f"DS_01_TALL_DI rising edges over Modbus: {edges}; tall boxes removed: {talls}; "
          f"names under the sensor while the DI was on: {seen}")  # fmt: skip
    assert seen == {"tall"} and edges >= talls >= 3
    assert {0.2, 0.4, 0.6} <= set(readings)  # tall top, short top, bare belt
