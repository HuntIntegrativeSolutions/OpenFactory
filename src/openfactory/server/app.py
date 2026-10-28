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
- ``GET /api/fault``: active faults (``{id, type, kind, params, tick}``) and each component's
  supported kinds (PLAN 2.6). ``POST /api/fault`` with ``{"id", "kind", "params"}`` injects one,
  replacing that component's current fault, and returns it with the one it replaced;
  ``DELETE /api/fault/{id}`` clears it. Unknown components are 404, unsupported kinds or bad
  parameters 400. Faults act from the next tick. Access (PLAN §6.4, ruled 2026-10-27): open when
  the server is bound to a loopback address; bound beyond loopback, every ``/api/fault`` request
  needs the configured token in the :data:`FAULT_TOKEN_HEADER` header (401 without it or with a
  wrong one), and with no token configured the fault API is off (403).
- ``GET /api/stats``: the run's statistics so far (PLAN 2.11, :mod:`openfactory.core.stats`):
  throughput per remover, conveyor utilization, actuator cycles, part dwell, and losses.
- ``GET /api/parity``: the core's raw photo-eye beam edges with their ticks next to the browser's
  sensor events (:meth:`Runtime.parity`), for ``tools/parity_trace.py --live``.
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
import hmac
import ipaddress
import json
import logging
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from openfactory import __version__
from openfactory.core.config import Endpoint, load_settings
from openfactory.core.loader import load
from openfactory.server.runtime import Runtime

log = logging.getLogger(__name__)

STATE_RATE_HZ = 20.0
#: Request header carrying the fault token when the server is bound beyond loopback (PLAN §6.4).
FAULT_TOKEN_HEADER = "X-OpenFactory-Fault-Token"


def is_loopback(bind: str) -> bool:
    """True for ``localhost`` and loopback addresses (``127.0.0.0/8``, ``::1``)."""
    if bind == "localhost":
        return True
    try:
        return ipaddress.ip_address(bind).is_loopback
    except ValueError:
        return False  # a host name other than localhost: assume it reaches the network


def fault_api_notice(bind: str, token: str | None) -> str | None:
    """The one-line notice ``run`` prints when the fault API is off, else None."""
    if is_loopback(bind) or token:
        return None
    return (
        f"fault API off: the viewer is bound to {bind}, beyond loopback, and no fault token is set "
        "(--fault-token or [server] fault_token in openfactory.local.toml)"
    )


class FaultRequest(BaseModel):
    """Body of ``POST /api/fault`` (PLAN 2.6)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: str
    params: dict[str, Any] = Field(default_factory=dict)


WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"

_NO_BUILD = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>OpenFactory</title></head>
<body style="background:#1F2A36;color:#E5E7EB;font-family:sans-serif">
<h1>OpenFactory</h1><p>The viewer is not built. Run <code>npm run build</code> in <code>web/</code>.
The API is up: <a style="color:#60A5FA" href="/api/scene">/api/scene</a>.</p></body></html>"""


def create_app(
    runtime: Runtime,
    web_dist: Path | None = WEB_DIST,
    *,
    bind: str = "127.0.0.1",
    fault_token: str | None = None,
) -> FastAPI:
    """The app for one runtime. ``bind`` is the address the server listens on; it decides
    whether ``/api/fault`` is open (loopback) or needs ``fault_token`` (PLAN §6.4)."""
    app = FastAPI(title="OpenFactory", version=__version__, docs_url="/api/docs")
    app.state.runtime = runtime
    faults_open = is_loopback(bind)
    expected = fault_token.encode() if fault_token else None

    def fault_access(
        token: str | None = Header(default=None, alias=FAULT_TOKEN_HEADER),
    ) -> None:
        if faults_open:
            return
        if expected is None:
            raise HTTPException(
                403,
                "fault API off: this server is bound beyond loopback and has no fault token",
            )
        if token is None or not hmac.compare_digest(token.encode(), expected):
            raise HTTPException(401, f"fault API: missing or wrong {FAULT_TOKEN_HEADER}")

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

    guarded = [Depends(fault_access)]

    @app.get("/api/fault", dependencies=guarded)
    def faults() -> dict[str, Any]:
        return runtime.faults()

    @app.post("/api/fault", dependencies=guarded)
    def inject_fault(request: FaultRequest) -> dict[str, Any]:
        try:
            return runtime.inject_fault(request.id, request.kind, request.params)
        except KeyError as exc:
            raise HTTPException(404, str(exc.args[0])) from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.delete("/api/fault/{component_id}", dependencies=guarded)
    def clear_fault(component_id: str) -> dict[str, Any]:
        try:
            return runtime.clear_fault(component_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc.args[0])) from None

    @app.get("/api/stats")
    def stats() -> dict[str, Any]:
        return runtime.statistics()

    @app.get("/api/parity")
    def parity() -> dict[str, Any]:
        return runtime.parity()

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
    settings = load_settings()
    web = settings.web
    endpoint = Endpoint(bind=args.bind or web.bind, port=args.port or web.port)
    token = settings.server.fault_token
    runtime = Runtime(load(args.scene))
    runtime.start()
    log.info("viewer and API on http://%s:%d/", endpoint.bind, endpoint.port)
    if notice := fault_api_notice(endpoint.bind, token):
        log.warning(notice)
    app = create_app(runtime, bind=endpoint.bind, fault_token=token)
    try:
        uvicorn.Server(server_config(app, endpoint)).run()
    finally:
        with contextlib.suppress(Exception):
            runtime.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
