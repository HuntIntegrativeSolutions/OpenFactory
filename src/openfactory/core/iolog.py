"""Tag change log (PLAN 5.14a): every I/O table change of a live run, kept in a ring.

Behavior spec
-------------
:class:`IoLog` holds one entry per tag change: the tick, the tag and its new value. Each entry
also has a simulated time (``tick * dt``) and a writer, both derived when it is read:
``simulation`` for a DI or AI tag and ``plc`` for a DO or AO, because each kind has exactly one
writer side (:data:`openfactory.core.iotable.WRITER_FOR_KIND`).

The ring keeps the last :data:`WINDOW_S` seconds of simulated time or the last
:data:`MAX_CHANGES` changes, whichever is smaller. Its memory is therefore fixed by the cap,
never by the length of the run. :attr:`IoLog.dropped_to` is the newest tick whose change was
dropped, so a reader can tell whether what it asked for is all still held.

**Where the changes come from.** The changes are the ones a recording holds (PLAN 2.12), made
by the same function, :func:`changed`. :class:`openfactory.record.Recorder` diffs each tick's
snapshot with it, and :meth:`IoLog.feed` diffs the I/O table's values after each tick with it.
The first feed lists every tag, as a recording's tick 0 does. A live run feeds the log from the
simulation thread after the step lock is released. Under the lock it only copies the table's
values (:meth:`IOTable.values`, the same copy a snapshot holds), and it builds no snapshot on
the tick path (PLAN 2.9, ruled 2026-10-30). The log has its own small lock, so a reader on the
web server's thread never waits for a tick.

:meth:`IoLog.query` serves ``GET /api/iolog``: the changes after ``since_tick``, optionally only
some tags, in the order they happened. A replay builds an uncapped log from the recording
(:meth:`IoLog.from_changes`) and serves it up to the tick being shown.

Not a safety-rated simulation. OpenFactory is a training and demo tool.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Iterable, Mapping
from typing import Any

from openfactory.core.iotable import WRITER_FOR_KIND, Writer

#: How much simulated time the live log keeps, seconds.
WINDOW_S = 600.0
#: The most changes the live log keeps.
MAX_CHANGES = 20_000

WRITER_NAMES = {Writer.SIM: "simulation", Writer.PLC: "plc"}


def changed(last: dict[str, Any], now: Mapping[str, Any]) -> dict[str, Any]:
    """The tags in ``now`` whose value differs from ``last`` (or that ``last`` lacks), in
    ``now``'s order; ``last`` is updated to match. The one diff behind a recording's I/O lines
    and the live change log."""
    changes = {k: v for k, v in now.items() if k not in last or last[k] != v}
    last.update(changes)
    return changes


class IoLog:
    """A ring of tag changes (see the module docstring)."""

    def __init__(
        self,
        kinds: Mapping[str, str],
        dt: float,
        *,
        window_s: float | None = WINDOW_S,
        max_changes: int | None = MAX_CHANGES,
    ) -> None:
        #: Tag name -> kind (DI, DO, AI, AO), for the writer and for refusing unknown tags.
        self.kinds = dict(kinds)
        self.dt = float(dt)
        self._window_ticks = None if window_s is None else round(window_s / self.dt)
        self._ring: deque[tuple[int, str, Any]] = deque(maxlen=max_changes)
        self._last: dict[str, Any] = {}
        self._lock = threading.Lock()
        #: The last tick fed; -1 before the first.
        self.tick = -1
        #: The newest tick a dropped change had; -1 while nothing has been dropped.
        self.dropped_to = -1

    @classmethod
    def from_changes(
        cls, kinds: Mapping[str, str], dt: float, changes: Iterable[tuple[int, Mapping[str, Any]]]
    ) -> IoLog:
        """An uncapped log of a recording's ``(tick, {tag: value})`` lines (a replay)."""
        log = cls(kinds, dt, window_s=None, max_changes=None)
        for tick, items in changes:
            log.add(tick, items)
        return log

    def feed(self, tick: int, values: Mapping[str, Any]) -> None:
        """Log what changed in the I/O table's ``values`` as of the end of ``tick``."""
        self.add(tick, changed(self._last, values))

    def add(self, tick: int, changes: Mapping[str, Any]) -> None:
        """Log ``changes`` (``{tag: value}``) at ``tick``, then drop what the cap no longer
        holds."""
        with self._lock:
            ring = self._ring
            if ring.maxlen is not None and len(ring) + len(changes) > ring.maxlen:
                over = len(ring) + len(changes) - ring.maxlen
                if over > len(ring):  # more changes in one tick than the whole ring holds
                    self.dropped_to = tick
                else:
                    self.dropped_to = max(self.dropped_to, ring[over - 1][0])
            ring.extend((tick, tag, value) for tag, value in changes.items())
            if self._window_ticks is not None:
                oldest = tick - self._window_ticks
                while ring and ring[0][0] <= oldest:
                    self.dropped_to = max(self.dropped_to, ring.popleft()[0])
            self.tick = tick

    def __len__(self) -> int:
        return len(self._ring)

    @property
    def first_tick(self) -> int | None:
        """The tick of the oldest change held, or None when empty."""
        with self._lock:
            return self._ring[0][0] if self._ring else None

    def entries(self, until_tick: int | None = None) -> list[tuple[int, str, Any]]:
        """Every change held, ``(tick, tag, value)`` in order, up to ``until_tick``."""
        with self._lock:
            entries = list(self._ring)
        return entries if until_tick is None else [e for e in entries if e[0] <= until_tick]

    def held(self, until_tick: int | None = None) -> tuple[list[tuple[int, str, Any]], int]:
        """:meth:`entries` and :attr:`dropped_to`, read together under the log's lock."""
        with self._lock:
            entries, dropped_to = list(self._ring), self.dropped_to
        if until_tick is not None:
            entries = [e for e in entries if e[0] <= until_tick]
        return entries, dropped_to

    def query(
        self,
        since_tick: int = -1,
        tags: Iterable[str] | None = None,
        *,
        until_tick: int | None = None,
    ) -> dict[str, Any]:
        """The changes after ``since_tick`` (up to ``until_tick``), for ``GET /api/iolog``.
        KeyError naming any tag that is not in the scene."""
        wanted = None if tags is None else list(tags)
        if wanted is not None:
            unknown = [t for t in wanted if t not in self.kinds]
            if unknown:
                raise KeyError(f"not a tag of this scene: {', '.join(unknown)}")
        keep = None if wanted is None else set(wanted)
        with self._lock:
            entries = list(self._ring)
            tick, dropped_to = self.tick, self.dropped_to
        if until_tick is not None:
            tick = min(tick, until_tick)
        out = [
            {
                "tick": k,
                "t": round(k * self.dt, 6),
                "tag": tag,
                "value": value,
                "writer": WRITER_NAMES[WRITER_FOR_KIND[self.kinds[tag]]],
            }
            for k, tag, value in entries
            if since_tick < k <= tick and (keep is None or tag in keep)
        ]
        return {
            "dt": self.dt,
            "tick": tick,
            "since_tick": since_tick,
            "first_tick": entries[0][0] if entries else None,
            # every change after since_tick is still held: nothing newer than it was dropped
            "complete": dropped_to <= since_tick,
            "changes": out,
        }
