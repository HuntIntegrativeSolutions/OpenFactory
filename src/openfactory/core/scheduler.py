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

**Pause and step (PLAN 5.14h).** :meth:`Scheduler.pause` stops calling ``step`` after the tick
in progress; simulated time stands still, whatever the wall clock does. :meth:`Scheduler.
step_ticks` runs N more ticks while paused, on the scheduler's own thread (so ``stats.ticks`` and
the ``duration_s`` limit count them), and returns once they are done. Resuming restarts the
real-time schedule from that moment, so the paused wall time is never caught up in a burst and
no tick is dropped for it. A run that is never paused calls ``step`` exactly as before.
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
        # PLAN 5.14h: paused, and the ticks asked for while paused, under one condition
        self._cv = threading.Condition()
        self._paused = False
        self._pending = 0
        self._holding = False  # the run's thread is in _hold: no tick is in progress
        #: The current (or last) run's stats, updated as it goes, so a server can report dropped
        #: ticks while the run is live (PLAN 4.3).
        self.stats = RunStats()

    def stop(self) -> None:
        """Ask a running :meth:`run` to return after its current tick. Thread-safe."""
        self._stop.set()
        with self._cv:
            self._cv.notify_all()

    @property
    def paused(self) -> bool:
        return self._paused

    def pause(self, paused: bool = True, wait_s: float = 1.0) -> None:
        """Pause after the tick in progress, or resume (PLAN 5.14h). Thread-safe. A pause waits
        up to ``wait_s`` for that tick to finish, so the caller reads the tick that stands."""
        with self._cv:
            self._paused = bool(paused)
            if not paused:
                self._pending = 0
            self._cv.notify_all()
            if paused and wait_s > 0:
                self._cv.wait_for(lambda: self._holding or self._stop.is_set(), wait_s)

    def step_ticks(self, n: int, timeout: float = 10.0) -> int:
        """While paused, run ``n`` more ticks on the run's thread and wait for them (PLAN
        5.14h); the ticks run so far. RuntimeError when not paused; TimeoutError when they did
        not run in ``timeout`` seconds (no run, or a stopped one)."""
        with self._cv:
            if not self._paused:
                raise RuntimeError("step: pause the simulation first")
            target = self.stats.ticks + self._pending + int(n)
            self._pending += int(n)
            self._cv.notify_all()
            if not self._cv.wait_for(
                lambda: self.stats.ticks >= target or self._stop.is_set(), timeout
            ):
                raise TimeoutError("step: the simulation did not run the ticks asked for")
            return self.stats.ticks

    def _hold(self, limit: int | None, stats: RunStats) -> int:
        """While paused: wait, and run the ticks a step asks for; the ticks run. The caller
        loops until the pause ends or the run stops. ``_holding`` is true while no tick is in
        progress here, which a pause waits for."""
        ran = 0
        with self._cv:
            self._holding = True
            self._cv.notify_all()
            try:
                while self._paused and not self._pending and not self._stop.is_set():
                    self._cv.wait(0.05)
                while self._paused and self._pending and not self._stop.is_set():
                    if limit is not None and stats.ticks >= limit:
                        self._pending = 0
                        break
                    self._pending -= 1
                    self._holding = False
                    self._cv.release()
                    try:
                        self.step(self.dt)
                    finally:
                        self._cv.acquire()
                        self._holding = True
                    stats.ticks += 1
                    ran += 1
                    self._cv.notify_all()
            finally:
                self._holding = False
        return ran

    def run(self, duration_s: float | None = None) -> RunStats:
        self._stop.clear()
        limit = None if duration_s is None else round(duration_s / self.dt)
        stats = self.stats = RunStats()
        wall_start = self._clock()
        if self.realtime:
            self._run_realtime(limit, stats, wall_start)
        else:
            while (limit is None or stats.ticks < limit) and not self._stop.is_set():
                if self._paused:
                    self._hold(limit, stats)
                    continue
                self.step(self.dt)
                stats.ticks += 1
        stats.sim_time = stats.ticks * self.dt
        stats.wall_time = self._clock() - wall_start
        return stats

    def _run_realtime(self, limit: int | None, stats: RunStats, start: float) -> None:
        origin, done = start, 0  # tick `done + k` is due at origin + (k + 1) * dt
        while (limit is None or stats.ticks < limit) and not self._stop.is_set():
            if self._paused:  # PLAN 5.14h: simulated time stands; steps run here
                done += self._hold(limit, stats)
                origin = self._clock() - done * self.dt  # resume from now: no catch-up burst
                continue
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
                if self._paused:
                    break
                stats.max_lateness = max(
                    stats.max_lateness, self._clock() - (origin + (done + 1) * self.dt)
                )
                self.step(self.dt)
                stats.ticks += 1
                done += 1
