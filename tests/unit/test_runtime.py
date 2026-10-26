"""PLAN 1.9: Runtime snapshots and viewer events, without a network."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from openfactory.core.loader import build
from openfactory.core.schema import Scene
from openfactory.server.runtime import Runtime

EXAMPLE = Path(__file__).resolve().parents[2] / "scenes" / "sorting-by-height.yaml"


def operator_scene():
    return build(
        Scene.model_validate(
            {
                "openfactory": "0.1",
                "scene": {"name": "panel", "description": "Operator panel"},
                "components": [
                    {
                        "id": "PB_01",
                        "type": "pushbutton",
                        "description": "Start Pushbutton",
                        "position": [0, 1, 0],
                        "io": {"pressed": "PB_01_DI"},
                    },
                    {
                        "id": "SS_01",
                        "type": "selector_switch",
                        "description": "Mode Selector",
                        "position": [0.2, 1, 0],
                        "io": {"pos_a": "SS_01_A_DI", "pos_b": "SS_01_B_DI"},
                    },
                ],
            }
        )
    )


def test_snapshot_is_replaced_each_tick_not_mutated():
    runtime = Runtime(operator_scene(), realtime=False)
    first = runtime.snapshot
    runtime.scheduler.run(0.1)
    assert runtime.snapshot is not first and first["tick"] == 0
    assert runtime.snapshot["tick"] == 5 and runtime.snapshot["t"] == pytest.approx(0.1)


def test_press_and_select_events_reach_components():
    runtime = Runtime(operator_scene(), realtime=False)
    runtime.apply_event({"type": "press", "id": "PB_01"})
    runtime.apply_event({"type": "select", "id": "SS_01", "value": "b"})
    runtime.scheduler.run(0.02)
    io = runtime.snapshot["io"]
    assert io["PB_01_DI"] is True and io["SS_01_B_DI"] is True and io["SS_01_A_DI"] is False
    assert runtime.status()["events"] == {"press:PB_01": 1, "select:SS_01": 1}


@pytest.mark.parametrize(
    ("event", "error"),
    [
        ({"type": "press", "id": "SS_01"}, "not a pushbutton"),
        ({"type": "select", "id": "PB_01", "value": "a"}, "not a selector_switch"),
        ({"type": "select", "id": "SS_01", "value": "center"}, "not in"),
        ({"type": "warp", "id": "PB_01"}, "Input should be"),
        ({"type": "press"}, "Field required"),
    ],
)
def test_bad_events_are_rejected_and_not_counted(event, error):
    runtime = Runtime(operator_scene(), realtime=False)
    with pytest.raises((ValueError, ValidationError), match=error):
        runtime.apply_event(event)
    assert runtime.event_counts == {}


def test_background_thread_runs_and_stops():
    runtime = Runtime(operator_scene(), realtime=False)
    runtime.start(duration_s=1.0)
    runtime._thread.join(5)  # noqa: SLF001
    assert not runtime.running and runtime.stats.ticks == 50
    assert runtime.snapshot["tick"] == 50


def test_runtime_records_core_beam_edges_and_parity_view():
    """Housekeeping 5: raw photo-eye edges with ticks, next to browser sensor events."""
    from openfactory.core.iotable import Writer
    from openfactory.core.loader import load

    loaded = load(EXAMPLE)
    for tag in ("CONV_01_RUN_DO", "EMT_01_EN_DO"):
        loaded.io.set(tag, True, Writer.PLC)
    runtime = Runtime(loaded)
    for _ in range(320):
        runtime._step(0.02)
    core = runtime.parity()["core"]
    assert core[:2] == [
        {"id": "PE_01", "blocked": True, "tick": 273},  # the parity trace's first tall-box edges
        {"id": "PE_01", "blocked": False, "tick": 304},
    ]
    runtime.apply_event({"type": "sensor", "id": "PE_01", "value": True, "tick": 275})
    viewer = runtime.parity()["viewer"]
    assert viewer[0]["tick"] == 275 and viewer[0]["core_tick"] == 320
    assert runtime.loaded.io.get("PE_01_DI") is False  # parity-only: the event changed no tag


def test_snapshot_parts_carry_world_velocity():
    """PLAN 2.0a: the viewer starts a newly seen part moving as the core's part already is."""
    from openfactory.core.iotable import Writer
    from openfactory.core.loader import load

    loaded = load(EXAMPLE)
    for tag in ("CONV_01_RUN_DO", "EMT_01_EN_DO"):
        loaded.io.set(tag, True, Writer.PLC)
    runtime = Runtime(loaded)
    runtime._step(0.02)  # the emitter spawns at the end of this tick
    runtime._step(0.02)  # and the part falls during this one
    falling = runtime.snapshot["parts"][0]
    assert falling["carrier"] is None and falling["velocity"][1] < 0  # falling from the emitter
    for _ in range(100):
        runtime._step(0.02)
    carried = runtime.snapshot["parts"][0]
    assert carried["carrier"] == "CONV_01"
    assert carried["velocity"] == pytest.approx([0.5, 0.0, 0.0])  # belt speed along +x
