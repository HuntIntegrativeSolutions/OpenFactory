"""PLAN 2.13 Done-when, comms-loss injection: through /api/fault, `drop` makes a Modbus client's
requests time out (and a write in a dropped request is not applied) while the simulation keeps
ticking, clearing it brings answers back with current values, and `latency_ms` delays each answer
by that much, once per request, reads and writes alike."""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException

from openfactory.core.config import Endpoint
from openfactory.core.iotable import Writer
from openfactory.core.loader import load
from openfactory.protocols.modbus import ModbusServer, allocate
from openfactory.server.app import create_app, server_config
from openfactory.server.runtime import Runtime

SORTER = Path(__file__).resolve().parents[2] / "scenes" / "sorting-by-height.yaml"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_drop_and_latency_through_the_fault_api():
    loaded = load(SORTER)
    loaded.io.set("CONV_01_RUN_DO", True, Writer.PLC)
    runtime = Runtime(loaded)
    modbus = ModbusServer(
        loaded.io, "float32", Endpoint(port=free_port()), allocate(loaded.io.tags(), "float32")
    )
    runtime.comms = modbus.comms
    web_endpoint = Endpoint(bind="127.0.0.1", port=free_port())
    web = uvicorn.Server(server_config(create_app(runtime, web_dist=None), web_endpoint))
    web_thread = threading.Thread(target=web.run, daemon=True)
    base = f"http://127.0.0.1:{web_endpoint.port}"
    di = modbus.map.by_tag["CONV_01_RUN_DI"].address
    co = modbus.map.by_tag["EMT_01_EN_DO"].address
    report: list[str] = []

    async def session() -> None:
        server_task = asyncio.create_task(modbus.serve_forever())
        await modbus.wait_ready()
        client = AsyncModbusTcpClient(
            "127.0.0.1", port=modbus.endpoint.port, timeout=0.5, retries=0
        )
        assert await client.connect()
        http = httpx.AsyncClient(base_url=base)

        async def timed(call):
            start = time.perf_counter()
            result = await call
            return result, (time.perf_counter() - start) * 1000

        try:
            healthy, ms = await timed(client.read_discrete_inputs(di, count=1))
            assert healthy.bits[0] is True and ms < 100
            report.append(f"healthy read: {ms:.0f} ms")

            # drop: no reply; the sim keeps ticking; a write in a dropped request is lost
            r = await http.post("/api/fault", json={"comms": {"drop": True}})
            assert r.json() == {"comms": {"drop": True, "latency_ms": 0.0}}
            tick0 = runtime.snapshot["tick"]
            start = time.perf_counter()
            with pytest.raises(ModbusException):
                await client.read_discrete_inputs(di, count=1)
            report.append(
                f"dropped read: no reply; gave up at {(time.perf_counter() - start) * 1e3:.0f} ms"
            )
            client.close()  # a timed-out client reconnects, as a PLC master does
            assert await client.connect()
            with pytest.raises(ModbusException):
                await client.write_coil(co, True)
            ticked = runtime.snapshot["tick"] - tick0
            assert ticked >= 40, ticked  # at least 0.8 s simulated across the two timeouts
            assert loaded.io.get("EMT_01_EN_DO") is False  # the dropped write never landed
            report.append(
                f"while dropped the sim ran {ticked} ticks; the dropped write was not applied"
            )
            assert (await http.get("/api/fault")).json()["comms"]["drop"] is True

            # clear: answers come back, current values
            assert (await http.delete("/api/fault/comms")).status_code == 200
            client.close()
            assert await client.connect()
            back, ms = await timed(client.read_discrete_inputs(di, count=1))
            assert back.bits[0] is True and ms < 100
            report.append(f"cleared: read answered in {ms:.0f} ms")

            # latency: each request answered that much later, once per request
            await http.post("/api/fault", json={"comms": {"latency_ms": 300}})
            _, read_ms = await timed(client.read_discrete_inputs(di, count=1))
            _, fc05_ms = await timed(client.write_coil(co, True))  # FC05: hook called twice
            _, fc03_ms = await timed(client.read_holding_registers(0, count=2))
            report.append(
                f"latency 300 ms: FC02 {read_ms:.0f}, FC05 {fc05_ms:.0f}, FC03 {fc03_ms:.0f} ms"
            )
            for ms in (read_ms, fc05_ms, fc03_ms):
                assert 290 <= ms <= 450, ms
            assert loaded.io.get("EMT_01_EN_DO") is True  # a late write still lands
            events = [e for e in runtime.events if e["type"] == "comms"]
            assert [e["params"] for e in events] == [
                {"drop": True, "latency_ms": 0.0},
                {"drop": False, "latency_ms": 0.0},
                {"drop": False, "latency_ms": 300.0},
            ]
            bad = await http.post("/api/fault", json={"comms": {"latency_ms": -5}})
            assert bad.status_code == 422
            both = await http.post(
                "/api/fault", json={"id": "CYL_01", "kind": "jammed", "comms": {}}
            )
            assert both.status_code == 422
        finally:
            await http.aclose()
            client.close()
            await modbus.shutdown()
            server_task.cancel()

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


def test_cli_rejects_a_bad_comms_fault_and_a_script_run_has_none(tmp_path, strip_ansi):
    from typer.testing import CliRunner

    from openfactory.cli import app

    runner = CliRunner()
    script = SORTER.parents[1] / "examples" / "pyplc" / "sorting-by-height.py"
    bad = runner.invoke(
        app, ["test", str(SORTER), "--script", str(script), "--fault", "comms=slow"]
    )
    message = strip_ansi(bad.output)
    assert bad.exit_code == 2 and "a comms fault is comms=drop" in message
    assert "comms=latency:ms=N (N > 0)" in message
    none = runner.invoke(
        app, ["test", str(SORTER), "--script", str(script), "--fault", "comms=drop"]
    )
    assert none.exit_code == 1 and "no protocol server" in none.output
