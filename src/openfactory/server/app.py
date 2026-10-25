"""Web server: scene API, state and event WebSockets, and the built viewer (PLAN §3.3, task 1.9).

Behavior spec
-------------
:func:`create_app` builds a FastAPI app around one :class:`~openfactory.server.runtime.Runtime`:

- ``GET /api/scene``: the resolved scene, once per viewer: metadata, ``dt``, every component's
  static geometry (``Component.geometry()``: id, type, position, rotation and type-specific
  dimensions) plus its description, the connections, the tag list, and loader warnings.
- ``GET /api/state``: the latest state snapshot (same shape as a ``/ws/state`` frame).
- ``GET /api/status``: sim time and tick, part counts, and viewer events received by type and id.
- ``GET /api/events``: the most recent viewer events with the core time they arrived.
- ``WS /ws/state``: sends the latest snapshot as JSON every 50 ms (20 Hz) on absolute deadlines,
  so slow sends do not lower the rate. The simulation runs at its own 50 Hz; a frame is whatever
  snapshot is current when it is sent, so the viewer interpolates.
- ``WS /ws/events``: receives viewer events as JSON objects (see
  :meth:`Runtime.apply_event`). An invalid event is answered with ``{"error": "..."}`` and the
  socket stays open.
- ``/``: the built viewer from ``web/dist`` (``npm run build``), when present. Without a build,
  ``/`` returns a short page saying how to build it; the API still works.

Run it standalone with ``python -m openfactory.server.app SCENE [--port N]`` (the ``openfactory
run`` CLI of PLAN 1.10 wraps the same pieces together with the Modbus server). It binds the
``[web]`` endpoint from ``openfactory.toml``, ``127.0.0.1:8000`` by default.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from openfactory import __version__
from openfactory.core.config import Endpoint, load_settings
from openfactory.core.loader import load
from openfactory.server.runtime import Runtime

log = logging.getLogger(__name__)

STATE_RATE_HZ = 20.0
WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"

_NO_BUILD = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>OpenFactory</title></head>
<body style="background:#1F2A36;color:#E5E7EB;font-family:sans-serif">
<h1>OpenFactory</h1><p>The viewer is not built. Run <code>npm run build</code> in <code>web/</code>.
The API is up: <a style="color:#60A5FA" href="/api/scene">/api/scene</a>.</p></body></html>"""


def create_app(runtime: Runtime, web_dist: Path | None = WEB_DIST) -> FastAPI:
    app = FastAPI(title="OpenFactory", version=__version__, docs_url="/api/docs")
    app.state.runtime = runtime

    @app.get("/api/scene")
    def scene() -> dict[str, Any]:
        return runtime.scene_document()

    @app.get("/api/state")
    def state() -> dict[str, Any]:
        return runtime.snapshot

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        return runtime.status()

    @app.get("/api/events")
    def events(limit: int = 100) -> list[dict[str, Any]]:
        return list(runtime.events)[-limit:] if limit > 0 else []

    @app.websocket("/ws/state")
    async def ws_state(ws: WebSocket) -> None:
        await ws.accept()
        loop = asyncio.get_running_loop()
        period = 1.0 / STATE_RATE_HZ
        deadline = loop.time()
        try:
            while True:
                await ws.send_text(json.dumps(runtime.snapshot, separators=(",", ":")))
                deadline += period
                delay = deadline - loop.time()
                if delay < -period:  # fell behind (slow client): restart the schedule
                    deadline = loop.time()
                    delay = 0.0
                await asyncio.sleep(max(delay, 0.0))
        except (WebSocketDisconnect, RuntimeError):
            pass  # client went away

    @app.websocket("/ws/events")
    async def ws_events(ws: WebSocket) -> None:
        await ws.accept()
        try:
            while True:
                text = await ws.receive_text()
                try:
                    raw = json.loads(text)
                    if not isinstance(raw, dict):
                        raise ValueError("an event is a JSON object")
                    runtime.apply_event(raw)
                except (ValueError, ValidationError) as exc:
                    await ws.send_text(json.dumps({"error": str(exc)}))
        except WebSocketDisconnect:
            pass

    if web_dist is not None and (web_dist / "index.html").is_file():
        app.mount("/", StaticFiles(directory=web_dist, html=True), name="viewer")
    else:

        @app.get("/", response_class=HTMLResponse)
        def no_build() -> str:
            return _NO_BUILD

    return app


def server_config(app: FastAPI, endpoint: Endpoint) -> uvicorn.Config:
    """uvicorn settings for the viewer server, on the ``websockets`` (sans-I/O) implementation."""
    return uvicorn.Config(
        app, host=endpoint.bind, port=endpoint.port, ws="websockets-sansio", log_level="warning"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m openfactory.server.app")
    parser.add_argument("scene")
    parser.add_argument("--bind", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    web = load_settings().web
    endpoint = Endpoint(bind=args.bind or web.bind, port=args.port or web.port)
    runtime = Runtime(load(args.scene))
    runtime.start()
    log.info("viewer and API on http://%s:%d/", endpoint.bind, endpoint.port)
    try:
        uvicorn.Server(server_config(create_app(runtime), endpoint)).run()
    finally:
        with contextlib.suppress(Exception):
            runtime.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
