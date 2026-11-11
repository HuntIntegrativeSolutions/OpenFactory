"""PLAN 2.9 first item (ruled 2026-10-30): state serialization is off the tick path. With no
recorder a tick builds no snapshot; the first reader after a tick builds it once and every later
reader gets the same object; every viewer gets the same serialized text; a recorded run still
builds one per tick; an exam switch rebuilds it at once."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from openfactory.core.loader import load
from openfactory.server import app as app_module
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


def test_every_viewer_gets_the_same_text_serialized_once():
    """PLAN 4.3: one broadcaster serializes each frame once and sends that text to every viewer."""
    runtime = Runtime(load(SORTER), realtime=False)
    app = create_app(runtime, web_dist=None)
    client = TestClient(app)
    with client.websocket_connect("/ws/state") as a, client.websocket_connect("/ws/state") as b:
        # frames sent before the second viewer joined say "viewers": 1; read to the first that
        # both received, the broadcast both were in
        first_a = next(t for t in iter(a.receive_text, None) if '"viewers":2' in t)
        first_b = next(t for t in iter(b.receive_text, None) if '"viewers":2' in t)
        assert first_a == first_b
        serialized, sent = app.state.frames["serialized"], app.state.frames["sent"]
        assert a.receive_text() == b.receive_text()  # the next broadcast too: the same text
    assert serialized <= sent  # never more serializations than frames sent
    assert app.state.frames["serialized"] < app.state.frames["sent"]  # shared, not per viewer


def test_an_exam_switch_rebuilds_the_snapshot_between_ticks():
    loaded = load(SORTER)
    loaded.enable_exam()
    runtime = Runtime(loaded, realtime=False)
    runtime._step(0.02)
    assert runtime.snapshot["io"]["PE_01_DI"] is False
    runtime.set_exam_switch("PE_01_DI", True)
    assert runtime.snapshot["io"]["PE_01_DI"] is True  # no tick needed


def test_a_failed_broadcaster_restarts_while_viewers_stay(caplog, monkeypatch):
    """PLAN 4.3, review of 2026-11-10: if serializing a frame fails, the broadcaster logs the
    failure once and starts again after BROADCAST_RETRY_S while viewers are connected, so the
    viewer that was connected gets frames again without reconnecting."""
    monkeypatch.setattr(app_module, "BROADCAST_RETRY_S", 0.05)

    class Flaky(Runtime):
        failures = 2

        @property
        def snapshot(self):  # noqa: ANN201
            if Flaky.failures:
                Flaky.failures -= 1
                raise RuntimeError("a frame that will not serialize")
            return super().snapshot

    runtime = Flaky(load(SORTER), realtime=False)
    client = TestClient(create_app(runtime, web_dist=None))
    with client.websocket_connect("/ws/state") as viewer:
        assert '"tick"' in viewer.receive_text()  # the same viewer, after two failures
    assert caplog.text.count("state broadcaster failed") == 2  # once per failure
    with client.websocket_connect("/ws/state") as second:  # and a later viewer is served too
        assert '"tick"' in second.receive_text()
