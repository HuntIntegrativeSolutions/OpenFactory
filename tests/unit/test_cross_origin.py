"""PLAN 4.1c fix 6 (ruled 2026-11-12): a page from another site cannot change a scene, a fault or
an exam switch through a browser. No response carries CORS headers, so it cannot read an answer;
a changing request with a foreign ``Origin`` is refused with 403; and a ``POST`` that is not
``application/json`` (what a cross-site form or a ``text/plain`` fetch sends without a
preflight) is refused with 415 before any handler runs. The file on disk is unchanged throughout."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openfactory.core.loader import build
from openfactory.core.schema import load_scene
from openfactory.server.app import create_app
from openfactory.server.editor import EditSession
from openfactory.server.runtime import Runtime

REPO = Path(__file__).resolve().parents[2]
OWN = "http://127.0.0.1"  # the tests' TestClient origin: scheme and Host
EVIL = "https://example.invalid"
EDIT_POSTS = (
    "/api/edit/preview",
    "/api/edit/save",
    "/api/edit/new",
    "/api/edit/palette",
    "/api/edit/references",
)


@pytest.fixture
def editing(tmp_path):
    for f in (REPO / "scenes").glob("sorting-by-height*"):
        shutil.copy(f, tmp_path / f.name)
    scene = tmp_path / "sorting-by-height.yaml"
    runtime = Runtime(build(load_scene(scene), base_dir=tmp_path), realtime=False)
    client = TestClient(
        create_app(runtime, web_dist=None, edit=EditSession(scene)), base_url="http://127.0.0.1"
    )
    return scene, client


def _body(client: TestClient, path: str) -> dict:
    base = client.get("/api/edit/source").json()["base"]
    op = {"op": "set", "path": ["components", "CONV_01", "speed_mps"], "value": 0.7}
    body: dict = {"base": base, "ops": [op]}
    if path == "/api/edit/new":
        body |= {"type": "photo_eye", "position": [1.0, 0.0, 0.0]}
    if path == "/api/edit/references":
        body |= {"id": "REM_02"}
    return body


def test_no_response_carries_cors_headers(editing):
    _, client = editing
    asks = {"Origin": EVIL, "Access-Control-Request-Method": "POST"}
    for res in (
        client.get("/api/scene", headers={"Origin": EVIL}),
        client.get("/api/edit/source", headers={"Origin": EVIL}),
        client.options("/api/edit/save", headers=asks),
        client.post(
            "/api/edit/save", json=_body(client, "/api/edit/save"), headers={"Origin": EVIL}
        ),
    ):
        assert not [h for h in res.headers if h.lower().startswith("access-control-")], res.headers


@pytest.mark.parametrize("path", EDIT_POSTS)
def test_an_edit_post_from_another_origin_is_refused_with_403(editing, path):
    scene, client = editing
    before = scene.read_text(encoding="utf-8")
    res = client.post(path, json=_body(client, path), headers={"Origin": EVIL})
    assert res.status_code == 403 and EVIL in res.json()["detail"]
    for origin in ("null", "http://127.0.0.1:9999", "https://127.0.0.1"):  # port, scheme
        assert (
            client.post(path, json=_body(client, path), headers={"Origin": origin}).status_code
            == 403
        )
    assert scene.read_text(encoding="utf-8") == before


@pytest.mark.parametrize("path", EDIT_POSTS)
@pytest.mark.parametrize(
    "content_type",
    ["text/plain", "application/x-www-form-urlencoded", "multipart/form-data; boundary=x"],
)
def test_an_edit_post_that_is_not_json_is_refused_with_415(editing, path, content_type):
    import json

    scene, client = editing
    before = scene.read_text(encoding="utf-8")
    text = json.dumps(_body(client, path))
    for headers in ({"Content-Type": content_type}, {"Content-Type": content_type, "Origin": OWN}):
        res = client.post(path, content=text, headers=headers)
        assert res.status_code == 415, (content_type, res.status_code, res.text)
    assert scene.read_text(encoding="utf-8") == before


@pytest.mark.parametrize("path", EDIT_POSTS)
def test_the_editors_own_page_and_tools_without_an_origin_still_work(editing, path):
    _, client = editing
    for headers in ({"Origin": OWN}, {}):
        res = client.post(path, json=_body(client, path), headers=headers)
        assert res.status_code == 200, (path, headers, res.text)


def test_a_save_from_the_own_origin_writes_the_file(editing):
    scene, client = editing
    res = client.post(
        "/api/edit/save", json=_body(client, "/api/edit/save"), headers={"Origin": OWN}
    )
    assert res.status_code == 200 and "speed_mps: 0.7" in scene.read_text(encoding="utf-8")


def _running() -> TestClient:
    scene = REPO / "scenes" / "sorting-by-height.yaml"
    loaded = build(load_scene(scene), base_dir=scene.parent)
    loaded.enable_exam()  # so POST /api/exam has a switch to turn
    return TestClient(
        create_app(Runtime(loaded, realtime=False), web_dist=None), base_url="http://127.0.0.1"
    )


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/api/fault", {"id": "CONV_01", "kind": "stuck_off", "params": {}}),
        ("DELETE", "/api/fault/CONV_01", None),
        ("POST", "/api/exam", {"tag": "PE_01_DI", "on": True}),
    ],
)
def test_faults_and_the_exam_switch_refuse_another_origin_and_non_json(method, path, body):
    client = _running()
    res = client.request(method, path, json=body, headers={"Origin": EVIL})
    assert res.status_code == 403, res.text
    assert client.get("/api/fault").json()["active"] == []
    if method == "POST":
        res = client.request(
            method, path, content=str(body), headers={"Content-Type": "text/plain"}
        )
        assert res.status_code == 415, res.text
        # the same request from the viewer's own page reaches the handler
        ok = client.request(method, path, json=body, headers={"Origin": OWN})
        assert ok.status_code == 200, ok.text
