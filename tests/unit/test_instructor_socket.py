"""PLAN 4.12: /ws/instructor, the instructor view's stream on the 4.3 broadcaster. The token is
the first message; a configured token is always required, with none the stream is open on
loopback only. An instructor frame lists every active fault even in exam mode, where the trainee
frame hides them. One WebSocket per TestClient session (it runs each on its own event loop)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from openfactory.core.loader import load
from openfactory.server.app import create_app
from openfactory.server.runtime import Runtime

SORTER = Path(__file__).resolve().parents[2] / "scenes" / "sorting-by-height.yaml"


def exam_runtime() -> Runtime:
    loaded = load(SORTER)
    loaded.enable_exam()
    runtime = Runtime(loaded, realtime=False)
    runtime._step(0.02)
    runtime.inject_fault("CYL_01", "jammed", {})
    return runtime


def refused(client: TestClient, first: object) -> str:
    with client.websocket_connect("/ws/instructor") as ws:
        ws.send_text(json.dumps(first))
        message = json.loads(ws.receive_text())
        assert message["refused"] is True
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_text()
        assert closed.value.code == 1008
        return message["error"]


def test_a_wrong_or_missing_token_is_refused_even_on_loopback():
    client = TestClient(create_app(exam_runtime(), web_dist=None, fault_token="s3cret"))
    assert "wrong fault token" in refused(client, {"token": "nope"})
    assert "wrong fault token" in refused(client, {"token": None})
    assert "wrong fault token" in refused(client, "not an object")
    assert client.get("/api/status").json()["instructors"] == 0


def test_with_no_token_it_is_open_on_loopback_and_refused_beyond_it():
    open_client = TestClient(create_app(exam_runtime(), web_dist=None))
    with open_client.websocket_connect("/ws/instructor") as ws:
        ws.send_text(json.dumps({"token": None}))
        assert json.loads(ws.receive_text())["instructor"] is True
    lan = TestClient(create_app(exam_runtime(), web_dist=None, bind="0.0.0.0"))
    assert "bound beyond loopback" in refused(lan, {"token": "anything"})


def test_the_instructor_frame_lists_faults_that_the_exam_mode_trainee_frame_hides():
    app = create_app(exam_runtime(), web_dist=None, fault_token="s3cret")
    client = TestClient(app)
    with client.websocket_connect("/ws/instructor") as ws:
        ws.send_text(json.dumps({"token": "s3cret"}))
        frame = json.loads(ws.receive_text())
        assert frame["instructor"] is True and frame["faults"] == {"CYL_01": "jammed"}
        assert "fault" not in frame["components"]["CYL_01"]  # the same snapshot underneath
        status = client.get("/api/status").json()
        assert (status["instructors"], status["viewers"]) == (1, 1)
    with client.websocket_connect("/ws/state") as ws:
        trainee = json.loads(ws.receive_text())
        assert "faults" not in trainee and "instructor" not in trainee
        assert "fault" not in trainee["components"]["CYL_01"]
    assert client.get("/api/status").json()["instructors"] == 0
