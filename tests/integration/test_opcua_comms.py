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

#: The injected latency (ms), large next to what a healthy request takes on a loaded machine, so
#: the wall-clock bounds below tell the cases apart with room to spare. At 300 ms, with a 200 ms
#: healthy bound, a healthy read took 257 ms with every core busy (PLAN 2.9, the flaky test).
LATENCY_MS = 1000
#: A healthy request answers well inside the latency a delayed one waits.
HEALTHY_MS = LATENCY_MS / 2
#: A delayed request waits the latency once, not twice.
DELAYED_MS = (LATENCY_MS - 10, 2 * LATENCY_MS - 100)
#: Client settings for a server under that latency: asyncua's client probes the server every
#: ``watchdog_intervall`` seconds (default 1) with that as the probe's timeout, so a latency at or
#: above it reads as a dead server and the client disconnects. The request timeout waits it out.
PATIENT = {"timeout": 3 * LATENCY_MS / 1000, "watchdog_intervall": 3 * LATENCY_MS / 1000}


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
            assert value is True and ms < HEALTHY_MS
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
            assert value is True and ms < HEALTHY_MS
            report.append(f"cleared: after reconnecting, read answered in {ms:.0f} ms")

            # latency: reads and writes each answered that much later; a late write still lands
            await http.post("/api/fault", json={"comms": {"latency_ms": LATENCY_MS}})
            client2 = Client(server.url, **PATIENT)  # waits out the delay
            await client2.connect()
            try:
                node = client2.get_node(ua.NodeId("CONV_01_RUN_DI", ns))
                _, read_ms = await timed(node.read_value())
                _, write_ms = await timed(
                    client2.get_node(ua.NodeId("EMT_01_EN_DO", ns)).write_value(true)
                )
                # requests in flight together each wait the latency from their own arrival, as on a
                # slow network: not one behind the other (asyncua handles a connection's messages
                # in turn, and two reads used to take twice the latency)
                both, both_ms = await timed(asyncio.gather(node.read_value(), node.read_value()))
                assert both == [True, True]
            finally:
                await client2.disconnect()
            report.append(
                f"latency {LATENCY_MS} ms: read {read_ms:.0f}, write {write_ms:.0f}, "
                f"two reads together {both_ms:.0f} ms"
            )
            for ms in (read_ms, write_ms, both_ms):
                assert DELAYED_MS[0] <= ms < DELAYED_MS[1], ms
            assert loaded.io.get("EMT_01_EN_DO") is True
            await http.delete("/api/fault/comms")
            events = [e for e in runtime.events if e["type"] == "comms"]
            assert [e["params"] for e in events] == [
                {"drop": True, "latency_ms": 0.0},
                {"drop": False, "latency_ms": 0.0},
                {"drop": False, "latency_ms": float(LATENCY_MS)},
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
    """`run --opcua` serves both protocols; `--fault comms=latency:ms=N --fault-at 0` delays a
    Modbus read and an OPC UA read alike, and the recording carries the comms event. The run is
    stopped with SIGTERM once probed (a clean stop: the recording is closed whole), so a slow
    start on a busy machine cannot outlast it."""
    from pymodbus.client import AsyncModbusTcpClient

    from openfactory.record import read_recording

    modbus_port, opcua_port = free_port(), free_port()
    rec = tmp_path / "run.ofrec"
    proc = subprocess.Popen(
        [sys.executable, "-m", "openfactory", "run", str(SORTER), "--headless", "--opcua",
         "--port", str(modbus_port), "--opcua-port", str(opcua_port), "--duration", "300",
         "--fault", f"comms=latency:ms={LATENCY_MS}", "--fault-at", "0", "--record", str(rec),
         "-q"],
        cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )  # fmt: skip

    async def probe() -> tuple[float, float]:
        url = f"opc.tcp://127.0.0.1:{opcua_port}/openfactory/"
        deadline = time.monotonic() + 60  # a hang guard: asyncua took over 20 s on a busy machine
        client = Client(url, **PATIENT)
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
        mb = AsyncModbusTcpClient(
            "127.0.0.1", port=modbus_port, timeout=3 * LATENCY_MS / 1000, retries=0
        )
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
        proc.terminate()
        out, _ = proc.communicate(timeout=60)
    print(
        f"--fault comms=latency:ms={LATENCY_MS}: OPC UA read {opcua_ms:.0f} ms, "
        f"Modbus {modbus_ms:.0f} ms"
    )
    assert proc.returncode == 0, out
    for ms in (opcua_ms, modbus_ms):
        assert DELAYED_MS[0] <= ms < DELAYED_MS[1], ms
    recording = read_recording(rec)
    comms = [event for _tick, event in recording.events if event.get("type") == "comms"]
    assert comms and comms[0]["params"] == {"drop": False, "latency_ms": float(LATENCY_MS)}
