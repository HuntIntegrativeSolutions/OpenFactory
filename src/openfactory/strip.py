"""The film strip: motion for the assistant (PLAN 5.14f).

Behavior spec
-------------
``openfactory strip FILE.ofrec --at T --span 2 --frames 6 [--look-at ID] [--labels tags]`` gives
one image of ``frames`` stills in a row, each stamped with its time and tick, and a text table of
what changed between each still and the next: every tag that changed (its value at both stills
and how many times it changed between them) and every part (its carrier at both stills and how
far it moved; a part that appeared or was removed is said so). The input may be a capture folder
(PLAN 5.14e), whose ``last-60s.ofrec`` is read, so "what I just saw" is one command.

Times: the stills are spread evenly over ``span`` seconds centred on ``T`` (the recording's end
without ``--at``); a window that runs past either end of the recording is moved inside it, and
one longer than the recording is the whole recording. Each still is the recording's frame at or
before its time, so it shows what the recording holds, never an in-between pose.

How: the recording is served in-process, paused (:class:`openfactory.record.ReplayRuntime`, the
app ``openfactory replay`` serves, on a free loopback port), and the viewer is driven in headless
Chromium as ``tools/screenshot.py`` does: for each still the replay seeks to the frame, the
viewer is waited on until it shows that tick with every part at rest (never a fixed sleep), and
the viewer draws the still onto a canvas (the plant, its labels and its status lines, as a
capture's picture). The stills are joined into one image in the page, on a canvas, so no image
library is added (ruling 5). Playwright and its Chromium are needed (the dev tools; the command
says so when they are missing).
"""

from __future__ import annotations

import base64
import bisect
import contextlib
import math
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from openfactory.record import Recording, read_recording

CHROMIUM_ARGS = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"]
#: The gap between two stills and the height of the band their stamp is written in, pixels.
GAP_PX = 4
STAMP_PX = 22


def recording_of(source: Path) -> Path:
    """The recording a strip reads: the file, or a capture folder's ``last-60s.ofrec``."""
    if source.is_dir():
        inside = source / "last-60s.ofrec"
        if not inside.is_file():
            raise ValueError(f"{source}: a folder, but not a capture (no last-60s.ofrec)")
        return inside
    return source


def strip_ticks(rec: Recording, at_s: float | None, span_s: float, frames: int) -> list[int]:
    """The recording's frame ticks of the ``frames`` stills (see the module docstring)."""
    if frames < 2:
        raise ValueError("a strip needs at least 2 frames")
    if span_s <= 0:
        raise ValueError("--span must be positive")
    ticks = [k for k, _ in rec.frames]
    first, last = ticks[0] * rec.dt, ticks[-1] * rec.dt
    centre = last if at_s is None else at_s
    span = min(span_s, last - first)
    start = min(max(centre - span / 2, first), last - span)
    out = []
    for i in range(frames):
        t = start + span * i / (frames - 1)
        j = bisect.bisect_right(ticks, math.floor(t / rec.dt + 1e-6)) - 1
        out.append(ticks[max(j, 0)])
    return out


def _fmt(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".") if value % 1 else str(int(value))
    return str(value)


def change_table(rec: Recording, ticks: list[int]) -> str:
    """What changed between each still and the next, as Markdown."""
    frames = dict(rec.frames)
    lines = [
        f"# Strip of {rec.header['scene']['scene']['name']}: "
        f"{len(ticks)} stills, t = {ticks[0] * rec.dt:.2f} to {ticks[-1] * rec.dt:.2f} s",
        "",
        "| Still | t (s) | Tick |",
        "|---|---|---|",
    ]
    lines += [f"| {i + 1} | {k * rec.dt:.2f} | {k} |" for i, k in enumerate(ticks)]
    for i, (a, b) in enumerate(zip(ticks, ticks[1:], strict=False)):
        fa, fb = frames[a], frames[b]
        lines += [
            "",
            f"## Still {i + 1} to {i + 2} (t = {a * rec.dt:.2f} to {b * rec.dt:.2f} s)",
            "",
        ]
        counts: dict[str, int] = {}
        for k, items in rec.io:
            if a < k <= b:
                for tag in items:
                    counts[tag] = counts.get(tag, 0) + 1
        tags = [
            f"- `{tag}` {_fmt(fa['io'].get(tag))} → {_fmt(fb['io'].get(tag))}"
            + (f" ({n} changes)" if n > 1 else "")
            for tag, n in sorted(counts.items())
        ]
        lines += ["Tags:", *(tags or ["- no change"]), ""]
        pa = {p["id"]: p for p in fa["parts"]}
        pb = {p["id"]: p for p in fb["parts"]}
        parts = []
        for pid in sorted(set(pa) | set(pb)):
            if pid not in pa:
                p = pb[pid]
                parts.append(f"- {pid} ({p['name']}) appeared, on {p['carrier'] or 'nothing'}")
            elif pid not in pb:
                parts.append(
                    f"- {pid} ({pa[pid]['name']}) removed, was on {pa[pid]['carrier'] or 'nothing'}"
                )
            else:
                moved = math.dist(pa[pid]["position"], pb[pid]["position"])
                ca, cb = pa[pid]["carrier"] or "nothing", pb[pid]["carrier"] or "nothing"
                where = f"on {ca}" if ca == cb else f"{ca} → {cb}"
                parts.append(f"- {pid} ({pb[pid]['name']}) {where}, moved {moved:.3f} m")
        lines += ["Parts:", *(parts or ["- none"])]
    return "\n".join(lines) + "\n"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def serve_paused(rec: Recording) -> Iterator[tuple[Any, int]]:
    """The recording served paused on a free loopback port: ``(runtime, port)``."""
    import uvicorn

    from openfactory.core.config import Endpoint
    from openfactory.record import ReplayRuntime
    from openfactory.server.app import create_app, server_config

    runtime = ReplayRuntime(rec, paused=True)
    port = _free_port()
    server = uvicorn.Server(
        server_config(create_app(runtime, bind="127.0.0.1"), Endpoint(bind="127.0.0.1", port=port))
    )
    thread = threading.Thread(target=server.run, name="openfactory-strip-server", daemon=True)
    thread.start()
    runtime.start()
    deadline = time.monotonic() + 30
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            raise RuntimeError("the strip's replay server did not start")
        time.sleep(0.05)
    try:
        yield runtime, port
    finally:
        server.should_exit = True
        thread.join(10)
        runtime.stop()


_JOIN = """async ([stills, stamps, w, h, gap, band, bg, fg]) => {
  const canvas = document.createElement("canvas");
  canvas.width = stills.length * w + (stills.length - 1) * gap;
  canvas.height = h + band;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  for (let i = 0; i < stills.length; i++) {
    const img = new Image();
    img.src = stills[i];
    await img.decode();
    const x = i * (w + gap);
    ctx.drawImage(img, x, band, w, h);
    ctx.fillStyle = fg;
    ctx.font = "13px Arial, sans-serif";
    ctx.textBaseline = "middle";
    ctx.fillText(stamps[i], x + 6, band / 2);
  }
  return canvas.toDataURL("image/png");
}"""


def make_strip(
    source: Path,
    *,
    out: Path,
    at_s: float | None = None,
    span_s: float = 2.0,
    frames: int = 6,
    look_at: str | None = None,
    labels: str = "ids",
    size: tuple[int, int] = (960, 540),
    probes: list[tuple[str, tuple[float, float, float]]] | None = None,
    timeout_s: float = 60.0,
) -> dict[str, Any]:
    """Write the strip image to ``out`` and its table beside it (``.md``); returns the ticks,
    the table, each still's place in the image, and the screen point of each probe (a
    component id and a point in its own frame) in each still, in the still's pixels."""
    rec = read_recording(recording_of(source))
    ticks = strip_ticks(rec, at_s, span_s, frames)
    table = change_table(rec, ticks)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError(
            "openfactory strip needs Playwright and its Chromium: install the dev tools "
            "(`uv sync`) and run `uv run playwright install chromium`"
        ) from None
    from urllib.parse import urlencode

    query = {"quality": "low", "panels": "off", "labels": labels}
    if look_at:
        query["look"] = look_at
    w, h = size
    still_w, still_h = w // 2, h // 2
    stills: list[str] = []
    stamps: list[str] = []
    points: list[list[list[float] | None]] = []
    with serve_paused(rec) as (runtime, port), sync_playwright() as pw:
        browser = pw.chromium.launch(args=CHROMIUM_ARGS)
        page = browser.new_page(viewport={"width": w, "height": h})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{port}/?{urlencode(query)}")
        page.wait_for_function(
            "() => { const v = window.__openfactory; return v && (v.error || v.ready); }",
            timeout=timeout_s * 1000,
        )
        if page.evaluate("() => window.__openfactory.error"):
            raise RuntimeError(f"viewer error: {page.evaluate('() => window.__openfactory.error')}")
        for tick in ticks:
            runtime.seek(tick)
            page.wait_for_function(
                "t => window.__openfactory.tick === t && window.__openfactoryPartsSettled()",
                arg=tick,
                timeout=timeout_s * 1000,
            )
            stills.append(page.evaluate("() => window.__openfactoryGrab()"))
            stamps.append(f"t = {tick * rec.dt:.2f} s   tick {tick}")
            points.append(
                [
                    page.evaluate(
                        "([id, p]) => window.__openfactoryScreenOf(id, p)", [cid, list(p)]
                    )
                    for cid, p in (probes or [])
                ]
            )
        joined = page.evaluate(
            _JOIN, [stills, stamps, still_w, still_h, GAP_PX, STAMP_PX, "#1F2A36", "#E5E7EB"]
        )
        browser.close()
        if errors:
            raise RuntimeError("viewer error: " + "; ".join(errors))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(base64.b64decode(joined.split(",", 1)[1]))
    out.with_suffix(".md").write_text(table, encoding="utf-8")
    scale = still_w / w
    return {
        "ticks": ticks,
        "table": table,
        "stills": [
            {
                "tick": k,
                "x": i * (still_w + GAP_PX),
                "y": STAMP_PX,
                "width": still_w,
                "height": still_h,
            }
            for i, k in enumerate(ticks)
        ],  # fmt: skip
        "probes": [
            [None if p is None else [p[0] * scale, p[1] * scale] for p in still] for still in points
        ],
    }
