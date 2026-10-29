"""PLAN 2.11 exam mode: `exam: switch` DI bindings follow an operator switch instead of the
simulation, /api/exam lists and turns them (guarded like /api/fault), and state frames hide
fault markers while exam mode is on."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import ValidationError

from openfactory.core.loader import build, load
from openfactory.core.schema import Scene
from openfactory.server.app import create_app
from openfactory.server.runtime import Runtime

SORTER = Path(__file__).resolve().parents[2] / "scenes" / "sorting-by-height.yaml"


def sorter_with(component_id: str, port: str, binding: dict) -> dict:
    doc = yaml.safe_load(SORTER.read_text(encoding="utf-8"))
    next(c for c in doc["components"] if c["id"] == component_id)["io"][port] = binding
    return doc


def test_only_a_di_binding_takes_exam_switch():
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        Scene.model_validate(
            sorter_with("CYL_01", "extend", {"tag": "CYL_01_EXT_DO", "exam": "switch"})
        )
    with pytest.raises(ValidationError, match="'switch'"):
        Scene.model_validate(sorter_with("PE_01", "detect", {"tag": "PE_01_DI", "exam": "lever"}))
    scene = Scene.model_validate(
        sorter_with("PE_01", "detect", {"tag": "PE_01_DI", "exam": "switch"})
    )
    assert [b.name for b in scene.tag_bindings() if b.exam] == ["PE_01_DI"]


def test_the_switch_not_the_sensor_drives_the_tag():
    loaded = load(SORTER)
    assert loaded.exam is None  # off unless asked for
    assert loaded.enable_exam() == {"PE_01_DI": False}
    loaded.world.spawn("tall", (0.3, 0.4, 0.3), (1.5, 0.2, 0.0))  # right in the beam
    for _ in range(3):
        loaded.step(0.02)
    assert loaded.component("PE_01").blocked and loaded.io.get("PE_01_DI") is False
    loaded.set_exam_switch("PE_01_DI", True)
    assert loaded.io.get("PE_01_DI") is True  # at once, between ticks
    loaded.world.parts.clear()
    loaded.step(0.02)
    assert not loaded.component("PE_01").blocked and loaded.io.get("PE_01_DI") is True
    with pytest.raises(KeyError, match="CYL_01_EXT_DI"):
        loaded.set_exam_switch("CYL_01_EXT_DI", True)


def runtime(exam: bool) -> Runtime:
    loaded = build(Scene.model_validate(yaml.safe_load(SORTER.read_text(encoding="utf-8"))))
    if exam:
        loaded.enable_exam()
    return Runtime(loaded, realtime=False)


def test_api_exam_lists_and_turns_switches_and_frames_hide_faults():
    rt = runtime(exam=True)
    http = TestClient(create_app(rt, web_dist=None))
    listed = http.get("/api/exam").json()
    assert listed == {
        "enabled": True,
        "switches": [
            {
                "tag": "PE_01_DI",
                "component": "PE_01",
                "port": "detect",
                "description": "Part detected",
                "on": False,
            }
        ],
    }
    assert http.post("/api/exam", json={"tag": "PE_01_DI", "on": True}).json() == {
        "tag": "PE_01_DI",
        "on": True,
    }
    assert http.get("/api/state").json()["io"]["PE_01_DI"] is True
    assert http.post("/api/exam", json={"tag": "NOPE_DI", "on": True}).status_code == 404
    http.post("/api/fault", json={"id": "PE_01", "kind": "misaligned", "params": {"offset_m": 0.1}})
    rt._step(0.02)
    eye = http.get("/api/state").json()["components"]["PE_01"]
    assert "fault" not in eye and eye["beam_offset_m"] == 0.0  # hidden from trainees
    assert http.get("/api/fault").json()["active"][0]["kind"] == "misaligned"  # instructor sees it


def test_without_exam_mode_the_api_says_so_and_frames_keep_fault_markers():
    rt = runtime(exam=False)
    http = TestClient(create_app(rt, web_dist=None))
    assert http.get("/api/exam").json() == {"enabled": False, "switches": []}
    assert http.post("/api/exam", json={"tag": "PE_01_DI", "on": True}).status_code == 404
    http.post("/api/fault", json={"id": "PE_01", "kind": "stuck_on"})
    rt._step(0.02)
    assert http.get("/api/state").json()["components"]["PE_01"]["fault"] == "stuck_on"


def test_post_api_exam_is_guarded_beyond_loopback():
    rt = runtime(exam=True)
    off = TestClient(create_app(rt, web_dist=None, bind="0.0.0.0"))
    assert off.post("/api/exam", json={"tag": "PE_01_DI", "on": True}).status_code == 403
    assert off.get("/api/exam").status_code == 200  # the switch list is not a secret
    guarded = TestClient(create_app(rt, web_dist=None, bind="0.0.0.0", fault_token="s3cret"))
    body = {"tag": "PE_01_DI", "on": True}
    assert guarded.post("/api/exam", json=body).status_code == 401
    ok = guarded.post("/api/exam", json=body, headers={"X-OpenFactory-Fault-Token": "s3cret"})
    assert ok.status_code == 200
