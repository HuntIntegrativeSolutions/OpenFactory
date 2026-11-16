"""PLAN 5.14a: the tag change log of a live run, ``core/iolog.py`` and ``GET /api/iolog``."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openfactory.core.iolog import MAX_CHANGES, WINDOW_S, IoLog, changed
from openfactory.core.loader import load
from openfactory.pyplc import ScriptPlc
from openfactory.record import ReplayRuntime, read_recording
from openfactory.server.app import create_app
from openfactory.server.runtime import Runtime

REPO = Path(__file__).resolve().parents[2]
KIT = REPO / "kits" / "sorting-by-height"
DT = 0.02


def sorter_runtime() -> Runtime:
    """The sorter under its stand-in script, as the kit recording was made (the kit's scene and
    script are copies of these; loading the kit's own script would leave a ``__pycache__`` in the
    kit, which ``build_kit.py --check`` refuses)."""
    loaded = load(REPO / "scenes" / "sorting-by-height.yaml")
    return Runtime(
        loaded,
        realtime=False,
        controller=ScriptPlc(REPO / "examples" / "pyplc" / "sorting-by-height.py", loaded.io),
    )


def run(runtime: Runtime, ticks: int) -> None:
    for _ in range(ticks):
        runtime._step(runtime.scheduler.dt)


@pytest.fixture(scope="module")
def recording():  # noqa: ANN201
    return read_recording(KIT / "sorting-by-height.ofrec")


@pytest.fixture(scope="module")
def sorter_30s() -> Runtime:
    runtime = sorter_runtime()
    run(runtime, 1500)
    return runtime


def recorded(rec, until_tick: int) -> list[tuple[int, str, object]]:  # noqa: ANN001
    return [(k, tag, v) for k, items in rec.io if k <= until_tick for tag, v in items.items()]


def test_changed_is_the_recorders_diff() -> None:
    last: dict[str, object] = {}
    assert changed(last, {"A_DI": False, "B_AI": 1.5}) == {"A_DI": False, "B_AI": 1.5}
    assert changed(last, {"A_DI": False, "B_AI": 1.5}) == {}
    assert changed(last, {"A_DI": True, "B_AI": 1.5}) == {"A_DI": True}
    assert last == {"A_DI": True, "B_AI": 1.5}


def test_the_sorters_30_s_log_is_its_recordings_changes(sorter_30s: Runtime, recording) -> None:  # noqa: ANN001
    """Done-when: PE_01_DI's and CYL_01_EXT_DO's rising edges at the ticks the recording gives;
    in fact every change of the first 30 s, tick for tick."""
    log = sorter_30s.iolog_query()
    live = [(c["tick"], c["tag"], c["value"]) for c in log["changes"]]
    assert live == recorded(recording, 1500)
    assert log["tick"] == 1500 and log["complete"] and log["first_tick"] == 0

    def rising(tag: str, changes: list[tuple[int, str, object]]) -> list[int]:
        return [k for k, t, v in changes if t == tag and v is True and k > 0]

    for tag in ("PE_01_DI", "CYL_01_EXT_DO"):
        assert rising(tag, live), f"{tag} never rose in 30 s"
        assert rising(tag, live) == rising(tag, recorded(recording, 1500))
    writers = {c["tag"]: c["writer"] for c in log["changes"]}
    assert writers["PE_01_DI"] == "simulation" and writers["CYL_01_EXT_DO"] == "plc"


def test_query_after_a_tick_and_for_some_tags(sorter_30s: Runtime) -> None:
    every = sorter_30s.iolog_query()["changes"]
    some = sorter_30s.iolog_query(since_tick=500, tags=["PE_01_DI", "CYL_01_EXT_DO"])["changes"]
    assert some == [
        c for c in every if c["tick"] > 500 and c["tag"] in ("PE_01_DI", "CYL_01_EXT_DO")
    ]
    assert all(c["t"] == round(c["tick"] * DT, 6) for c in some)
    with pytest.raises(KeyError, match="NOT_A_TAG_DI"):
        sorter_30s.iolog_query(tags=["NOT_A_TAG_DI"])


def test_the_log_never_grows_past_its_cap_in_a_600_s_run() -> None:
    """Done-when: a 600 s run of the sorter keeps within the cap at every tick."""
    runtime = sorter_runtime()
    log = runtime.iolog
    assert log is not None
    window = round(WINDOW_S / DT)
    most = 0
    for _ in range(round(600 / DT)):
        runtime._step(DT)
        assert len(log) <= MAX_CHANGES
        most = max(most, len(log))
        oldest = log.first_tick
        assert oldest is None or oldest > log.tick - window
    assert log.tick == 30000 and most > 0


def test_the_cap_by_count_and_by_time() -> None:
    kinds = {"A_AI": "AI", "B_DO": "DO"}
    by_count = IoLog(kinds, DT, max_changes=100)
    for tick in range(300):
        by_count.add(tick, {"A_AI": float(tick)})
    assert len(by_count) == 100
    q = by_count.query()
    assert q["first_tick"] == 200
    assert not q["complete"] and by_count.dropped_to == 199
    assert by_count.query(since_tick=199)["complete"]

    by_time = IoLog(kinds, DT, window_s=10.0)  # 500 ticks
    for tick in range(0, 2000, 50):
        by_time.add(tick, {"A_AI": float(tick), "B_DO": tick % 100 == 0})
    q = by_time.query()
    assert q["first_tick"] == 1500 and len(q["changes"]) == 2 * 10
    assert by_time.dropped_to == 1450

    one_tick = IoLog(kinds, DT, max_changes=1)
    one_tick.add(7, {"A_AI": 1.0, "B_DO": True})
    assert len(one_tick) == 1 and one_tick.dropped_to == 7


def test_get_api_iolog_live(sorter_30s: Runtime) -> None:
    client = TestClient(create_app(sorter_30s, web_dist=None), base_url="http://127.0.0.1")
    body = client.get("/api/iolog", params={"since_tick": 1000, "tags": "PE_01_DI"}).json()
    assert body["changes"] and all(c["tag"] == "PE_01_DI" for c in body["changes"])
    assert all(c["tick"] > 1000 for c in body["changes"])
    assert client.get("/api/iolog", params={"tags": "NOPE_DI"}).status_code == 404
    assert client.post("/api/iolog", json={}).status_code == 405  # read-only


def test_get_api_iolog_in_a_replay_stops_at_the_tick_shown(recording) -> None:  # noqa: ANN001
    replay = ReplayRuntime(recording)
    replay.snapshot = next(frame for tick, frame in recording.frames if tick == 300)
    body = (
        TestClient(create_app(replay, web_dist=None), base_url="http://127.0.0.1")
        .get("/api/iolog")
        .json()
    )
    assert body["tick"] == 300 and body["complete"]
    got = [(c["tick"], c["tag"], c["value"]) for c in body["changes"]]
    assert got == recorded(recording, 300)
