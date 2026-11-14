"""Ruled 2026-11-13: a server bound to loopback answers only a loopback ``Host`` (DNS rebinding),
and ``/ws/events`` refuses a websocket whose ``Origin`` is not the server's own."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from openfactory.core.loader import build
from openfactory.core.schema import load_scene
from openfactory.server.app import create_app, host_refusal, ws_origin_refusal
from openfactory.server.runtime import Runtime

REPO = Path(__file__).resolve().parents[2]


def _runtime() -> Runtime:
    scene = REPO / "scenes" / "sorting-by-height.yaml"
    return Runtime(build(load_scene(scene), base_dir=scene.parent), realtime=False)


@pytest.mark.parametrize(
    "host", ["127.0.0.1:8080", "localhost", "LOCALHOST:8000", "[::1]:8080", "127.1.2.3", None]
)
def test_loopback_hosts_pass(host):
    assert host_refusal(host) is None


@pytest.mark.parametrize(
    "host", ["evil.example:8080", "example.invalid", "10.0.0.5:8000", "[2001:db8::1]:80", "0.0.0.0"]
)
def test_other_hosts_are_refused(host):
    assert host_refusal(host) is not None


def test_a_loopback_server_refuses_a_foreign_host():
    client = TestClient(create_app(_runtime(), web_dist=None), base_url="http://127.0.0.1")
    assert client.get("/api/scene").status_code == 200
    refused = client.get("http://rebound.example:8000/api/scene")
    assert refused.status_code == 421
    assert "not a loopback name" in refused.json()["detail"]
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect("ws://rebound.example/ws/state") as ws,
    ):
        ws.receive_text()


def test_a_server_beyond_loopback_does_not_check_host():
    client = TestClient(create_app(_runtime(), web_dist=None, bind="0.0.0.0"))
    assert client.get("http://plant-pc.lan:8000/api/scene").status_code == 200


def test_ws_origin_rule():
    assert ws_origin_refusal("ws", "127.0.0.1:8000", None) is None
    assert ws_origin_refusal("ws", "127.0.0.1:8000", "http://127.0.0.1:8000") is None
    assert ws_origin_refusal("wss", "127.0.0.1:8000", "https://127.0.0.1:8000") is None
    assert ws_origin_refusal("ws", "127.0.0.1:8000", "https://127.0.0.1:8000") is not None
    assert ws_origin_refusal("ws", "127.0.0.1:8000", "https://example.invalid") is not None


def test_events_socket_refuses_a_foreign_origin():
    runtime = _runtime()
    client = TestClient(create_app(runtime, web_dist=None), base_url="http://127.0.0.1")
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect(
            "ws://127.0.0.1/ws/events", headers={"origin": "https://example.invalid"}
        ) as ws,
    ):  # a loopback Host, so only the Origin refuses it
        ws.send_text('{"type": "button", "id": "X", "pressed": true}')
        ws.receive_text()
    own = {"origin": "http://127.0.0.1"}  # TestClient sends Host 127.0.0.1
    with client.websocket_connect("ws://127.0.0.1/ws/events", headers=own) as ws:
        ws.send_text("[]")
        assert "error" in ws.receive_text()  # accepted: the socket answers
