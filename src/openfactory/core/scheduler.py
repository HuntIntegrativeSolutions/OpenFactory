"""Fixed-step scheduler (PLAN §3.1, task 1.6).

Behavior spec
-------------
The scheduler calls a step function with a constant ``dt = 1 / rate_hz`` (default 50 Hz). The
simulation therefore never sees wall-clock time; only how *often* it is stepped depends on mode:

- **Realtime** (default): tick *n* is due at ``start + n·dt`` on the wall clock. After each wake-up
  the scheduler runs every tick that is due (an accumulator of ``elapsed − ticks·dt``), then sleeps
  until the next deadline. Deadlines are absolute, so sleep overshoot does not accumulate into
  drift. If the step function falls more than ``max_catch_up`` ticks behind (for example the host
  was suspended), the excess ticks are dropped and counted in :attr:`RunStats.dropped`, and the
  schedule restarts from now, rather than spiraling into ever-longer catch-up bursts.
- **As fast as possible**: ticks run back to back with no sleeping, for tests and CI. The sequence
  of ``step(dt)`` calls is identical to realtime mode, so results are identical.

:meth:`Scheduler.run` stops after ``duration_s`` simulated seconds (rounded to whole ticks), when
:meth:`Scheduler.stop` is called from another thread, or never if neither applies. The clock and
sleep functions are injectable so the timing logic can be tested without waiting.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

DEFAULT_RATE_HZ = 50.0
DEFAULT_MAX_CATCH_UP = 10
#: A deadline within this fraction of a tick counts as reached. Without it, float error can leave
#: ``(now - origin) / dt`` at 0.99999… right after sleeping to a deadline, and the scheduler would
#: keep sleeping for amounts too small to advance the clock.
_DEADLINE_TOL = 1e-6


@dataclass
class RunStats:
    """Outcome of one :meth:`Scheduler.run`."""

    ticks: int = 0
    sim_time: float = 0.0
    wall_time: float = 0.0
    #: Ticks skipped because the step function fell more than ``max_catch_up`` behind.
    dropped: int = 0
    #: Worst lateness of a tick behind its deadline, in seconds (realtime mode only).
    max_lateness: float = 0.0

    @property
    def drift(self) -> float:
        """Relative difference between wall time and simulated time (0.01 = 1 %)."""
        return (self.wall_time - self.sim_time) / self.sim_time if self.sim_time else 0.0


class Scheduler:
    def __init__(
        self,
        step: Callable[[float], None],
        rate_hz: float = DEFAULT_RATE_HZ,
        *,
        realtime: bool = True,
        max_catch_up: int = DEFAULT_MAX_CATCH_UP,
        clock: Callable[[], float] = time.perf_counter,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if rate_hz <= 0:
            raise ValueError("rate_hz must be positive")
        if max_catch_up < 1:
            raise ValueError("max_catch_up must be at least 1")
        self.step = step
        self.dt = 1.0 / rate_hz
        self.realtime = realtime
        self.max_catch_up = max_catch_up
        self._clock = clock
        self._sleep = sleep
        self._stop = threading.Event()

    def stop(self) -> None:
        """Ask a running :meth:`run` to return after its current tick. Thread-safe."""
        self._stop.set()

    def run(self, duration_s: float | None = None) -> RunStats:
        self._stop.clear()
        limit = None if duration_s is None else round(duration_s / self.dt)
        stats = RunStats()
        wall_start = self._clock()
        if self.realtime:
            self._run_realtime(limit, stats, wall_start)
        else:
            while (limit is None or stats.ticks < limit) and not self._stop.is_set():
                self.step(self.dt)
                stats.ticks += 1
        stats.sim_time = stats.ticks * self.dt
        stats.wall_time = self._clock() - wall_start
        return stats

    def _run_realtime(self, limit: int | None, stats: RunStats, start: float) -> None:
        origin, done = start, 0  # tick `done + k` is due at origin + (k + 1) * dt
        while (limit is None or stats.ticks < limit) and not self._stop.is_set():
            now = self._clock()
            due = int((now - origin) / self.dt + _DEADLINE_TOL)  # ticks due by now
            behind = due - done
            if behind <= 0:
                self._sleep(origin + (done + 1) * self.dt - now)
                continue
            if behind > self.max_catch_up:
                skipped = behind - self.max_catch_up
                stats.dropped += skipped
                origin += skipped * self.dt  # restart the schedule instead of spiraling
                behind = self.max_catch_up
            for _ in range(behind):
                if (limit is not None and stats.ticks >= limit) or self._stop.is_set():
                    return
                stats.max_lateness = max(
                    stats.max_lateness, self._clock() - (origin + (done + 1) * self.dt)
                )
                self.step(self.dt)
                stats.ticks += 1
                done += 1
