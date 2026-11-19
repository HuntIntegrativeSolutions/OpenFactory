"""Record and replay (PLAN 2.12): ``run --record FILE.ofrec`` and ``openfactory replay FILE.ofrec``.

Behavior spec
-------------
A recording is gzip-compressed JSON Lines (gzip ``mtime`` 0, no file name in its header), so
two recordings of the same scripted run are byte-identical. Every line is one JSON object:

- ``{"header": {...}}`` first: the format and its version, the OpenFactory version, the scene's
  own YAML text, the resolved scene document the viewer loads (``/api/scene``), ``dt``, the analog
  encoding, and ``frame_every``; ``"exam": true`` only for a run in exam mode, whose frames hide
  faults (PLAN 2.11; ``openfactory look`` says so, 5.14b). The file alone is enough to replay
  the run: no scene file, no lockfile. Nothing machine-specific goes in: no paths, host names,
  addresses, wall-clock times, or tokens; every time is simulated.
- ``{"k": TICK, "io": {TAG: VALUE}}`` for every tick in which an I/O table tag changed (tick 0
  lists every tag), and ``{"k": TICK, "io": {...}, "frame": {...}}`` every ``frame_every`` ticks
  (and at tick 0) with the full state frame the viewer streams (``/ws/state``).
- ``{"k": TICK, "event": {...}}`` for each runtime event (an injected or cleared fault, an exam
  switch, a viewer button press), as ``/api/events`` lists it.
- ``{"end": {...}}`` last: the run summary (spawned, removed, lost, by remover), the statistics
  block, and the script traceback if the run stopped on one.

:class:`Recorder` takes each tick's snapshot (snapshots are never mutated after they are built,
PLAN 1.9) and does all diffing, serialization, and compression on its own writer thread, never
under the simulation's step lock. It is closed in a ``finally``, so a run stopped by Ctrl+C or a
script fault still leaves a complete file. :func:`read_recording` accepts a file cut short (a
crash mid-write): it returns what it could read, marked ``truncated``.

:class:`ReplayRuntime` serves a recording to the normal web app with no simulation: the same
``/api/scene`` (with ``"mode": "replay"``, which tells the viewer to draw parts where the
recording has them instead of simulating them), ``/ws/state`` frames at the recorded pace times
``speed``, ``/api/events`` as recorded, and ``/api/iolog`` (PLAN 5.14a) from the recording's
I/O lines, every change up to the tick being shown, uncapped, and ``/api/look`` (PLAN 5.14b) at
the frame being shown. Fault and exam requests are
refused with "replay: no simulation".

Pause, seek and step (PLAN 5.14f): :meth:`ReplayRuntime.pause` stops or resumes playback where it
is; :meth:`ReplayRuntime.seek` shows the recording's frame at or before a tick (the events log is
rebuilt up to it, and a playing replay goes on from there at its speed); :meth:`ReplayRuntime.
step` pauses and moves by whole frames, back or forward. ``ReplayRuntime(paused=True)`` starts
paused at the first frame (``openfactory strip``). A replay that played to its end without
``--loop`` is ``finished``; a seek back and play starts it again.
"""

from __future__ import annotations

import bisect
import gzip
import hashlib
import json
import queue
import threading
import time
import zlib
from collections import Counter, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openfactory.core.iolog import IoLog, changed

FORMAT = "openfactory-recording"
VERSION = 1
#: A full state frame every this many ticks: 10 Hz at the default 50 Hz tick. The replay viewer
#: eases parts between frames; at 200 parts a frame every 5 ticks with gzip level 1 costs the
#: sim about 1.4 ms a tick (every 2 ticks at level 9: up to 12 ms; measured 2026-10-29).
FRAME_EVERY = 5


def _line(obj: dict[str, Any]) -> bytes:
    return (json.dumps(obj, separators=(",", ":"), allow_nan=False) + "\n").encode()


def recording_header(runtime: Any, scene_yaml: str | None, encoding: str | None) -> dict[str, Any]:
    """The header a recording of ``runtime``'s run carries (after ``format`` and ``version``):
    ``run --record`` writes it, and a capture's piece of recording (PLAN 5.14e) reuses it."""
    from openfactory import __version__

    loaded = runtime.loaded
    header = {
        "openfactory": __version__,
        "scene_yaml": scene_yaml if scene_yaml is not None else "",
        # no mesh URLs: a replay serves no /models/ and draws the primitives (PLAN 2.15)
        # nor snap ports: they are for the editor, and recordings stay as they were (4.1d)
        "scene": runtime.scene_document(meshes=False, snaps=False),
        "dt": runtime.scheduler.dt,
        "encoding": encoding or loaded.scene.plc.analog_encoding,
        "frame_every": FRAME_EVERY,
    }
    if loaded.exam is not None:  # its frames hide faults: `look` says so (PLAN 5.14b)
        header["exam"] = True
    return header


class Recorder:
    """Write one run to ``path`` (see the module docstring for the format)."""

    def __init__(self, path: str | Path, header: dict[str, Any], first: dict[str, Any]) -> None:
        self.path = Path(path)
        self._raw = self.path.open("wb")
        self._gz = gzip.GzipFile(
            filename="", mode="wb", fileobj=self._raw, mtime=0, compresslevel=1
        )
        self._queue: queue.Queue[tuple[str, Any] | None] = queue.Queue()
        self._io: dict[str, Any] = {}
        self._gz.write(_line({"header": {"format": FORMAT, "version": VERSION, **header}}))
        self._frame(first)
        self._thread = threading.Thread(
            target=self._write, name="openfactory-recorder", daemon=True
        )
        self._thread.start()
        self.closed = False

    # -- called from the simulation thread: cheap, no serialization ------------------------------

    def tick(self, snapshot: dict[str, Any]) -> None:
        self._queue.put(("tick", snapshot))

    def event(self, entry: dict[str, Any]) -> None:
        self._queue.put(("event", dict(entry)))

    def close(self, end: dict[str, Any]) -> None:
        """Flush everything queued, write the end record, and close the file. Idempotent."""
        if self.closed:
            return
        self.closed = True
        self._queue.put(("end", end))
        self._queue.put(None)
        self._thread.join()
        self._gz.close()
        self._raw.close()

    # -- writer thread -----------------------------------------------------------------------------

    def _frame(self, snapshot: dict[str, Any]) -> None:
        """Tick 0's full I/O table, then only what changed; the frame holds the whole table."""
        changes = changed(self._io, snapshot["io"])
        self._gz.write(_line({"k": snapshot["tick"], "io": changes, "frame": snapshot}))

    def _write(self) -> None:
        while (item := self._queue.get()) is not None:
            kind, data = item
            if kind == "tick":
                tick = data["tick"]
                if tick % FRAME_EVERY == 0:
                    self._frame(data)
                    continue
                changes = changed(self._io, data["io"])  # the live change log's diff (5.14a)
                if changes:
                    self._gz.write(_line({"k": tick, "io": changes}))
            elif kind == "event":
                self._gz.write(_line({"k": data.get("core_tick", 0), "event": data}))
            else:
                self._gz.write(_line({"end": data}))


@dataclass
class Recording:
    header: dict[str, Any]
    #: (tick, full state frame), in tick order.
    frames: list[tuple[int, dict[str, Any]]] = field(default_factory=list)
    #: (tick, the tags that changed in that tick and their new values), in tick order.
    io: list[tuple[int, dict[str, Any]]] = field(default_factory=list)
    events: list[tuple[int, dict[str, Any]]] = field(default_factory=list)
    end: dict[str, Any] | None = None
    #: The file was cut short (no end record, or a damaged last gzip block).
    truncated: bool = False

    @property
    def dt(self) -> float:
        return float(self.header["dt"])

    @property
    def held_from(self) -> int:
        """The tick before which the recording holds no change: -1 for a whole run, and a
        capture's piece's first tick (PLAN 5.14e), whose first line is the whole I/O table, not
        changes. ``look`` passes it as the change log's ``dropped_to``."""
        first = self.frames[0][0] if self.frames else 0
        return first if first > 0 else -1


def read_recording(path: str | Path) -> Recording:
    """Read a recording; a file cut short is read up to the damage and marked ``truncated``."""
    lines: list[bytes] = []
    truncated = False
    with gzip.open(path, "rb") as fh:
        try:
            for line in fh:
                lines.append(line)
        except (EOFError, zlib.error, gzip.BadGzipFile):
            truncated = True
    if not lines:
        raise ValueError(f"{path}: not an OpenFactory recording (empty)")
    try:
        header = json.loads(lines[0])["header"]
    except (ValueError, KeyError, TypeError):
        raise ValueError(f"{path}: not an OpenFactory recording") from None
    if header.get("format") != FORMAT:
        raise ValueError(f"{path}: not an OpenFactory recording (format {header.get('format')!r})")
    if header.get("version") != VERSION:
        raise ValueError(
            f"{path}: recording format version {header.get('version')}, need {VERSION}"
        )
    rec = Recording(header)
    for raw in lines[1:]:
        try:
            obj = json.loads(raw)
        except ValueError:
            truncated = True  # a half-written last line
            break
        if "end" in obj:
            rec.end = obj["end"]
            continue
        tick = obj["k"]
        if "event" in obj:
            rec.events.append((tick, obj["event"]))
            continue
        if "frame" in obj:
            rec.frames.append((tick, obj["frame"]))
        if obj.get("io"):
            rec.io.append((tick, obj["io"]))
    rec.truncated = truncated or rec.end is None
    if not rec.frames:
        raise ValueError(f"{path}: the recording has no state frames")
    return rec


def run_lines(path: str | Path) -> list[tuple[int | None, bytes]]:
    """Every line of a recording after its header, decompressed, as ``(tick, line)``; the end
    record has tick ``None``. The header is left out: it carries the OpenFactory version and the
    scene's own text, which change without the run changing (PLAN §9 rule 13)."""
    with gzip.open(path, "rb") as fh:
        lines = fh.read().splitlines()
    out: list[tuple[int | None, bytes]] = []
    for raw in lines[1:]:
        obj = json.loads(raw)
        out.append((None if "end" in obj else int(obj["k"]), raw))
    return out


def line_digest(line: bytes) -> str:
    """A short digest of one recorded line, for a committed run reference (PLAN §9 rule 13)."""
    return hashlib.sha256(line).hexdigest()[:16]


def first_difference(
    a: list[tuple[int | None, Any]], b: list[tuple[int | None, Any]], dt: float
) -> str | None:
    """Where two runs first part, as ``(tick, line)`` lists from :func:`run_lines` (or
    ``(tick, digest)`` lists from a run reference):
    ``None`` when they are the same, else a sentence naming the tick and simulated time."""
    for (ka, la), (kb, lb) in zip(a, b, strict=False):
        if (ka, la) == (kb, lb):
            continue
        if ka is None or kb is None:
            return (
                "the run summary (the end record) differs"
                if ka == kb
                else (f"one run ends before tick {ka if kb is None else kb}")
            )
        tick = min(ka, kb)
        return f"first differs at tick {tick} (t = {tick * dt:.2f} s)"
    if len(a) != len(b):
        longer = a if len(a) > len(b) else b
        tick = longer[min(len(a), len(b))][0]
        where = "after its end record" if tick is None else f"at tick {tick}"
        return f"one run has more lines, first {where}"
    return None


def io_table(rec: Recording) -> str:
    """The I/O change log as text: simulated time, tick, tag, value (feeds PLAN 2.8). Tick 0 lists
    every tag's starting value."""
    rows = [f"{'t (s)':>9}  {'tick':>6}  tag = value"]
    for tick, changes in rec.io:
        rows += [f"{tick * rec.dt:9.2f}  {tick:6d}  {tag} = {v}" for tag, v in changes.items()]
    return "\n".join(rows)


class ReplayRuntime:
    """Serves a recording to :func:`openfactory.server.app.create_app` with no simulation."""

    NO_SIM = "replay: no simulation; faults, exam switches and buttons are as recorded"

    def __init__(
        self, recording: Recording, *, speed: float = 1.0, loop: bool = False, paused: bool = False
    ) -> None:
        if speed <= 0:
            raise ValueError("replay speed must be positive")
        self.recording = recording
        self.speed = speed
        self.loop = loop
        self.lock = threading.Lock()
        self.snapshot: dict[str, Any] = recording.frames[0][1]
        self.events: deque[dict[str, Any]] = deque(maxlen=1000)
        self.event_counts: Counter[str] = Counter()
        self.finished = False
        #: Paused: the frame shown stays until a seek, a step or play (PLAN 5.14f).
        self.paused = paused
        self._ticks = [k for k, _ in recording.frames]
        self._event_ticks = [k for k, _ in recording.events]
        self._index = 0  # the frame shown
        self._seq = 0  # bumped by every seek, so the playback thread starts again from it
        self._wake = threading.Event()
        self._viewer_counts: Counter[str] = Counter()  # the browser's own reports (parity)
        self._events_to(self._ticks[0])
        #: Every recorded tag change (PLAN 5.14a), served up to the tick being shown.
        self.iolog = IoLog.from_changes(
            {t["name"]: t["kind"] for t in recording.header["scene"]["tags"]},
            recording.dt,
            recording.io,
        )
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- the interface create_app uses -----------------------------------------------------------

    def scene_document(self) -> dict[str, Any]:
        return {**self.recording.header["scene"], "mode": "replay"}

    def start(self, duration_s: float | None = None) -> None:
        self._spawn()

    def _spawn(self) -> None:
        if self._stop.is_set() or (self._thread is not None and self._thread.is_alive()):
            return
        self._thread = threading.Thread(target=self._play, name="openfactory-replay", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)

    # -- pause, seek and step (PLAN 5.14f) ---------------------------------------------------------

    def _events_to(self, tick: int) -> None:
        """The event log and counts as they were when the frame at ``tick`` was shown."""
        upto = [e for k, e in self.recording.events if k <= tick]
        self.events = deque(upto[-1000:], maxlen=1000)
        self.event_counts = Counter(f"{e['type']}:{e['id']}" for e in upto) + self._viewer_counts

    def _show(self, index: int) -> None:
        """Show frame ``index`` at once (under the lock)."""
        self._index = index
        self.snapshot = self.recording.frames[index][1]
        self._events_to(self._ticks[index])
        self._seq += 1

    def seek(self, tick: int) -> dict[str, Any]:
        """Show the recording's frame at or before ``tick`` (its first frame before that)."""
        index = max(bisect.bisect_right(self._ticks, int(tick)) - 1, 0)
        with self.lock:
            self._show(index)
            self.finished = False
        self._wake.set()
        if not self.paused:
            self._spawn()
        return self.replay_status()

    def pause(self, paused: bool) -> dict[str, Any]:
        """Pause, or play on from the frame shown (from the start after a finished replay)."""
        self.paused = bool(paused)
        if not self.paused and self.finished:
            with self.lock:
                self._show(0)
                self.finished = False
        self._wake.set()
        if not self.paused:
            self._spawn()
        return self.replay_status()

    def step(self, frames: int) -> dict[str, Any]:
        """Pause and move ``frames`` frames (negative: back), stopping at either end."""
        self.paused = True
        index = min(max(self._index + int(frames), 0), len(self._ticks) - 1)
        return self.seek(self._ticks[index])

    def replay_status(self) -> dict[str, Any]:
        """Where the replay is: ``GET /api/replay`` and every replay control's answer."""
        tick = self._ticks[self._index]
        return {
            "tick": tick,
            "t": round(tick * self.recording.dt, 6),
            "paused": self.paused,
            "finished": self.finished,
            "index": self._index,
            "frames": len(self._ticks),
            "first_tick": self._ticks[0],
            "last_tick": self._ticks[-1],
            "frame_every": int(self.recording.header.get("frame_every", FRAME_EVERY)),
            "dt": self.recording.dt,
            "speed": self.speed,
        }

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def status(self) -> dict[str, Any]:
        snap = self.snapshot
        return {
            "scene": self.recording.header["scene"]["scene"]["name"],
            "t": snap["t"],
            "tick": snap["tick"],
            "running": self.running,
            "parts": len(snap["parts"]),
            "replay": True,
            "finished": self.finished,
            "paused": self.paused,
            "last_tick": self.recording.frames[-1][0],
            "events": dict(self.event_counts),
        }

    def faults(self) -> dict[str, Any]:
        active = [
            {"id": cid, "kind": state["fault"]}
            for cid, state in self.snapshot["components"].items()
            if "fault" in state
        ]
        return {"active": active, "supported": {}}

    def inject_fault(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise ValueError(self.NO_SIM)

    def clear_fault(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise KeyError(self.NO_SIM)

    def exam(self) -> dict[str, Any]:
        return {"enabled": False, "switches": []}

    def set_exam_switch(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise KeyError(self.NO_SIM)

    def statistics(self) -> dict[str, Any]:
        return dict((self.recording.end or {}).get("statistics", {}))

    def check_status(self) -> dict[str, Any]:
        """A replay grades nothing live: no checks panel (PLAN 4.1i)."""
        return {"t": 0.0, "duration_s": None, "graded": False, "checks": [], "replay": True}

    def history(self, tag: str, seconds: float | None = None) -> dict[str, Any]:
        raise KeyError("replay: a recording has no tag history (PLAN 3.6); run the scene")

    def parity(self) -> dict[str, Any]:
        return {"core": [], "viewer": []}

    def iolog_query(self, since_tick: int = -1, tags: list[str] | None = None) -> dict[str, Any]:
        """The recorded tag changes after ``since_tick`` up to the tick being shown."""
        return self.iolog.query(since_tick, tags, until_tick=self.snapshot["tick"])

    def look(self, since_s: float = 5.0, component: str | None = None) -> str:
        """The plant in words at the frame being shown (PLAN 5.14b)."""
        from openfactory.observe import look, recording_plant

        snap = self.snapshot
        changes = self.iolog.entries(until_tick=int(snap["tick"]))
        plant = recording_plant(self.recording.header)
        document = self.recording.header["scene"]
        exam = bool(self.recording.header.get("exam"))
        return look(
            document, snap, changes, plant, since_s=since_s, component=component, exam=exam,
            dropped_to=self.recording.held_from,
        )  # fmt: skip

    def apply_event(self, raw: dict[str, Any]) -> Any:
        from openfactory.server.runtime import ViewerEvent

        event = ViewerEvent.model_validate(raw)
        if event.type in ("press", "select"):
            raise ValueError(self.NO_SIM)
        key = f"{event.type}:{event.id}"  # sensor events: counted, not acted on
        self.event_counts[key] += 1
        self._viewer_counts[key] += 1
        return event

    # -- playback ----------------------------------------------------------------------------------

    def _play(self) -> None:
        """Show each frame at its recorded time over ``speed``, timed from the frame playback
        (re)started at: the first, or the one a seek showed. A capture's piece starts past tick
        0 (PLAN 5.14e), so time is counted from the frame, never from tick 0."""
        rec, dt, ticks = self.recording, self.recording.dt, self._ticks
        while not self._stop.is_set():
            if self.paused:
                self._wake.wait(0.05)
                self._wake.clear()
                continue
            with self.lock:
                i, seq = self._index, self._seq
            start, base = time.monotonic(), ticks[i]
            while not self._stop.is_set() and not self.paused and seq == self._seq:
                if i + 1 >= len(ticks):  # the end
                    if not self.loop:
                        self.finished = True
                        return
                    with self.lock:
                        self._show(0)
                    break
                wait = start + (ticks[i + 1] - base) * dt / self.speed - time.monotonic()
                if wait > 0:
                    self._wake.wait(min(wait, 0.05))
                    self._wake.clear()
                    continue
                with self.lock:
                    if seq != self._seq:
                        break
                    i += 1
                    self._index = i
                    lo = bisect.bisect_right(self._event_ticks, ticks[i - 1])
                    hi = bisect.bisect_right(self._event_ticks, ticks[i])
                    for _, e in rec.events[lo:hi]:
                        self.events.append(e)
                        self.event_counts[f"{e['type']}:{e['id']}"] += 1
                    self.snapshot = rec.frames[i][1]
