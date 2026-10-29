"""Record and replay (PLAN 2.12): ``run --record FILE.ofrec`` and ``openfactory replay FILE.ofrec``.

Behavior spec
-------------
A recording is gzip-compressed JSON Lines (gzip ``mtime`` 0, no file name in its header), so
two recordings of the same scripted run are byte-identical. Every line is one JSON object:

- ``{"header": {...}}`` first: the format and its version, the OpenFactory version, the scene's
  own YAML text, the resolved scene document the viewer loads (``/api/scene``), ``dt``, the analog
  encoding, and ``frame_every``. The file alone is enough to replay the run: no scene file, no
  lockfile. Nothing machine-specific goes in: no paths, host names, addresses, wall-clock times,
  or tokens; every time is simulated.
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
``speed``, and ``/api/events`` as recorded. Fault and exam requests are refused with
"replay: no simulation".
"""

from __future__ import annotations

import gzip
import json
import queue
import threading
import time
import zlib
from collections import Counter, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

FORMAT = "openfactory-recording"
VERSION = 1
#: A full state frame every this many ticks: 10 Hz at the default 50 Hz tick. The replay viewer
#: eases parts between frames; at 200 parts a frame every 5 ticks with gzip level 1 costs the
#: sim about 1.4 ms a tick (every 2 ticks at level 9: up to 12 ms; measured 2026-10-29).
FRAME_EVERY = 5


def _line(obj: dict[str, Any]) -> bytes:
    return (json.dumps(obj, separators=(",", ":"), allow_nan=False) + "\n").encode()


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
        changes = {k: v for k, v in snapshot["io"].items() if k not in self._io or self._io[k] != v}
        self._io.update(changes)
        self._gz.write(_line({"k": snapshot["tick"], "io": changes, "frame": snapshot}))

    def _write(self) -> None:
        while (item := self._queue.get()) is not None:
            kind, data = item
            if kind == "tick":
                tick = data["tick"]
                if tick % FRAME_EVERY == 0:
                    self._frame(data)
                    continue
                changes = {k: v for k, v in data["io"].items() if self._io[k] != v}
                if changes:
                    self._io.update(changes)
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

    def __init__(self, recording: Recording, *, speed: float = 1.0, loop: bool = False) -> None:
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
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- the interface create_app uses -----------------------------------------------------------

    def scene_document(self) -> dict[str, Any]:
        return {**self.recording.header["scene"], "mode": "replay"}

    def start(self, duration_s: float | None = None) -> None:
        self._thread = threading.Thread(target=self._play, name="openfactory-replay", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

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

    def parity(self) -> dict[str, Any]:
        return {"core": [], "viewer": []}

    def apply_event(self, raw: dict[str, Any]) -> Any:
        from openfactory.server.runtime import ViewerEvent

        event = ViewerEvent.model_validate(raw)
        if event.type in ("press", "select"):
            raise ValueError(self.NO_SIM)
        self.event_counts[f"{event.type}:{event.id}"] += 1  # sensor events: counted, not acted on
        return event

    # -- playback ----------------------------------------------------------------------------------

    def _play(self) -> None:
        rec, dt = self.recording, self.recording.dt
        while not self._stop.is_set():
            start = time.monotonic()
            events = iter(rec.events)
            pending = next(events, None)
            for tick, frame in rec.frames:
                due = start + tick * dt / self.speed
                while (wait := due - time.monotonic()) > 0:
                    if self._stop.wait(min(wait, 0.05)):
                        return
                while pending is not None and pending[0] <= tick:
                    self.events.append(pending[1])
                    self.event_counts[f"{pending[1]['type']}:{pending[1]['id']}"] += 1
                    pending = next(events, None)
                self.snapshot = frame
            if not self.loop:
                self.finished = True
                return
