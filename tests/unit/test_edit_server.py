"""PLAN 4.1a: the edit server. Preview returns the lint's findings and the scene document; save
writes only the edited lines, refuses a stale base (the file changed on disk) with 409 and a
scene with lint errors with 422, and never touches the lockfiles."""

from __future__ import annotations

import difflib
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


@pytest.fixture
def editing(tmp_path):
    for f in (REPO / "scenes").glob("sorting-by-height*"):
        shutil.copy(f, tmp_path / f.name)  # the scene and its lockfiles
    scene = tmp_path / "sorting-by-height.yaml"
    runtime = Runtime(build(load_scene(scene), base_dir=tmp_path), realtime=False)
    client = TestClient(create_app(runtime, web_dist=None, edit=EditSession(scene)))
    return scene, client


def test_the_scene_is_served_in_edit_mode_with_its_source(editing):
    scene, client = editing
    doc = client.get("/api/scene").json()
    assert doc["mode"] == "edit" and doc["scene"]["name"] == "sorting-by-height"
    src = client.get("/api/edit/source").json()
    assert src["name"] == scene.name and src["text"] == scene.read_text(encoding="utf-8")
    assert len(src["base"]) == 64
    conv = next(c for c in src["data"]["components"] if c["id"] == "CONV_01")
    assert conv["speed_mps"] == 0.5 and conv["type"] == "belt_conveyor"


def test_preview_returns_the_lints_findings_and_the_scene_and_writes_nothing(editing):
    scene, client = editing
    before = scene.read_text(encoding="utf-8")
    base = client.get("/api/edit/source").json()["base"]
    far = {"op": "move", "id": "CONV_02", "position": [30.0, 0.75, 0.0]}  # off the floor: W204
    res = client.post("/api/edit/preview", json={"base": base, "ops": [far]}).json()
    assert res["changed"] and res["errors"] == 0
    assert any(f["code"] == "W204" and "CONV_02" in f["message"] for f in res["findings"])
    conv = next(c for c in res["scene"]["components"] if c["id"] == "CONV_02")
    assert conv["position"][0] == 30.0
    broken = {"op": "set", "path": ["components", "CONV_01", "speed_mps"], "value": -1.0}
    res = client.post("/api/edit/preview", json={"base": base, "ops": [broken]}).json()
    assert res["scene"] is None and [f["code"] for f in res["findings"]] == ["E100"]
    assert scene.read_text(encoding="utf-8") == before


def test_save_writes_only_the_edited_line_and_never_the_lockfiles(editing):
    scene, client = editing
    before = scene.read_text(encoding="utf-8")
    locks = {p: p.read_bytes() for p in scene.parent.glob("*.modbus.yaml")}
    base = client.get("/api/edit/source").json()["base"]
    op = {"op": "set", "path": ["components", "CONV_01", "speed_mps"], "value": 0.6}
    res = client.post("/api/edit/save", json={"base": base, "ops": [op]})
    assert res.status_code == 200, res.text
    after = scene.read_text(encoding="utf-8")
    diff = [d for d in difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="")]
    changed = [d for d in diff if d[:1] in "+-" and not d.startswith(("+++", "---"))]
    assert len(changed) == 2 and changed[1][1:].strip().startswith("speed_mps: 0.6")
    assert changed[0][1:].replace("0.5", "0.6") == changed[1][1:]  # the inline comment stays put
    assert {p: p.read_bytes() for p in locks} == locks
    assert res.json()["base"] != base
    assert client.get("/api/edit/source").json()["base"] == res.json()["base"]
    assert "speed_mps: 0.6" in client.get("/api/edit/source").json()["text"]
    assert client.get("/api/scene").json()["mode"] == "edit"  # rebuilt from the saved text


def test_a_save_against_a_file_changed_on_disk_is_refused(editing):
    scene, client = editing
    base = client.get("/api/edit/source").json()["base"]
    by_hand = scene.read_text(encoding="utf-8") + "# edited by hand\n"
    scene.write_text(by_hand, encoding="utf-8")
    op = {"op": "set", "path": ["components", "CONV_01", "speed_mps"], "value": 0.6}
    res = client.post("/api/edit/save", json={"base": base, "ops": [op]})
    assert res.status_code == 409 and "changed on disk" in res.json()["detail"]
    assert scene.read_text(encoding="utf-8") == by_hand  # the hand edit survives
    res = client.post("/api/edit/preview", json={"base": base, "ops": [op]})
    assert res.status_code == 409


def test_a_save_with_lint_errors_or_a_bad_op_writes_nothing(editing):
    scene, client = editing
    before = scene.read_text(encoding="utf-8")
    base = client.get("/api/edit/source").json()["base"]
    bad = {"op": "set", "path": ["components", "CONV_01", "speed_mps"], "value": -1.0}
    res = client.post("/api/edit/save", json={"base": base, "ops": [bad]})
    assert res.status_code == 422 and res.json()["detail"]["errors"] == 1
    res = client.post(
        "/api/edit/save", json={"base": base, "ops": [{"op": "delete", "id": "NOPE"}]}
    )
    assert res.status_code == 400 and "NOPE" in res.json()["detail"]
    assert scene.read_text(encoding="utf-8") == before


def test_the_run_api_has_no_edit_routes():
    runtime = Runtime(build(load_scene(REPO / "scenes" / "sorting-by-height.yaml")), realtime=False)
    client = TestClient(create_app(runtime, web_dist=None))
    assert client.get("/api/edit/source").status_code == 404
    assert "mode" not in client.get("/api/scene").json()
