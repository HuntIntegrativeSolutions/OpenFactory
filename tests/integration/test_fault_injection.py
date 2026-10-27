"""PLAN 2.6 Done-when: the /api/fault round trip against a real server, a fault a PLC sees over
Modbus, and the sorter's result under its script with a jammed pusher or a stuck photo eye."""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import uvicorn
from pymodbus.client import AsyncModbusTcpClient

from openfactory.core.config import Endpoint
from openfactory.core.loader import load
from openfactory.protocols.modbus import ModbusServer, allocate
from openfactory.pyplc import ScriptPlc
from openfactory.server.app import create_app, server_config
from openfactory.server.runtime import Runtime

REPO = Path(__file__).resolve().parents[2]
SORTER = REPO / "scenes" / "sorting-by-height.yaml"
SCRIPT = REPO / "examples" / "pyplc" / "sorting-by-height.py"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def served() -> Iterator[tuple[str, Runtime]]:
    runtime = Runtime(load(SORTER))
    endpoint = Endpoint(bind="127.0.0.1", port=free_port())
    server = uvicorn.Server(server_config(create_app(runtime, web_dist=None), endpoint))
    thread = threading.Thread(target=server.run, daemon=True)
    runtime.start()
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "uvicorn did not start"
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{endpoint.port}", runtime
    finally:
        server.should_exit = True
        runtime.stop()
        thread.join(5)


def test_fault_api_round_trip(served):
    base, runtime = served
    with httpx.Client(base_url=base) as http:
        listed = http.get("/api/fault").json()
        assert listed["active"] == []
        assert listed["supported"]["CYL_01"] == ["stuck_on", "stuck_off", "jammed", "slow"]
        assert listed["supported"]["PE_01"] == ["stuck_on", "stuck_off", "misaligned", "noisy"]
        assert listed["supported"]["REM_01"] == []
        first = http.post("/api/fault", json={"id": "CYL_01", "kind": "jammed"})
        assert first.status_code == 200 and first.json()["replaced"] is None
        second = http.post(
            "/api/fault", json={"id": "CYL_01", "kind": "slow", "params": {"factor": 0.25}}
        )
        body = second.json()
        print("POST /api/fault (replacing):", body)
        assert body["fault"]["kind"] == "slow" and body["replaced"]["kind"] == "jammed"
        bad = http.post("/api/fault", json={"id": "PE_01", "kind": "jammed"})
        unknown = http.post("/api/fault", json={"id": "NOPE_01", "kind": "jammed"})
        print(f"400: {bad.json()['detail']}\n404: {unknown.json()['detail']}")
        assert bad.status_code == 400 and "supports: stuck_on" in bad.json()["detail"]
        assert unknown.status_code == 404
        time.sleep(0.2)
        state = http.get("/api/state").json()
        assert state["components"]["CYL_01"]["fault"] == "slow"
        active = http.get("/api/fault").json()["active"]
        assert [(f["id"], f["kind"], f["params"]) for f in active] == [
            ("CYL_01", "slow", {"factor": 0.25})
        ]
        cleared = http.delete("/api/fault/CYL_01").json()
        assert cleared["cleared"]["kind"] == "slow"
        assert http.get("/api/fault").json()["active"] == []
        assert http.delete("/api/fault/NOPE_01").status_code == 404
        log = [e for e in http.get("/api/events").json() if e["type"] in ("fault", "clear")]
        print("event log:", [(e["type"], e["id"], e.get("kind"), e["core_tick"]) for e in log])
        assert [e["type"] for e in log] == ["fault", "fault", "clear"]
        assert all(isinstance(e["core_tick"], int) for e in log)


def test_a_stuck_on_photo_eye_reads_true_over_modbus_with_no_part():
    loaded = load(SORTER)
    runtime = Runtime(loaded, realtime=False)
    runtime.inject_fault("PE_01", "stuck_on")
    seen: list[bool] = []

    async def main() -> None:
        server = ModbusServer(
            loaded.io, "float32", Endpoint(port=free_port()), allocate(loaded.io.tags(), "float32")
        )
        task = asyncio.create_task(server.serve_forever())
        try:
            await server.wait_ready()
            client = AsyncModbusTcpClient("127.0.0.1", port=server.endpoint.port)
            assert await client.connect()
            address = server.map.by_tag["PE_01_DI"].address
            for _ in range(5):
                loaded.step(0.02)
            seen.append((await client.read_discrete_inputs(address, count=1)).bits[0])
            runtime.clear_fault("PE_01")
            for _ in range(5):
                loaded.step(0.02)
            seen.append((await client.read_discrete_inputs(address, count=1)).bits[0])
            client.close()
        finally:
            await server.shutdown()
            task.cancel()

    asyncio.run(main())
    print(f"PE_01_DI over Modbus, no part: stuck_on {seen[0]}, cleared {seen[1]}")
    assert seen == [True, False] and loaded.world.parts == {}


def sorter_under_script(component: str | None, kind: str | None) -> dict:
    loaded = load(SORTER)
    plc = ScriptPlc(SCRIPT, loaded.io)
    if component:
        loaded.component(component).fault_inject(kind)
    extended = extend_cmd = 0
    t = 0.0
    for _ in range(3000):  # 60 s
        plc.scan(t)
        loaded.step(0.02)
        t += 0.02
        extended += bool(loaded.io.get("CYL_01_EXT_DI"))
        extend_cmd += bool(loaded.io.get("CYL_01_EXT_DO"))
    removers = {
        c.id: dict(Counter(c.removed)) for c in loaded.components if c.type == "part_remover"
    }
    return {"removers": removers, "ext_di_ticks": extended, "ext_do_ticks": extend_cmd}


def test_the_sorter_under_its_script_shows_each_fault_to_the_plc():
    healthy = sorter_under_script(None, None)
    jammed = sorter_under_script("CYL_01", "jammed")
    blind = sorter_under_script("PE_01", "stuck_off")
    for name, result in (
        ("healthy", healthy),
        ("CYL_01 jammed", jammed),
        ("PE_01 stuck_off", blind),
    ):
        print(f"{name}: {result}")
    assert healthy["removers"] == {"REM_01": {"short": 9}, "REM_02": {"tall": 9}}
    # jammed: the script keeps commanding extend, the extended limit never makes, tall boxes pass
    assert jammed["ext_di_ticks"] == 0 and jammed["ext_do_ticks"] > 0
    assert jammed["removers"] == {"REM_01": {"short": 9, "tall": 9}, "REM_02": {}}
    # a blind eye: no divert is ever commanded
    assert blind["ext_do_ticks"] == 0
    assert blind["removers"] == {"REM_01": {"short": 9, "tall": 9}, "REM_02": {}}
