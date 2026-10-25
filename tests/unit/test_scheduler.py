"""PLAN 1.6: fixed-step scheduler timing (fake clock), fast mode, and determinism."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from openfactory.core.iotable import Writer
from openfactory.core.loader import load
from openfactory.core.scheduler import Scheduler

EXAMPLE = Path(__file__).resolve().parents[2] / "scenes" / "sorting-by-height.yaml"


class FakeClock:
    """Wall clock that only moves when the scheduler sleeps or a step 'takes time'."""

    def __init__(self, sleep_overshoot: float = 0.0) -> None:
        self.t = 100.0
        self.sleeps = 0
        self.overshoot = sleep_overshoot

    def clock(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps += 1
        self.t += max(seconds, 0.0) + self.overshoot


def running_example():
    loaded = load(EXAMPLE)
    loaded.io.set_many(
        {"CONV_01_RUN_DO": True, "CONV_01_SPD_AO": 100.0, "EMT_01_EN_DO": True}, Writer.PLC
    )
    return loaded


# --- realtime timing with a fake clock ---------------------------------------------------------


def test_realtime_steps_on_schedule_without_cumulative_drift():
    fake = FakeClock(sleep_overshoot=0.003)  # every sleep oversleeps by 3 ms
    calls = []
    sched = Scheduler(calls.append, clock=fake.clock, sleep=fake.sleep)
    stats = sched.run(duration_s=10.0)
    assert stats.ticks == 500 and len(calls) == 500
    assert all(dt == pytest.approx(0.02) for dt in calls)
    # absolute deadlines: 500 overshoots of 3 ms do not add up to 1.5 s of drift
    assert stats.wall_time - stats.sim_time < 0.01
    assert stats.dropped == 0


def test_realtime_catches_up_after_a_slow_step():
    fake = FakeClock()

    def step(_dt):
        if fake.sleeps == 10:
            fake.t += 0.1  # one step takes 100 ms = 5 ticks
            fake.sleeps += 1

    stats = Scheduler(step, clock=fake.clock, sleep=fake.sleep).run(duration_s=1.0)
    assert stats.ticks == 50 and stats.dropped == 0
    assert stats.max_lateness == pytest.approx(0.1, abs=0.021)
    assert abs(stats.drift) < 0.01


def test_realtime_drops_ticks_beyond_catch_up_cap():
    fake = FakeClock()

    def step(_dt):
        if fake.sleeps == 5:
            fake.t += 1.0  # host stalled for 1 s = 50 ticks
            fake.sleeps += 1

    stats = Scheduler(step, max_catch_up=10, clock=fake.clock, sleep=fake.sleep).run(2.0)
    assert stats.ticks == 100
    assert stats.dropped == 50 - 10


def test_fast_mode_never_sleeps():
    fake = FakeClock()
    stats = Scheduler(lambda dt: None, realtime=False, clock=fake.clock, sleep=fake.sleep).run(10)
    assert stats.ticks == 500 and fake.sleeps == 0


def test_stop_from_step_ends_run_after_that_tick():
    sched: Scheduler | None = None
    count = 0

    def step(_dt):
        nonlocal count
        count += 1
        if count == 7:
            sched.stop()

    sched = Scheduler(step, realtime=False)
    assert sched.run().ticks == 7


def test_rate_and_validation():
    assert Scheduler(lambda dt: None, rate_hz=100).dt == pytest.approx(0.01)
    with pytest.raises(ValueError):
        Scheduler(lambda dt: None, rate_hz=0)
    with pytest.raises(ValueError):
        Scheduler(lambda dt: None, max_catch_up=0)


# --- Done when: 10 simulated seconds in < 0.5 s wall time in fast mode -------------------------


def test_fast_mode_runs_10_simulated_seconds_under_half_a_second():
    loaded = running_example()
    sched = Scheduler(loaded.step, realtime=False)
    started = time.perf_counter()
    stats = sched.run(duration_s=10.0)
    wall = time.perf_counter() - started
    print(
        f"fast mode: {stats.ticks} ticks, {stats.sim_time:.2f} s simulated in {wall * 1000:.1f} ms "
        f"wall; {len(loaded.world.parts)} parts in scene, {loaded.world.lost.total()} lost"
    )
    assert stats.ticks == 500 and loaded.tick == 500
    assert loaded.time == pytest.approx(10.0)
    assert wall < 0.5
    assert loaded.world.spawned == 4  # emitter at t=0, 3, 6, 9 s: the scene did real work


def test_fast_mode_matches_manual_stepping():
    a, b = running_example(), running_example()
    Scheduler(a.step, realtime=False).run(duration_s=10.0)
    for _ in range(500):
        b.step(0.02)
    assert a.io.values() == b.io.values()
    assert {p.id: p.position for p in a.world.parts.values()} == {
        p.id: p.position for p in b.world.parts.values()
    }
