"""PLAN 1.9: the web server against a real uvicorn on an ephemeral loopback port."""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import uvicorn
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from openfactory.core.config import Endpoint
from openfactory.core.iotable import Writer
from openfactory.core.loader import load
from openfactory.server.app import create_app, server_config
from openfactory.server.runtime import Runtime

EXAMPLE = Path(__file__).resolve().parents[2] / "scenes" / "sorting-by-height.yaml"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def served() -> Iterator[tuple[str, Runtime]]:
    """The example scene running in realtime, served by uvicorn on 127.0.0.1:<free port>."""
    runtime = Runtime(load(EXAMPLE))
    for tag in ("CONV_01_RUN_DO", "EMT_01_EN_DO"):
        runtime.loaded.io.set(tag, True, Writer.PLC)
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
        yield f"127.0.0.1:{endpoint.port}", runtime
    finally:
        server.should_exit = True
        thread.join(5)
        runtime.stop()


# --- Done when: curl /api/scene returns resolved geometry; a WS client receives >= 15 frames/s ----


def test_api_scene_returns_resolved_geometry(served):
    host, _ = served
    doc = httpx.get(f"http://{host}/api/scene").json()
    comps = {c["id"]: c for c in doc["components"]}
    assert doc["scene"]["name"] == "sorting-by-height" and doc["dt"] == 0.02
    assert doc["scene"]["friction"] == 0.8
    assert comps["CONV_01"] == {
        "id": "CONV_01",
        "type": "belt_conveyor",
        "position": [0.0, 0.0, 0.0],
        "rotation_deg": 0.0,
        "length": 4.0,
        "width": 0.5,
        "description": "Infeed Belt Conveyor — Sorting Line",
    }
    assert comps["PE_01"]["beam_length"] == 0.6 and comps["PE_01"]["position"] == [1.5, 0.3, 0.0]
    assert comps["CYL_01"]["stroke"] == 0.6 and comps["CYL_01"]["plate_width"] == 0.3
    assert comps["CYL_01"]["plate_height"] == 0.25  # unset in the scene: sized from the tall box
    assert comps["REM_01"]["size"] == [0.3, 1.0, 1.0]
    assert [p["name"] for p in comps["EMT_01"]["parts"]] == ["tall", "short"]
    assert {t["name"] for t in doc["tags"]} >= {"CONV_01_RUN_DO", "PE_01_DI"}
    assert doc["warnings"] == []


def test_state_websocket_streams_at_least_15_frames_per_second(served):
    host, _ = served
    frames = []
    with connect(f"ws://{host}/ws/state") as ws:
        start = time.monotonic()
        while time.monotonic() - start < 2.0:
            frames.append(json.loads(ws.recv(timeout=1)))
        elapsed = time.monotonic() - start
    rate = len(frames) / elapsed
    ticks = [f["tick"] for f in frames]
    print(f"{len(frames)} frames in {elapsed:.2f} s = {rate:.1f} frames/s", end="; ")
    print(f"sim ticks {ticks[0]}..{ticks[-1]}")
    assert rate >= 15
    assert ticks == sorted(ticks) and ticks[-1] > ticks[0]  # live simulation, in order
    last = frames[-1]
    assert set(last) == {"t", "tick", "components", "parts", "io", "viewers"}
    assert last["viewers"] == 1  # PLAN 4.3: the frame says how many viewers are connected
    assert last["components"]["CONV_01"]["running"] is True
    assert last["parts"], "the enabled emitter has spawned parts"
    part = last["parts"][0]
    assert set(part) == {
        "id", "name", "size", "position", "velocity", "carrier", "spawn_tick", "spawn_position",
        "follow", "color",
    }  # fmt: skip
    assert 0 < part["spawn_tick"] <= last["tick"] and len(part["spawn_position"]) == 3


def test_event_websocket_records_and_rejects(served):
    host, runtime = served
    with connect(f"ws://{host}/ws/events") as ws:
        ws.send(json.dumps({"t": 1.0, "type": "sensor", "id": "PE_01", "value": True}))
        ws.send(json.dumps({"type": "sensor", "id": "NOPE_01", "value": True}))
        assert "unknown component 'NOPE_01'" in json.loads(ws.recv(timeout=2))["error"]
        ws.send(json.dumps({"type": "teleport", "id": "PE_01"}))
        assert "error" in json.loads(ws.recv(timeout=2))
        ws.send("[1, 2]")
        assert "JSON object" in json.loads(ws.recv(timeout=2))["error"]
    status = httpx.get(f"http://{host}/api/status").json()
    assert status["events"] == {"sensor:PE_01": 1}
    events = httpx.get(f"http://{host}/api/events").json()
    assert events[-1]["id"] == "PE_01" and events[-1]["value"] is True
    assert events[-1]["core_tick"] > 0
    assert runtime.event_counts["sensor:PE_01"] == 1
    parity = httpx.get(f"http://{host}/api/parity").json()
    assert set(parity) == {"tick", "dt", "core", "viewer"}
    assert [e["id"] for e in parity["viewer"]] == ["PE_01"] and isinstance(parity["core"], list)


def test_root_without_a_viewer_build_explains_how_to_build(served):
    host, _ = served
    page = httpx.get(f"http://{host}/").text
    assert "npm run build" in page


def test_a_viewer_stuck_on_a_send_is_dropped_and_the_others_keep_their_frames(monkeypatch):
    """Review of 2026-11-10: a viewer whose frame send has been pending more than STALLED_SEND_S
    (5 s; 0.5 s here) is disconnected and counted as viewers_dropped in /api/status, and the
    other viewer keeps receiving frames. A real uvicorn, so every socket is on one event loop;
    the stuck viewer's server-side send is replaced by one that never completes."""
    import asyncio

    from openfactory.server import app as app_module

    assert app_module.STALLED_SEND_S == 5.0
    monkeypatch.setattr(app_module, "STALLED_SEND_S", 0.5)
    runtime = Runtime(load(EXAMPLE))
    app = create_app(runtime, web_dist=None)
    endpoint = Endpoint(bind="127.0.0.1", port=free_port())
    server = uvicorn.Server(server_config(app, endpoint))
    thread = threading.Thread(target=server.run, daemon=True)
    runtime.start()
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            assert time.monotonic() < deadline, "uvicorn did not start"
            time.sleep(0.02)
        host = f"127.0.0.1:{endpoint.port}"
        with connect(f"ws://{host}/ws/state") as stuck, connect(f"ws://{host}/ws/state") as ok:
            while json.loads(ok.recv(timeout=2))["viewers"] != 2:
                pass
            stuck_server_side = next(iter(app.state.viewers))  # connected first

            async def never(text: str) -> None:
                await asyncio.Event().wait()

            stuck_server_side.send_text = never
            deadline = time.monotonic() + 5
            while httpx.get(f"http://{host}/api/status").json()["viewers_dropped"] == 0:
                assert time.monotonic() < deadline, "the stuck viewer was never dropped"
                ok.recv(timeout=2)
            status = httpx.get(f"http://{host}/api/status").json()
            assert status["viewers_dropped"] == 1 and status["viewers"] == 1
            frames = [json.loads(ok.recv(timeout=2)) for _ in range(30)]
            assert frames[-1]["viewers"] == 1  # the other viewer still gets frames
            ticks = [f["tick"] for f in frames]
            assert ticks[-1] > ticks[0]
            with pytest.raises(ConnectionClosed):  # the server closed the stuck viewer's socket
                while True:
                    stuck.recv(timeout=2)
    finally:
        server.should_exit = True
        thread.join(5)
        runtime.stop()
