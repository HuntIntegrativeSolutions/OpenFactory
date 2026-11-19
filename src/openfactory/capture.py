"""Capture: "show the assistant what I see" (PLAN 5.14e).

Behavior spec
-------------
A live run keeps the last 60 s in memory, always (ruling 3): :class:`CaptureBuffer` holds a full
state frame every ``FRAME_EVERY`` ticks (10 Hz at 50 Hz, 600 frames), the frames a recording
would hold; the tag changes between them come from the run's change log (:mod:`openfactory.core.
iolog`, which holds the last 600 s), and the events from the runtime's event log. The frame is
the snapshot the viewer reads, built on that tick under the step lock (as while recording), so a
viewer reading the same tick does not build it again.

``POST /api/capture`` (see :mod:`openfactory.server.app` for its checks) hands the image, the
camera and the note to :func:`write_capture`, which writes one folder,
``<base>/<scene>/<tick>/`` (``-2``, ``-3`` ... after it when that tick was captured already),
with six files:

- ``view.png``: the picture as the person saw it (the plant, the labels and the status lines,
  drawn by the viewer onto one canvas);
- ``view.json``: the camera (position and target, or the plan view), the ``view=`` parameter
  that reopens it, the selected component, the labels setting, and the tick the picture is from;
- ``report.md``: what was captured, when, the selected component, and the typed note, escaped:
  the note is data, written as text inside a quotation and never used in a path, a file name or
  a command;
- ``look.md``: ``openfactory look`` at the capture's tick (and the selected component's own
  report after it);
- ``snapshot.json``: the full state at that tick, as ``GET /api/state`` gives it;
- ``last-60s.ofrec``: the last 60 s as a recording, ending at the capture's tick, which
  ``openfactory replay`` and ``openfactory look`` read like any other.

The server names the folder: the base is ``--capture-dir`` (default ``openfactory-captures/``
where ``openfactory run`` was started, ruling 2), the scene's name is reduced to letters,
digits, ``-`` and ``_``, and the tick is a number. Nothing the browser sends becomes part of a
path.

The piece of recording is the recorder's own format (:mod:`openfactory.record`): the header the
run would have written, a first frame whose ``io`` is the whole I/O table (as tick 0's is), the
change log's lines up to the capture's tick, a frame every ``FRAME_EVERY`` ticks, the events,
the snapshot at the capture's tick as the last frame, and an end record naming the capture. When
the change log no longer holds every change after the buffer's first frame, the piece starts at
the first frame after the newest change it dropped.
"""

from __future__ import annotations

import gzip
import json
import re
import threading
from collections import deque
from pathlib import Path
from typing import Any

from openfactory.record import FORMAT, FRAME_EVERY, VERSION, _line

#: How much a live run keeps for a capture, seconds of simulated time (PLAN 5.14e).
WINDOW_S = 60.0
#: The largest picture a capture takes, bytes of PNG.
MAX_IMAGE_BYTES = 5 * 1024 * 1024
#: The longest note kept, characters.
MAX_NOTE_CHARS = 2000
#: The capture folder's base, beside where ``openfactory run`` was started (ruling 2).
DEFAULT_DIR = "openfactory-captures"
FILES = ("view.png", "view.json", "report.md", "look.md", "snapshot.json", "last-60s.ofrec")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class CaptureBusy(RuntimeError):
    """A capture was asked for while another was being written (``POST /api/capture``: 409)."""


class CaptureBuffer:
    """The last :data:`WINDOW_S` of state frames, one every ``frame_every`` ticks."""

    def __init__(self, dt: float, frame_every: int = FRAME_EVERY, window_s: float = WINDOW_S):
        self.dt = float(dt)
        self.frame_every = int(frame_every)
        self.frames: deque[tuple[int, dict[str, Any]]] = deque(
            maxlen=max(1, round(window_s / (self.dt * self.frame_every)))
        )
        self._lock = threading.Lock()

    def wants(self, tick: int) -> bool:
        return tick % self.frame_every == 0

    def add(self, tick: int, snapshot: dict[str, Any]) -> None:
        with self._lock:
            self.frames.append((tick, snapshot))

    def held(self) -> list[tuple[int, dict[str, Any]]]:
        with self._lock:
            return list(self.frames)


def piece_lines(
    header: dict[str, Any],
    frames: list[tuple[int, dict[str, Any]]],
    changes: list[tuple[int, str, Any]],
    dropped_to: int,
    events: list[dict[str, Any]],
    last: dict[str, Any],
) -> list[bytes]:
    """The lines of the recording piece (see the module docstring), ending at ``last``'s tick."""
    end_tick = int(last["tick"])
    frames = [(k, f) for k, f in frames if k < end_tick and k > dropped_to] + [(end_tick, last)]
    start = frames[0][0]
    by_tick: dict[int, dict[str, Any]] = {}
    for k, tag, value in changes:
        if start < k <= end_tick:
            by_tick.setdefault(k, {})[tag] = value
    frame_at = dict(frames)
    events_at: dict[int, list[dict[str, Any]]] = {}
    for e in events:
        k = int(e.get("core_tick", 0))
        if start <= k <= end_tick:
            events_at.setdefault(k, []).append(e)
    out = [_line({"header": {"format": FORMAT, "version": VERSION, **header}})]
    first = frame_at[start]
    out.append(_line({"k": start, "io": dict(first["io"]), "frame": first}))
    out += [_line({"k": start, "event": e}) for e in events_at.get(start, [])]
    for k in sorted((set(by_tick) | set(frame_at) | set(events_at)) - {start}):
        if k in frame_at:
            out.append(_line({"k": k, "io": by_tick.get(k, {}), "frame": frame_at[k]}))
        elif k in by_tick:
            out.append(_line({"k": k, "io": by_tick[k]}))
        out += [_line({"k": k, "event": e}) for e in events_at.get(k, [])]
    out.append(_line({"end": {"capture": {"from_tick": start, "tick": end_tick}}}))
    return out


def scene_slug(name: str) -> str:
    """The scene's name as one folder name: letters, digits, ``-`` and ``_``."""
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-")
    return slug[:64] or "scene"


def clean_note(note: str) -> str:
    """The note kept: at most :data:`MAX_NOTE_CHARS`, no control characters but newline and tab."""
    note = note.replace("\r\n", "\n").replace("\r", "\n")
    note = "".join(c for c in note if c in "\n\t" or (ord(c) >= 32 and ord(c) != 127))
    return note[:MAX_NOTE_CHARS].strip()


_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|~>])")


def escape_markdown(text: str) -> str:
    """Text shown as itself in Markdown and HTML: ``&``, ``<`` and ``>`` as entities, and every
    Markdown punctuation character behind a backslash, so a note cannot open a heading, a link,
    a code block or an HTML element."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return _MD_SPECIAL.sub(r"\\\1", text)


def one_line(text: str, limit: int = 200) -> str:
    """Text a browser sent, kept for one line of a report: every control character dropped
    (newline and tab too), at most ``limit`` characters, then escaped (:func:`escape_markdown`)."""
    kept = "".join(c for c in text if ord(c) >= 32 and ord(c) != 127)[:limit]
    return escape_markdown(kept)


def quoted(text: str) -> str:
    """``text`` escaped and quoted line by line (``> ``), or a line saying there is none."""
    if not text:
        return "*(no note)*"
    return "\n".join(f"> {escape_markdown(line)}" if line else ">" for line in text.split("\n"))


def new_folder(base: Path, scene: str, tick: int) -> Path:
    """``base/<scene>/<tick>``, or ``<tick>-2``, ``-3`` ... when that one exists; created."""
    parent = base / scene_slug(scene)
    parent.mkdir(parents=True, exist_ok=True)
    for n in range(1, 1000):
        folder = parent / (str(int(tick)) if n == 1 else f"{int(tick)}-{n}")
        try:
            folder.mkdir()
        except FileExistsError:
            continue
        return folder
    raise RuntimeError(f"too many captures of tick {tick}")


def write_capture(
    base: Path,
    *,
    scene: str,
    snapshot: dict[str, Any],
    image: bytes,
    view: dict[str, Any],
    note: str,
    selected: str | None,
    look_report: str,
    selected_report: str | None,
    piece: list[bytes],
    not_found: str | None = None,
) -> Path:
    """Write the six files of one capture; the folder written. ``not_found``: a selected id the
    browser sent that is not a component of the scene, named in ``report.md`` (one line,
    escaped) and nowhere else."""
    tick, t = int(snapshot["tick"]), float(snapshot["t"])
    folder = new_folder(base, scene, tick)
    (folder / "view.png").write_bytes(image)
    (folder / "view.json").write_text(json.dumps(view, indent=2) + "\n", encoding="utf-8")
    (folder / "snapshot.json").write_text(json.dumps(snapshot) + "\n", encoding="utf-8")
    look = look_report.rstrip("\n") + "\n"
    if selected_report:
        look += "\n" + selected_report.rstrip("\n") + "\n"
    (folder / "look.md").write_text(look, encoding="utf-8")
    with gzip.GzipFile(folder / "last-60s.ofrec", "wb", mtime=0, compresslevel=6) as gz:
        for line in piece:
            gz.write(line)
    first = json.loads(piece[1])["k"]
    seen = view.get("viewer_tick")
    seen = tick if not isinstance(seen, int) else seen
    report = [
        f"# Capture: {escape_markdown(scene)} at t = {t:.2f} s (tick {tick})",
        "",
        "## Note",
        "",
        quoted(clean_note(note)),
        "",
        "## What was captured",
        "",
        f"- Selected component: {selected_line(selected, not_found)}",
        f"- The picture is from tick {seen}; the state is at tick {tick}.",
        "- `view.png`: the picture; `view.json`: the camera, which `?view=` reopens it;",
        f"  `look.md`: the plant in words at tick {tick}; `snapshot.json`: the full state;",
        f"  `last-60s.ofrec`: ticks {first} to {tick} as a recording",
        "  (`openfactory replay last-60s.ofrec`, `openfactory look last-60s.ofrec`).",
        "",
    ]
    (folder / "report.md").write_text("\n".join(report), encoding="utf-8")
    return folder


def selected_line(selected: str | None, not_found: str | None) -> str:
    """``report.md``'s selected component: the id, ``none``, or the one sent that was not found."""
    if selected:
        return escape_markdown(selected)
    if not_found:
        sent = one_line(not_found)
        return f"none: the viewer sent \u201c{sent}\u201d, which was not found in the scene"
    return "none"


def list_captures(base: Path) -> list[Path]:
    """Every capture folder under ``base`` (``<scene>/<tick>``), newest first."""
    if not base.is_dir():
        return []
    found = [
        f
        for scene in base.iterdir()
        if scene.is_dir()
        for f in scene.iterdir()
        if f.is_dir() and (f / "report.md").is_file()
    ]
    return sorted(found, key=lambda f: (f / "report.md").stat().st_mtime_ns, reverse=True)
