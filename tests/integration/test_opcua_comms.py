"""PLAN 2.13b Done-when, comms-loss injection on the OPC UA server: through /api/fault, `drop`
makes an asyncua client's reads (and a write) fail while the simulation keeps ticking, clearing it
brings answers back with current values (the client that gave up reconnects, as Ignition does),
and `latency_ms` delays reads and writes by that much; one comms fault covers the Modbus and
OPC UA servers of a run, and `--fault comms=...` reaches an OPC UA run and its recording."""

from __future__ import annotations

import asyncio
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

asyncua = pytest.importorskip("asyncua")  # the opcua extra; CI installs --all-extras

from asyncua import Client, ua  # noqa: E402

from openfactory.core.config import Endpoint  # noqa: E402
from openfactory.core.iotable import Writer  # noqa: E402
from openfactory.core.loader import load  # noqa: E402
from openfactory.protocols.opcua import NAMESPACE, OpcUaServer  # noqa: E402
from openfactory.server.app import create_app, server_config  # noqa: E402
from openfactory.server.runtime import Runtime  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
SORTER = REPO / "scenes" / "sorting-by-height.yaml"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_drop_and_latency_on_the_opcua_server_through_the_fault_api():
    loaded = load(SORTER)
    loaded.io.set("CONV_01_RUN_DO", True, Writer.PLC)
    runtime = Runtime(loaded)
    server = OpcUaServer(loaded.io, Endpoint(port=free_port()), scene_name="sorting-by-height")
    runtime.comms = server.comms
    web_endpoint = Endpoint(bind="127.0.0.1", port=free_port())
    web = uvicorn.Server(server_config(create_app(runtime, web_dist=None), web_endpoint))
    web_thread = threading.Thread(target=web.run, daemon=True)
    base = f"http://127.0.0.1:{web_endpoint.port}"
    report: list[str] = []

    async def session() -> None:
        task = asyncio.create_task(server.serve_forever())
        await server.wait_ready()
        client = Client(server.url, timeout=0.5)  # request timeout, seconds
        await client.connect()
        http = httpx.AsyncClient(base_url=base)
        ns = await client.get_namespace_index(NAMESPACE)
        run_di = client.get_node(ua.NodeId("CONV_01_RUN_DI", ns))
        enable = client.get_node(ua.NodeId("EMT_01_EN_DO", ns))
        true = ua.DataValue(ua.Variant(True, ua.VariantType.Boolean))

        async def timed(call):
            start = time.perf_counter()
            result = await call
            return result, (time.perf_counter() - start) * 1000

        try:
            value, ms = await timed(run_di.read_value())
            assert value is True and ms < 200
            report.append(f"healthy read: {ms:.0f} ms")

            # drop: no reply; the sim keeps ticking; a write in a dropped request is lost
            r = await http.post("/api/fault", json={"comms": {"drop": True}})
            assert r.json() == {"comms": {"drop": True, "latency_ms": 0.0}}
            tick0 = runtime.snapshot["tick"]
            start = time.perf_counter()
            with pytest.raises((TimeoutError, ua.UaError)):
                await run_di.read_value()
            report.append(
                f"dropped read: no reply; gave up at {(time.perf_counter() - start) * 1e3:.0f} ms"
            )
            with pytest.raises((TimeoutError, ua.UaError)):
                await enable.write_value(true)
            await asyncio.sleep(0.3)
            ticked = runtime.snapshot["tick"] - tick0
            assert ticked >= 40, ticked  # at least 0.8 s simulated across the two timeouts
            assert loaded.io.get("EMT_01_EN_DO") is False  # the dropped write never landed
            report.append(
                f"while dropped the sim ran {ticked} ticks; the dropped write was not applied"
            )
            assert (await http.get("/api/fault")).json()["comms"]["drop"] is True

            # clear: answers come back, current values; asyncua's client closed its connection
            # after the timeouts, so it reconnects (as Ignition's OPC UA client does on its own)
            assert (await http.delete("/api/fault/comms")).status_code == 200
            await client.disconnect()
            client = Client(server.url, timeout=0.5)
            await client.connect()
            run_di = client.get_node(ua.NodeId("CONV_01_RUN_DI", ns))
            value, ms = await timed(run_di.read_value())
            assert value is True and ms < 200
            report.append(f"cleared: after reconnecting, read answered in {ms:.0f} ms")

            # latency: reads and writes each answered that much later; a late write still lands
            await http.post("/api/fault", json={"comms": {"latency_ms": 300}})
            client2 = Client(server.url, timeout=2)  # a client that waits longer than the delay
            await client2.connect()
            try:
                node = client2.get_node(ua.NodeId("CONV_01_RUN_DI", ns))
                _, read_ms = await timed(node.read_value())
                _, write_ms = await timed(
                    client2.get_node(ua.NodeId("EMT_01_EN_DO", ns)).write_value(true)
                )
            finally:
                await client2.disconnect()
            report.append(f"latency 300 ms: read {read_ms:.0f}, write {write_ms:.0f} ms")
            for ms in (read_ms, write_ms):
                assert 290 <= ms <= 600, ms
            assert loaded.io.get("EMT_01_EN_DO") is True
            await http.delete("/api/fault/comms")
            events = [e for e in runtime.events if e["type"] == "comms"]
            assert [e["params"] for e in events] == [
                {"drop": True, "latency_ms": 0.0},
                {"drop": False, "latency_ms": 0.0},
                {"drop": False, "latency_ms": 300.0},
                {"drop": False, "latency_ms": 0.0},
            ]
        finally:
            await http.aclose()
            await client.disconnect()
            await server.shutdown()
            task.cancel()

    runtime.start()
    web_thread.start()
    deadline = time.monotonic() + 10
    while not web.started:
        assert time.monotonic() < deadline
        time.sleep(0.02)
    try:
        asyncio.run(session())
    finally:
        web.should_exit = True
        runtime.stop()
        web_thread.join(5)
    print("\n".join(report))


def test_one_comms_fault_covers_modbus_and_opcua_and_reaches_the_recording(tmp_path):
    """`run --opcua` serves both protocols; `--fault comms=latency:ms=400 --fault-at 0` delays a
    Modbus read and an OPC UA read alike, and the recording carries the comms event."""
    from pymodbus.client import AsyncModbusTcpClient

    from openfactory.record import read_recording

    modbus_port, opcua_port = free_port(), free_port()
    rec = tmp_path / "run.ofrec"
    proc = subprocess.Popen(
        [sys.executable, "-m", "openfactory", "run", str(SORTER), "--headless", "--opcua",
         "--port", str(modbus_port), "--opcua-port", str(opcua_port), "--duration", "12",
         "--fault", "comms=latency:ms=400", "--fault-at", "0", "--record", str(rec), "-q"],
        cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )  # fmt: skip

    async def probe() -> tuple[float, float]:
        url = f"opc.tcp://127.0.0.1:{opcua_port}/openfactory/"
        deadline = time.monotonic() + 20
        client = Client(url, timeout=3)
        while True:  # asyncua's server takes a second or two to listen
            try:
                await client.connect()
                break
            except (OSError, TimeoutError):
                assert time.monotonic() < deadline, "OPC UA server never listened"
                await asyncio.sleep(0.2)
        try:
            ns = await client.get_namespace_index(NAMESPACE)
            node = client.get_node(ua.NodeId("PE_01_DI", ns))
            start = time.perf_counter()
            await node.read_value()
            opcua_ms = (time.perf_counter() - start) * 1000
        finally:
            await client.disconnect()
        mb = AsyncModbusTcpClient("127.0.0.1", port=modbus_port, timeout=3, retries=0)
        assert await mb.connect()
        try:
            start = time.perf_counter()
            await mb.read_discrete_inputs(0, count=1)
            modbus_ms = (time.perf_counter() - start) * 1000
        finally:
            mb.close()
        return opcua_ms, modbus_ms

    try:
        opcua_ms, modbus_ms = asyncio.run(probe())
    finally:
        out, _ = proc.communicate(timeout=60)
    print(f"--fault comms=latency:ms=400: OPC UA read {opcua_ms:.0f} ms, Modbus {modbus_ms:.0f} ms")
    assert proc.returncode == 0, out
    assert 390 <= opcua_ms <= 900 and 390 <= modbus_ms <= 900
    recording = read_recording(rec)
    comms = [event for _tick, event in recording.events if event.get("type") == "comms"]
    assert comms and comms[0]["params"] == {"drop": False, "latency_ms": 400.0}
