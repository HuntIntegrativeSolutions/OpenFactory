"""PLAN 2.9 first item (ruled 2026-10-30): state serialization is off the tick path. With no
recorder a tick builds no snapshot; the first reader after a tick builds it once and every later
reader gets the same object; every viewer gets the same serialized text; a recorded run still
builds one per tick; an exam switch rebuilds it at once."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from openfactory.core.loader import load
from openfactory.server.app import create_app
from openfactory.server.runtime import Runtime

SORTER = Path(__file__).resolve().parents[2] / "scenes" / "sorting-by-height.yaml"


def counting(runtime: Runtime) -> list[int]:
    built = [0]
    inner = runtime._take_snapshot

    def take():
        built[0] += 1
        return inner()

    runtime._take_snapshot = take  # type: ignore[method-assign]
    return built


def test_ticks_build_no_snapshot_and_a_read_builds_one():
    runtime = Runtime(load(SORTER), realtime=False)
    built = counting(runtime)
    for _ in range(50):
        runtime._step(0.02)
    assert built[0] == 0  # nothing on the tick path
    first = runtime.snapshot
    assert first["tick"] == 50 and built[0] == 1
    assert runtime.snapshot is first and built[0] == 1  # cached until the next tick
    runtime._step(0.02)
    assert runtime.snapshot["tick"] == 51 and built[0] == 2


def test_a_recorded_run_still_builds_every_tick():
    runtime = Runtime(load(SORTER), realtime=False)
    seen: list[int] = []

    class Recorder:
        def tick(self, snapshot):
            seen.append(snapshot["tick"])

    runtime.recorder = Recorder()
    for _ in range(5):
        runtime._step(0.02)
    assert seen == [1, 2, 3, 4, 5]


def test_every_viewer_gets_the_same_text_serialized_once(monkeypatch):
    import openfactory.server.app as app_module

    runtime = Runtime(load(SORTER), realtime=False)
    dumps = [0]
    real = app_module.json.dumps

    def counting_dumps(*args, **kwargs):
        dumps[0] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(app_module.json, "dumps", counting_dumps)
    client = TestClient(create_app(runtime, web_dist=None))
    with client.websocket_connect("/ws/state") as a, client.websocket_connect("/ws/state") as b:
        assert a.receive_text() == b.receive_text()
    assert dumps[0] == 1  # one snapshot, two viewers, one serialization


def test_an_exam_switch_rebuilds_the_snapshot_between_ticks():
    loaded = load(SORTER)
    loaded.enable_exam()
    runtime = Runtime(loaded, realtime=False)
    runtime._step(0.02)
    assert runtime.snapshot["io"]["PE_01_DI"] is False
    runtime.set_exam_switch("PE_01_DI", True)
    assert runtime.snapshot["io"]["PE_01_DI"] is True  # no tick needed
