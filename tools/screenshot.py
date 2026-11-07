"""Screenshot runner (PLAN task 1.13): how the implementing session verifies visual work.

Usage::

    uv run python tools/screenshot.py SCENE [--seconds N] [--out PNG] [--force TAG=VALUE ...]
                                            [--script F.py | --bundled-script]
                                            [--size 1280x720] [--build]

What it does:

1. Optionally runs ``npm run build`` in ``web/`` (``--build``); otherwise it needs ``web/dist``.
2. Starts ``openfactory run SCENE`` on two free loopback ports (Modbus and viewer), passing any
   ``--force TAG=VALUE`` and ``--script F.py`` (a stand-in PLC, PLAN 2.10) through, so a scene can
   be shown running without a PLC. SCENE may be a ``.ofz`` bundle (PLAN 2.18); ``--bundled-script``
   runs the script it carries.
3. Opens the viewer in Playwright's headless Chromium with SwiftShader WebGL and waits until the
   viewer reports ``window.__openfactory.ready``, a simulation time of at least ``--seconds``, and
   (with ``--min-sensor-events N``) at least N photo-eye events sent from its Rapier physics to the
   core (it waits on the viewer's own flags, never on a fixed sleep). With ``--min-meshes N`` it
   also requires N custom meshes drawn and none failed (PLAN 2.15; the viewer settles every mesh
   before its first frame, so ``ready`` already implies they have loaded or failed). With
   ``--housings TYPE,...`` it requires each listed component type to have drawn its default
   model (PLAN 4.2, ``window.__openfactory.housings``) and no default model to have failed, and
   ``--max-draw-calls N`` fails if the last frame took more than N draw calls (the default models
   are merged by material so the sorter stays under 45, ruled 2026-11-07). It then
   prints the viewer's physics backend and the core's ``/api/status`` counts of events received.
   It also prints how far the viewer has drawn any part the core carries on a conveyor from
   upright (``window.__openfactory.maxTiltDeg``: the core holds such parts upright, so a tilt is
   the viewer departing from it; PLAN 2.9), and ``--max-tilt-deg N`` fails above N degrees.
   Likewise it prints how far outside its carrier's deck the viewer has drawn such a part
   (``window.__openfactory.maxOffDeckM``, the position check: the core keeps it on the deck), and
   ``--max-off-deck-m M`` fails above M metres.
4. Saves a PNG (default ``tools/screenshots/<scene>-<N>s.png``, git-ignored) and checks it is not
   blank: at least 2 % of pixels must differ from the ISA-101 background ``#1F2A36`` and at most
   90 % may be near-black. A failed check, a viewer error, or a timeout exits 1.

The PNG check decodes the file with the standard library (``zlib``), so the tool needs nothing
beyond Playwright. Look at the PNG yourself as well: the check catches blank or black frames, not
wrong ones.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import socket
import struct
import subprocess
import sys
import time
import zlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
WEB = REPO / "web"
DEFAULT_DIR = REPO / "tools" / "screenshots"
BACKGROUND = (0x1F, 0x2A, 0x36)
CHROMIUM_ARGS = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"]
MIN_FOREGROUND = 0.02
MAX_BLACK = 0.90


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --- PNG check -----------------------------------------------------------------------------------


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    return a if pa <= pb and pa <= pc else (b if pb <= pc else c)


def read_png(path: Path) -> tuple[int, int, int, bytes]:
    """Decode an 8-bit, non-interlaced RGB or RGBA PNG: (width, height, channels, pixels)."""
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path} is not a PNG")
    pos, idat, header = 8, b"", None
    while pos < len(data):
        length, kind = struct.unpack(">I4s", data[pos : pos + 8])
        body = data[pos + 8 : pos + 8 + length]
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat += body
        pos += 12 + length
    if header is None:
        raise ValueError(f"{path}: no IHDR")
    width, height, depth, color, _, _, interlace = header
    channels = {2: 3, 6: 4}.get(color)
    if depth != 8 or channels is None or interlace:
        raise ValueError(f"{path}: unsupported PNG (depth {depth}, color type {color})")
    raw = zlib.decompress(idat)
    stride = width * channels
    out = bytearray(height * stride)
    prev = bytearray(stride)
    for y in range(height):
        kind = raw[y * (stride + 1)]
        line = bytearray(raw[y * (stride + 1) + 1 : (y + 1) * (stride + 1)])
        for x in range(stride):
            a = line[x - channels] if x >= channels else 0
            b = prev[x]
            c = prev[x - channels] if x >= channels else 0
            if kind == 1:
                line[x] = (line[x] + a) & 0xFF
            elif kind == 2:
                line[x] = (line[x] + b) & 0xFF
            elif kind == 3:
                line[x] = (line[x] + (a + b) // 2) & 0xFF
            elif kind == 4:
                line[x] = (line[x] + _paeth(a, b, c)) & 0xFF
        out[y * stride : (y + 1) * stride] = line
        prev = line
    return width, height, channels, bytes(out)


def image_stats(path: Path) -> dict[str, float]:
    """Fractions of pixels that differ from the background and that are near-black."""
    width, height, channels, px = read_png(path)
    total = width * height
    foreground = black = 0
    colors: set[tuple[int, int, int]] = set()
    for i in range(0, len(px), channels):
        r, g, b = px[i], px[i + 1], px[i + 2]
        if abs(r - BACKGROUND[0]) + abs(g - BACKGROUND[1]) + abs(b - BACKGROUND[2]) > 24:
            foreground += 1
        if r + g + b < 24:
            black += 1
        if len(colors) < 4096:
            colors.add((r >> 3, g >> 3, b >> 3))
    return {
        "width": width,
        "height": height,
        "foreground": foreground / total,
        "black": black / total,
        "colors": len(colors),
    }


def check_image(path: Path) -> tuple[bool, str]:
    s = image_stats(path)
    ok = s["foreground"] >= MIN_FOREGROUND and s["black"] <= MAX_BLACK
    verdict = "OK" if ok else "BLANK"
    return ok, (
        f"{verdict}: {s['width']}x{s['height']}, {s['foreground']:.1%} non-background, "
        f"{s['black']:.1%} near-black, {s['colors']} colour bins"
    )


# --- runner --------------------------------------------------------------------------------------


def build_viewer() -> None:
    subprocess.run(["npm", "run", "build"], cwd=WEB, check=True)


def wait_for_api(port: int, proc: subprocess.Popen[str], timeout: float = 30.0) -> None:
    import urllib.request

    deadline = time.monotonic() + timeout
    while True:
        if proc.poll() is not None:
            raise RuntimeError(f"openfactory run exited with {proc.returncode}")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=1):
                return
        except OSError:
            if time.monotonic() > deadline:
                raise TimeoutError("viewer server did not start") from None
            time.sleep(0.1)


@contextlib.contextmanager
def running_viewer(
    scene: Path,
    *,
    forces: list[str],
    size: tuple[int, int] = (1280, 720),
    script: Path | None = None,
    bundled_script: bool = False,
) -> Iterator[tuple[Any, int, list[str]]]:
    """Start ``openfactory run SCENE`` on free loopback ports and open its viewer in headless
    Chromium (SwiftShader WebGL). Yields ``(page, web_port, console)``; stops both on exit.
    Shared with ``tools/parity_trace.py --live``."""
    from playwright.sync_api import sync_playwright

    if not (WEB / "dist" / "index.html").is_file():
        raise FileNotFoundError("web/dist is missing: run with --build or `npm run build` in web/")
    web_port, modbus_port = free_port(), free_port()
    cmd = [
        sys.executable, "-m", "openfactory", "run", str(scene), "-q",
        "--port", str(modbus_port), "--web-port", str(web_port),
    ]  # fmt: skip
    for force in forces:
        cmd += ["--force", force]
    if script is not None:
        cmd += ["--script", str(script)]
    if bundled_script:
        cmd.append("--bundled-script")
    proc = subprocess.Popen(
        cmd, cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    try:
        wait_for_api(web_port, proc)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=CHROMIUM_ARGS)
            page = browser.new_page(viewport={"width": size[0], "height": size[1]})
            console: list[str] = []
            page.on("console", lambda m: console.append(f"{m.type}: {m.text}"))
            page.on("pageerror", lambda e: console.append(f"pageerror: {e}"))
            page.goto(f"http://127.0.0.1:{web_port}/")
            yield page, web_port, console
            browser.close()
    finally:
        proc.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(10)
        if proc.poll() is None:
            proc.kill()


def wait_for_viewer(
    page: Any, console: list[str], *, seconds: float, min_sensor_events: int = 0, timeout: float
) -> dict[str, Any]:
    """Wait until the viewer is ready at sim time >= ``seconds`` with ``min_sensor_events`` sent;
    return ``window.__openfactory``. Raises on a viewer error or timeout."""
    try:
        page.wait_for_function(
            "([s, n]) => { const v = window.__openfactory; return v && (v.error || "
            "(v.ready && v.simTime >= s && (v.sensorEvents || 0) >= n)); }",
            arg=[seconds, min_sensor_events],
            timeout=timeout * 1000,
        )
    except Exception as exc:
        raise TimeoutError(f"viewer not ready: {exc}\n" + "\n".join(console)) from None
    status = page.evaluate("() => window.__openfactory")
    if status.get("error"):
        raise RuntimeError(f"viewer error: {status['error']}\n" + "\n".join(console))
    return status


def get_json(web_port: int, path: str) -> Any:
    import json
    import urllib.request

    with urllib.request.urlopen(f"http://127.0.0.1:{web_port}{path}", timeout=5) as res:
        return json.load(res)


def screenshot(
    scene: Path,
    *,
    seconds: float,
    out: Path,
    forces: list[str],
    size: tuple[int, int],
    min_sensor_events: int = 0,
    timeout: float = 60.0,
    script: Path | None = None,
    min_meshes: int = 0,
    housings: tuple[str, ...] = (),
    bundled_script: bool = False,
) -> dict[str, object]:
    with running_viewer(
        scene, forces=forces, size=size, script=script, bundled_script=bundled_script
    ) as (
        page,
        web_port,
        console,
    ):
        status = wait_for_viewer(
            page, console, seconds=seconds, min_sensor_events=min_sensor_events, timeout=timeout
        )
        meshes = status.get("meshes") or {"loaded": 0, "failed": 0}
        if meshes["loaded"] < min_meshes or (min_meshes and meshes["failed"]):
            raise RuntimeError(
                f"custom meshes: {meshes['loaded']} drawn, {meshes['failed']} failed, "
                f"{min_meshes} wanted\n" + "\n".join(console)
            )
        drawn = status.get("housings") or {"loaded": 0, "failed": 0, "types": []}
        missing = sorted(set(housings) - set(drawn["types"]))
        if housings and (missing or drawn["failed"]):
            raise RuntimeError(
                f"default models: {drawn['loaded']} drawn, {drawn['failed']} failed, none for "
                f"{', '.join(missing) or '-'}\n" + "\n".join(console)
            )
        page.wait_for_timeout(200)  # let the last frame paint
        out.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(out))
        core = get_json(web_port, "/api/status")
    return {"status": status, "console": console, "core": core}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("scene", type=Path)
    parser.add_argument("--seconds", type=float, default=3.0, help="simulated seconds to wait")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--force", action="append", default=[], metavar="TAG=VALUE")
    parser.add_argument("--script", type=Path, default=None, help="stand-in PLC (run --script)")
    parser.add_argument(
        "--bundled-script", action="store_true", help="a .ofz scene's own script (PLAN 2.18)"
    )
    parser.add_argument("--size", default="1280x720", help="viewport WIDTHxHEIGHT")
    parser.add_argument("--build", action="store_true", help="run npm run build first")
    parser.add_argument(
        "--max-tilt-deg",
        type=float,
        default=None,
        metavar="N",
        help="fail if the viewer drew a part on a conveyor more than N degrees from upright",
    )
    parser.add_argument(
        "--max-off-deck-m",
        type=float,
        default=None,
        metavar="M",
        help="fail if the viewer drew a part the core carries more than M m outside its deck",
    )
    parser.add_argument(
        "--min-sensor-events",
        type=int,
        default=0,
        metavar="N",
        help="also wait until the viewer physics has sent N photo-eye events to the core",
    )
    parser.add_argument(
        "--min-meshes",
        type=int,
        default=0,
        metavar="N",
        help="fail unless the viewer drew N custom meshes and none failed (PLAN 2.15)",
    )
    parser.add_argument(
        "--max-draw-calls",
        type=int,
        default=None,
        metavar="N",
        help="fail if the viewer's last frame took more than N draw calls (PLAN 4.2)",
    )
    parser.add_argument(
        "--housings",
        default="",
        metavar="TYPE,...",
        help="fail unless each listed type drew its default model and none failed (PLAN 4.2)",
    )
    args = parser.parse_args(argv)
    width, height = (int(v) for v in args.size.lower().split("x"))
    out = args.out or DEFAULT_DIR / f"{args.scene.stem}-{args.seconds:g}s.png"
    if args.build:
        build_viewer()
    result = screenshot(
        args.scene.resolve(),
        seconds=args.seconds,
        out=out,
        forces=args.force,
        size=(width, height),
        min_sensor_events=args.min_sensor_events,
        min_meshes=args.min_meshes,
        housings=tuple(t for t in args.housings.split(",") if t),
        script=args.script.resolve() if args.script else None,
        bundled_script=args.bundled_script,
    )
    ok, summary = check_image(out)
    status = result["status"]
    print(f"wrote {out}")
    print(
        f"viewer: scene {status['scene']}, {status['components']} components, "
        f"{status['frames']} frames, t = {status['simTime']:.2f} s"
    )
    print(
        f"viewer physics: {status.get('physics')}, "
        f"sensor events sent: {status.get('sensorEvents')}, "
        f"steps {status.get('physicsSteps')}, ticks dropped {status.get('droppedTicks')}"
    )
    meshes = status.get("meshes") or {}
    if meshes.get("loaded") or meshes.get("failed"):
        print(f"custom meshes: {meshes['loaded']} drawn, {meshes['failed']} failed")
    drawn = status.get("housings") or {}
    if drawn.get("loaded") or drawn.get("failed"):
        types = ", ".join(drawn["types"]) or "-"
        print(f"default models: {drawn['loaded']} drawn ({types}), {drawn['failed']} failed")
    calls = int(status.get("drawCalls") or 0)
    print(f"draw calls in the last frame: {calls}")
    if args.max_draw_calls is not None and calls > args.max_draw_calls:
        print(f"draw-call check failed: {calls} > {args.max_draw_calls}")
        ok = False
    core = result["core"]
    print(f"core /api/status: t = {core['t']:.2f} s, events received {core['events']}")  # type: ignore[index]
    tilt, worst = float(status.get("maxTiltDeg") or 0.0), status.get("tilt")
    where = f" ({worst['part']} at tick {worst['tick']})" if worst else ""
    tilt_line = f"carried parts drawn at most {tilt:.2f} degrees from upright{where}"
    print(tilt_line)
    if args.max_tilt_deg is not None and tilt > args.max_tilt_deg:
        print(f"tilt check failed: {tilt:.2f} > {args.max_tilt_deg:g} degrees")
        ok = False
    off, worst = float(status.get("maxOffDeckM") or 0.0), status.get("offDeck")
    where = f" ({worst['part']} off {worst['carrier']} at tick {worst['tick']})" if worst else ""
    off_line = f"carried parts drawn at most {off * 1000:.1f} mm outside their deck{where}"
    print(off_line)
    if args.max_off_deck_m is not None and off > args.max_off_deck_m:
        print(f"position check failed: {off:.3f} > {args.max_off_deck_m:g} m")
        ok = False
    if os.environ.get("GITHUB_ACTIONS") == "true":  # readable without signing in, as bench.py
        level = "notice" if ok else "error"
        frames = (
            f"{status['frames']} frames, {status.get('droppedTicks')} ticks dropped, "
            f"{calls} draw calls"
        )
        print(f"::{level} title=screenshot {args.scene.name}::{tilt_line}; {off_line}; {frames}")
    print(summary)
    for line in result["console"]:  # type: ignore[union-attr]
        if line.startswith(("error", "pageerror", "warning")):
            print(f"browser {line}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
