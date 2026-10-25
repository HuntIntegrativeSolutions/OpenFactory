"""PLAN 1.6: realtime drift under 1 % on the real wall clock (takes about 2 s)."""

from __future__ import annotations

from pathlib import Path

from openfactory.core.iotable import Writer
from openfactory.core.loader import load
from openfactory.core.scheduler import Scheduler

EXAMPLE = Path(__file__).resolve().parents[2] / "scenes" / "sorting-by-height.yaml"


def test_realtime_drift_under_one_percent():
    loaded = load(EXAMPLE)
    loaded.io.set_many(
        {"CONV_01_RUN_DO": True, "CONV_01_SPD_AO": 100.0, "EMT_01_EN_DO": True}, Writer.PLC
    )
    stats = Scheduler(loaded.step).run(duration_s=2.0)
    print(
        f"realtime: {stats.ticks} ticks, sim {stats.sim_time:.3f} s, wall {stats.wall_time:.4f} s, "
        f"drift {stats.drift * 100:+.3f} %, max lateness {stats.max_lateness * 1000:.2f} ms, "
        f"dropped {stats.dropped}"
    )
    assert stats.ticks == 100
    assert abs(stats.drift) < 0.01
    assert stats.dropped == 0
