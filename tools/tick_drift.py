"""Measure how far the real-time tick loop falls behind with viewers connected (PLAN 2.9).

Usage::

    uv run python tools/tick_drift.py scenes/sorting-by-height.yaml \\
        --script examples/pyplc/sorting-by-height.py --seconds 30 --viewers 0
    uv run python tools/tick_drift.py ... --viewers 3          # 3 WebSocket clients on /ws/state
    uv run python tools/tick_drift.py ... --browsers 1         # the real viewer, headless Chromium

The scene runs in real time (50 Hz) behind the normal web app, as ``openfactory run`` serves it.
Every 100 ms of wall time the tool samples how far the simulation is behind the wall clock,
``behind = wall seconds since start - simulated seconds``, and it times every tick's work on the
simulation thread. It prints one line of numbers (JSON with ``--json``):

- ``behind_max_s`` / ``behind_end_s``: the worst and the final lag of simulated time;
- ``dropped``: ticks the scheduler gave up on (more than 10 behind), each one simulated time
  lost for good;
- ``max_lateness_ms``: the latest any tick started after its deadline (the scheduler's own);
- ``step_ms_mean`` / ``step_ms_p99`` / ``step_ms_max``: the tick's own work on the simulation
  thread (scan, step, snapshot, recorder hand-off);
- ``frames``: state frames the WebSocket clients received.

Run the baseline and the candidate in the same session on the same machine (PLAN 2.9, "Measure
against a same-session baseline"); absolute numbers vary from day to day.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
import statistics
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def measure(
    scene: Path,
    *,
    script: Path | None,
    seconds: float,
    viewers: int,
    browsers: int,
    forces: list[str],
) -> dict:
    import uvicorn
    import websockets

    from openfactory.core.config import Endpoint
    from openfactory.core.iotable import Writer
    from openfactory.core.loader import load
    from openfactory.server.app import create_app, server_config
    from openfactory.server.runtime import Runtime

    loaded = load(scene)
    for item in forces:
        name, value = item.split("=", 1)
        loaded.io.set(name, value not in ("0", "false"), Writer.PLC)
    controller = None
    if script is not None:
        from openfactory.pyplc import ScriptPlc

        controller = ScriptPlc(script, loaded.io)
    runtime = Runtime(loaded, controller=controller)
    step_ms: list[float] = []
    cpu_ms: list[float] = []
    inner = runtime._step

    def timed(dt: float) -> None:
        t0, c0 = time.perf_counter(), time.thread_time()
        inner(dt)
        step_ms.append((time.perf_counter() - t0) * 1000)
        cpu_ms.append((time.thread_time() - c0) * 1000)

    runtime.scheduler.step = timed
    port = free_port()
    web = uvicorn.Server(
        server_config(create_app(runtime, web_dist=REPO / "web" / "dist"), Endpoint(port=port))
    )
    web_thread = threading.Thread(target=web.run, daemon=True)
    web_thread.start()
    while not web.started:
        time.sleep(0.02)

    frames = [0]
    stop = threading.Event()

    async def client() -> None:
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws/state", max_size=None) as ws:
            while not stop.is_set():
                await asyncio.wait_for(ws.recv(), 5)
                frames[0] += 1

    def clients() -> None:
        async def main() -> None:
            await asyncio.gather(*(client() for _ in range(viewers)))

        asyncio.run(main())

    threads = []
    if viewers:
        threads.append(threading.Thread(target=clients, daemon=True))
    pw = browser_ctx = None
    pages = []
    if browsers:
        from playwright.sync_api import sync_playwright
        from screenshot import CHROMIUM_ARGS  # the CI screenshot runner's software-GL flags

        pw = sync_playwright().start()
        browser_ctx = pw.chromium.launch(args=CHROMIUM_ARGS)
        for _ in range(browsers):  # one tab per viewer, each running its own Rapier world
            page = browser_ctx.new_page(viewport={"width": 1280, "height": 720})
            page.goto(f"http://127.0.0.1:{port}/")
            page.wait_for_function("() => window.__openfactory?.ready", timeout=60_000)
            pages.append(page)

    samples: list[float] = []
    proc0 = time.process_time()
    runtime.start(seconds)
    start = time.perf_counter()
    for t in threads:
        t.start()
    while runtime.running:
        time.sleep(0.1)
        samples.append((time.perf_counter() - start) - runtime.snapshot["t"])
    stop.set()
    proc_cpu_s = time.process_time() - proc0
    stats = runtime.stats
    if browser_ctx is not None:
        browser_ctx.close()
        pw.stop()
    web.should_exit = True
    web_thread.join(5)
    return {
        "scene": scene.name,
        "viewers": viewers,
        "browsers": browsers,
        "seconds": seconds,
        "ticks": stats.ticks if stats else 0,
        "dropped": stats.dropped if stats else 0,
        "max_lateness_ms": round((stats.max_lateness if stats else 0) * 1000, 1),
        "behind_max_s": round(max(samples, default=0.0), 3),
        "behind_end_s": round(samples[-1] if samples else 0.0, 3),
        "step_ms_mean": round(statistics.fmean(step_ms), 2) if step_ms else 0.0,
        "step_ms_p99": round(sorted(step_ms)[int(len(step_ms) * 0.99) - 1], 2) if step_ms else 0.0,
        "step_ms_max": round(max(step_ms, default=0.0), 2),
        # the tick's CPU time on its own thread: wall time well above it means the thread was
        # waiting (for the GIL, the step lock, or a CPU core)
        "step_cpu_ms_mean": round(statistics.fmean(cpu_ms), 2) if cpu_ms else 0.0,
        # CPU used by this process's other threads (web server, clients) during the run
        "other_threads_cpu_s": round(proc_cpu_s - sum(cpu_ms) / 1000, 2),
        "frames": frames[0],
        "parts_end": len(runtime.snapshot["parts"]),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("scene", type=Path)
    parser.add_argument("--script", type=Path)
    parser.add_argument("--seconds", type=float, default=30.0)
    parser.add_argument("--viewers", type=int, default=0, help="WebSocket clients on /ws/state")
    parser.add_argument(
        "--browsers", type=int, default=0, help="real viewer tabs (headless Chromium, software GL)"
    )
    parser.add_argument("--force", action="append", default=[], help="TAG=VALUE, repeatable")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = measure(
        args.scene, script=args.script, seconds=args.seconds, viewers=args.viewers,
        browsers=args.browsers, forces=args.force,
    )  # fmt: skip
    print(json.dumps(result) if args.json else "  ".join(f"{k}={v}" for k, v in result.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
