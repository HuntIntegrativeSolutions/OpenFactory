"""Tag history for the viewer's trend panel (PLAN 3.6): a ring buffer per AI tag.

Behavior spec
-------------
:class:`TagHistory` keeps the last :data:`WINDOW_S` seconds of every AI tag in the I/O table,
sampled on simulated time every :data:`PERIOD_S` seconds (2 Hz): :meth:`TagHistory.observe` is
called after every tick with the simulated time, and records a sample of every AI tag when the
time has reached the next sample point, so a 50 Hz run samples every 25th tick and a run at any
speed (``--as-fast-as-possible``, a slower rate) keeps the same 2 Hz of simulated time. Each tag's
buffer is a ``deque`` with room for one window (1201 samples), so memory is fixed by the number of
AI tags, never by the length of the run, and the oldest sample drops as each new one arrives.

:meth:`TagHistory.series` returns one tag's samples, optionally only the last ``seconds``, as two
lists, ``t`` (s, simulated) and ``v`` (engineering units), with the tag's units and range, so a
client can draw it without asking for anything else. The values are what the I/O table holds: an
AI's engineering value after any fault on its component, the same value the PLC reads.

Only AI tags have a history (the ruled scope: a trend of any analog input); a DI, DO or AO tag
raises :class:`KeyError` with the reason.
"""

from __future__ import annotations

from collections import deque
from typing import Any

#: Sample period, simulated seconds (2 Hz: 1200 samples in ten minutes).
PERIOD_S = 0.5
#: How much history each tag keeps, simulated seconds.
WINDOW_S = 600.0
#: Samples per tag: one window, both ends included.
MAXLEN = round(WINDOW_S / PERIOD_S) + 1
#: Slack on the sample clock, against float error in the summed tick time.
EPS = 1e-9


class TagHistory:
    def __init__(self, io: Any) -> None:
        self.io = io
        self.tags = [t.name for t in io.tags() if t.kind == "AI"]
        self.t: deque[float] = deque(maxlen=MAXLEN)
        self.values: dict[str, deque[float]] = {n: deque(maxlen=MAXLEN) for n in self.tags}
        self.next_t = 0.0

    def observe(self, time: float) -> None:
        """Record a sample of every AI tag if ``time`` has reached the next sample point."""
        if not self.tags or time + EPS < self.next_t:
            return
        self.t.append(round(time, 6))
        for name in self.tags:
            self.values[name].append(float(self.io.get(name)))
        # the next point on the 2 Hz grid after this time, so a late tick does not drift it
        self.next_t = (int((time + EPS) / PERIOD_S) + 1) * PERIOD_S

    def series(self, tag: str, seconds: float | None = None) -> dict[str, Any]:
        """``{tag, units, range, period_s, window_s, t, v}`` for one AI tag, the last ``seconds``
        of it (all it holds when None)."""
        if tag not in self.values:
            kind = next((t.kind for t in self.io.tags() if t.name == tag), None)
            why = f"{tag!r} is a {kind} tag; only AI tags have a history" if kind else (
                f"no tag {tag!r} in this scene"
            )
            raise KeyError(why)
        t, v = list(self.t), list(self.values[tag])
        if seconds is not None and t:
            start = t[-1] - seconds - EPS
            first = next((i for i, ti in enumerate(t) if ti >= start), len(t))
            t, v = t[first:], v[first:]
        spec = self.io.tag(tag)
        return {
            "tag": tag,
            "units": spec.units or "",
            "range": list(spec.range) if spec.range else None,
            "period_s": PERIOD_S,
            "window_s": WINDOW_S,
            "t": t,
            "v": v,
        }
