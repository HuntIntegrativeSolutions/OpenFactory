"""Web server: scene API, state and event WebSockets, and the built viewer (PLAN §3.3, task 1.9).

Behavior spec
-------------
:func:`create_app` builds a FastAPI app around one :class:`~openfactory.server.runtime.Runtime`:

- ``GET /api/scene``: the resolved scene, once per viewer: metadata, ``dt``, every component's
  static geometry (``Component.geometry()``: id, type, position, rotation and type-specific
  dimensions) plus its description, the connections, the tag list, and loader warnings.
- ``GET /api/state``: the latest state snapshot (same shape as a ``/ws/state`` frame).
- ``GET /api/status``: sim time and tick, part counts, viewer events received by type and id,
  and the classroom figures (PLAN 4.3): ``viewers`` (state sockets connected),
  ``frames_serialized``, ``frames_sent`` and ``frames_skipped`` (a slow viewer's skipped frames)
  and ``viewers_dropped`` (viewers disconnected because one send was pending more than
  :data:`STALLED_SEND_S`) since the server started, ``instructors`` (instructor views connected,
  counted in ``viewers`` too) and the core's ``dropped_ticks`` this run.
- ``GET /api/events``: the most recent viewer events with the core time they arrived.
- ``GET /api/fault``: active faults (``{id, type, kind, params, tick}``) and each component's
  supported kinds (PLAN 2.6). ``POST /api/fault`` with ``{"id", "kind", "params"}`` injects one,
  replacing that component's current fault, and returns it with the one it replaced;
  ``DELETE /api/fault/{id}`` clears it. ``{"comms": {"drop": true}}`` or
  ``{"comms": {"latency_ms": N}}`` stalls or delays the protocol server instead (PLAN 2.13; 400
  with no protocol server), ``DELETE /api/fault/comms`` clears that, and ``GET`` shows it as
  ``comms`` with ``protocols``, the servers it covers (PLAN 2.13b: one fault for Modbus TCP and
  OPC UA alike). Unknown components are 404, unsupported kinds or bad
  parameters 400. Faults act from the next tick. Access (PLAN §6.4, ruled 2026-10-27): open when
  the server is bound to a loopback address; bound beyond loopback, every ``/api/fault`` request
  needs the configured token in the :data:`FAULT_TOKEN_HEADER` header (401 without it or with a
  wrong one), and with no token configured the fault API is off (403).
- ``GET /api/exam``: exam mode (PLAN 2.11) and its operator switches, ``{enabled, switches:
  [{tag, component, port, description, on}]}``. ``POST /api/exam`` with ``{"tag", "on"}`` turns
  one (404 for a tag without a switch); it is guarded like ``/api/fault`` (open on loopback, the
  fault token beyond it), since the instructor drives it. In exam mode state frames carry no
  fault markers.
- ``GET /api/stats``: the run's statistics so far (PLAN 2.11, :mod:`openfactory.core.stats`):
  throughput per remover, conveyor utilization, actuator cycles, part dwell, and losses.
- ``GET /api/history?tag=TAG[&seconds=S]``: one AI tag's history for the trend panel (PLAN 3.6,
  :mod:`openfactory.core.history`): the last ten minutes (or ``S`` seconds) sampled at 2 Hz of
  simulated time, ``{tag, units, range, period_s, window_s, t: [s], v: [value]}``. 404 for a tag
  that is not an AI tag of the scene (with the reason) and in a replay; 400 for ``seconds`` <= 0.
- ``GET /api/parity``: the core's raw photo-eye beam edges with their ticks next to the browser's
  sensor events (:meth:`Runtime.parity`), for ``tools/parity_trace.py --live``.
- ``WS /ws/state``: the latest snapshot as JSON every 50 ms (20 Hz) on absolute deadlines, plus
  ``viewers``, how many viewers are connected. The simulation runs at its own 50 Hz; a frame is
  whatever snapshot is current when it is sent, so the viewer interpolates. One broadcaster serves
  every viewer (PLAN 4.3): each frame is serialized once and the same text goes to all of them,
  each send its own task, so a slow viewer skips frames (counted) instead of delaying the others,
  and a viewer whose send has been pending more than :data:`STALLED_SEND_S` is dropped (counted,
  its socket closed). If the broadcaster fails it logs the failure once and starts again after
  :data:`BROADCAST_RETRY_S` while any viewer is connected.
  Each browser keeps its own camera; faults and exam switches are the core's, so every viewer
  sees the same ones.
- ``WS /ws/instructor`` (PLAN 4.12): the instructor view's stream. The client's first message is
  ``{"token": "..."}``; with a fault token configured it must match (on any bind, loopback too:
  this stream shows what exam mode hides), with none it is open on loopback and refused beyond
  it, as ``/api/fault``. A refusal is ``{"error": ...}`` and close code 1008. Accepted, it gets
  the same frames as ``/ws/state`` from the same broadcaster, with each component's real state
  where exam mode hides it (a misaligned eye's ``beam_offset_m``), plus ``instructor: true``,
  ``faults`` (``{component id: fault kind}`` for every active fault, even in exam mode) and
  ``comms``, that text serialized once per frame for every instructor.
- ``WS /ws/events``: receives viewer events as JSON objects (see
  :meth:`Runtime.apply_event`). An invalid event is answered with ``{"error": "..."}`` and the
  socket stays open.
- **Browsers on other sites** (PLAN 4.1c fix 6, ruled 2026-11-12): no response carries CORS
  headers, so a page from another origin cannot read any answer. Every request that changes
  something (``POST``, ``PUT``, ``PATCH`` or ``DELETE`` under ``/api/``: the editor's preview,
  save, palette and new, ``/api/fault`` and ``/api/exam``) is refused with **403** when it has an
  ``Origin`` header that is not this server's own origin (the scheme and the ``Host`` the request
  was sent to), and a ``POST`` whose content type is not ``application/json`` is refused with
  **415**, so a cross-site form or a ``text/plain`` fetch, which a browser sends without asking
  first, never reaches a handler. A request with no ``Origin`` (curl, Python, the tests) is not
  a browser on another site and passes. ``WS /ws/events`` is refused the same way (closed before
  it is accepted, an HTTP 403) when its ``Origin`` is not this server's own.
- **DNS rebinding** (ruled 2026-11-13): a server bound to loopback answers only requests and
  sockets whose ``Host`` is a loopback name or address (``localhost``, ``127.0.0.0/8``, ``[::1]``,
  any port); anything else gets 421 (a socket is closed before it is accepted). A page on a site
  whose name is re-pointed at 127.0.0.1 sends its own name as ``Host`` and is refused. A server
  bound beyond loopback (``--bind 0.0.0.0``) does not check ``Host``.
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
from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from openfactory import __version__
from openfactory.core.config import Endpoint, load_settings
from openfactory.core.loader import load
from openfactory.server.runtime import Runtime

log = logging.getLogger(__name__)

STATE_RATE_HZ = 20.0
#: A viewer whose frame send has been pending this long is dropped (review of 2026-11-10).
STALLED_SEND_S = 5.0
#: After a broadcaster failure, wait this long before starting it again (review of 2026-11-10).
BROADCAST_RETRY_S = 1.0
#: How long an instructor socket may take to send its token (PLAN 4.12).
INSTRUCTOR_AUTH_S = 5.0
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


class ExamSwitch(BaseModel):
    """Body of ``POST /api/exam`` (PLAN 2.11)."""

    model_config = ConfigDict(extra="forbid")

    tag: str
    on: bool


class CommsRequest(BaseModel):
    """The ``comms`` body of ``POST /api/fault`` (PLAN 2.13)."""

    model_config = ConfigDict(extra="forbid")

    drop: bool = False
    latency_ms: float = Field(0.0, ge=0)


class EditRequest(BaseModel):
    """The scene editor's operations against a base text (PLAN 4.1a, server/editor.py)."""

    base: str
    ops: list[dict[str, Any]]


class NewComponentRequest(EditRequest):
    """A new component for the editor's palette (PLAN 4.1c): its type and where to place it."""

    type: str
    position: tuple[float, float, float] = (0.0, 0.0, 0.0)


class FaultRequest(BaseModel):
    """Body of ``POST /api/fault``: a component fault ``{id, kind, params}`` (PLAN 2.6) or a comms
    fault ``{comms: {drop, latency_ms}}`` (PLAN 2.13), not both."""

    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    kind: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    comms: CommsRequest | None = None

    @model_validator(mode="after")
    def _one_kind_of_fault(self) -> FaultRequest:
        component = self.id is not None and self.kind is not None
        if component == (self.comms is not None):
            raise ValueError("give either id and kind, or comms")
        return self


WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"

_NO_BUILD = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>OpenFactory</title></head>
<body style="background:#1F2A36;color:#E5E7EB;font-family:sans-serif">
<h1>OpenFactory</h1><p>The viewer is not built. Run <code>npm run build</code> in <code>web/</code>.
The API is up: <a style="color:#60A5FA" href="/api/scene">/api/scene</a>.</p></body></html>"""


#: Methods that change something; only these are checked for their origin.
CHANGING = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def write_refusal(
    method: str, scheme: str, host: str | None, origin: str | None, content_type: str | None
) -> tuple[int, str] | None:
    """Why a changing ``/api/`` request is refused, as ``(status, reason)``, or None (PLAN 4.1c
    fix 6). A browser sends ``Origin`` with every cross-origin write; it must be this server's
    own, ``scheme://host`` as the request addressed it. A POST must be JSON: a browser sends a
    form or ``text/plain`` to another site without asking, but never ``application/json``."""
    if origin is not None and origin != f"{scheme}://{host}":
        return 403, f"refused: a request from {origin} is not from this server's own pages"
    if method == "POST":
        mime = (content_type or "").split(";", 1)[0].strip().lower()
        if mime != "application/json":
            return 415, "refused: send the request body as application/json"
    return None


def host_refusal(host: str | None) -> str | None:
    """Why a request to a loopback-bound server is refused for its ``Host`` header, or None
    (DNS rebinding). A missing ``Host`` passes: only an HTTP/1.0 client omits it, never a
    browser."""
    if host is None:
        return None
    name = host.strip()
    if name.startswith("["):  # [::1]:8080
        name = name[1 : name.find("]")] if "]" in name else name
    elif name.count(":") == 1:
        name = name.split(":", 1)[0]
    if is_loopback(name.lower()):
        return None
    return f"refused: Host {host!r} is not a loopback name or address"


def ws_origin_refusal(scheme: str, host: str | None, origin: str | None) -> str | None:
    """Why a websocket is refused for its ``Origin``, or None. A browser sends ``http(s)://``
    origins for ``ws(s)://`` sockets; no ``Origin`` (a script) passes."""
    if origin is None:
        return None
    own = f"{'https' if scheme in ('wss', 'https') else 'http'}://{host}"
    if origin != own:
        return f"refused: a socket from {origin} is not from this server's own pages"
    return None


class LoopbackHostOnly:
    """ASGI middleware: refuse any HTTP request or websocket whose ``Host`` fails
    :func:`host_refusal` (421, or a socket closed before it is accepted)."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] in ("http", "websocket"):
            headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope["headers"]}
            refusal = host_refusal(headers.get("host"))
            if refusal is not None:
                log.warning("%s", refusal)
                if scope["type"] == "http":
                    await JSONResponse({"detail": refusal}, status_code=421)(scope, receive, send)
                else:
                    await send({"type": "websocket.close", "code": 1008})
                return
        await self.app(scope, receive, send)


def create_app(
    runtime: Runtime,
    web_dist: Path | None = WEB_DIST,
    *,
    bind: str = "127.0.0.1",
    fault_token: str | None = None,
    edit: Any = None,
) -> FastAPI:
    """The app for one runtime. ``bind`` is the address the server listens on; it decides
    whether ``/api/fault`` is open (loopback) or needs ``fault_token`` (PLAN §6.4)."""
    app = FastAPI(title="OpenFactory", version=__version__, docs_url="/api/docs")
    app.state.runtime = runtime
    faults_open = is_loopback(bind)
    if faults_open:
        app.add_middleware(LoopbackHostOnly)

    @app.middleware("http")
    async def same_origin_writes(request: Request, call_next: Any) -> Any:
        """Refuse a change from a page on another site (see the module docstring)."""
        if request.method in CHANGING and request.url.path.startswith("/api/"):
            refusal = write_refusal(
                request.method,
                request.url.scheme,
                request.headers.get("host"),
                request.headers.get("origin"),
                request.headers.get("content-type"),
            )
            if refusal is not None:
                return JSONResponse({"detail": refusal[1]}, status_code=refusal[0])
        return await call_next(request)

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
        if edit is not None:  # PLAN 4.1a: the file being edited, as it is now
            return edit.document()
        return runtime.scene_document()

    if edit is not None:
        from openfactory.server.editor import EditConflict, EditRejected

        @app.get("/api/edit/source")
        def edit_source() -> dict[str, Any]:
            return edit.source()

        def run_edit(action: Any, body: EditRequest) -> dict[str, Any]:
            try:
                return action(body.base, body.ops)
            except EditConflict as exc:
                raise HTTPException(409, str(exc)) from None
            except EditRejected as exc:
                raise HTTPException(422, exc.result) from None
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from None

        @app.post("/api/edit/preview")
        def edit_preview(body: EditRequest) -> dict[str, Any]:
            return run_edit(edit.preview, body)

        @app.post("/api/edit/save")
        def edit_save(body: EditRequest) -> dict[str, Any]:
            return run_edit(edit.save, body)

        @app.post("/api/edit/palette")
        def edit_palette(body: EditRequest) -> dict[str, Any]:
            return run_edit(edit.palette, body)

        @app.post("/api/edit/new")
        def edit_new(body: NewComponentRequest) -> dict[str, Any]:
            return run_edit(lambda base, ops: edit.new(base, ops, body.type, body.position), body)

    @app.get("/models/{name}")
    def model(name: str) -> FileResponse:
        """A custom mesh the scene declares (PLAN 2.15), by component id; nothing else is served.
        A replay has no ``meshes`` and serves none."""
        if edit is not None:  # PLAN 4.1c: the scene as last previewed
            meshes = edit.meshes
        else:
            meshes = getattr(getattr(runtime, "loaded", None), "meshes", {})
        comp_id, _, ext = name.rpartition(".")
        if ext != "glb" or comp_id not in meshes:
            raise HTTPException(status_code=404, detail=f"no mesh {name!r} in this scene")
        return FileResponse(meshes[comp_id], media_type="model/gltf-binary")

    @app.get("/api/state")
    def state() -> dict[str, Any]:
        return runtime.snapshot

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        scheduler = getattr(runtime, "scheduler", None)  # a replay has none
        return {
            **runtime.status(),
            "viewers": len(viewers),
            "frames_serialized": frames["serialized"],
            "frames_sent": frames["sent"],
            "frames_skipped": frames["skipped"],
            "viewers_dropped": frames["dropped"],
            "instructors": len(instructors),
            "dropped_ticks": scheduler.stats.dropped if scheduler is not None else 0,
        }

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
            if request.comms is not None:
                return runtime.set_comms(**request.comms.model_dump())
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

    @app.get("/api/exam")
    def exam() -> dict[str, Any]:
        return runtime.exam()

    @app.post("/api/exam", dependencies=guarded)
    def set_exam(request: ExamSwitch) -> dict[str, Any]:
        try:
            return runtime.set_exam_switch(request.tag, request.on)
        except KeyError as exc:
            raise HTTPException(404, str(exc.args[0])) from None

    @app.get("/api/stats")
    def stats() -> dict[str, Any]:
        return runtime.statistics()

    @app.get("/api/history")
    def history(tag: str, seconds: float | None = None) -> dict[str, Any]:
        """One AI tag's last ten minutes at 2 Hz of simulated time (PLAN 3.6)."""
        if seconds is not None and seconds <= 0:
            raise HTTPException(400, "seconds must be positive")
        try:
            return runtime.history(tag, seconds)
        except KeyError as exc:
            raise HTTPException(404, str(exc.args[0])) from None

    @app.get("/api/parity")
    def parity() -> dict[str, Any]:
        return runtime.parity()

    # One broadcaster for every viewer (PLAN 4.3, the classroom): each 20 Hz frame is serialized
    # once and the same text is sent to every connected viewer; a viewer still sending its last
    # frame skips this one instead of holding the others back; one stuck on a send for more than
    # STALLED_SEND_S is dropped. Each viewer maps to its pending send and when that send started.
    viewers: dict[WebSocket, tuple[asyncio.Task[None], float] | None] = {}
    frames = {"serialized": 0, "sent": 0, "skipped": 0, "dropped": 0}
    broadcaster: dict[str, asyncio.Task[None] | None] = {"task": None}
    dropping: set[asyncio.Task[None]] = set()
    instructors: set[WebSocket] = set()  # PLAN 4.12: these viewers get the instructor frame
    app.state.viewers = viewers
    app.state.frames = frames

    def frame_text() -> str:
        """The current snapshot and the viewer count as JSON, serialized once per frame for
        every viewer. All WebSocket handlers run on this event loop's thread: no lock."""
        frames["serialized"] += 1
        return json.dumps({**runtime.snapshot, "viewers": len(viewers)}, separators=(",", ":"))

    def instructor_text() -> str:
        """The frame plus every active fault (PLAN 4.12), serialized once for every instructor.
        A replay has no fault API: its recorded component states carry their faults."""
        faults_of = getattr(runtime, "faults", None)
        found = faults_of() if faults_of is not None else {"active": [], "comms": None}
        hidden_of = getattr(runtime, "exam_hidden", None)
        hidden = hidden_of() if hidden_of is not None else {}
        frames["serialized"] += 1
        snap = runtime.snapshot
        components = snap["components"]
        if hidden:  # exam mode: draw the plant as it is (a misaligned eye where it points)
            components = {k: {**v, **hidden.get(k, {})} for k, v in components.items()}
        frame = {
            **snap,
            "components": components,
            "viewers": len(viewers),
            "instructor": True,
            "faults": {f["id"]: f["kind"] for f in found["active"]},
            "comms": found.get("comms"),
        }
        return json.dumps(frame, separators=(",", ":"))

    async def send(ws: WebSocket, text: str) -> None:
        try:
            await ws.send_text(text)
            frames["sent"] += 1
        except (WebSocketDisconnect, RuntimeError):
            viewers.pop(ws, None)

    async def broadcast() -> None:
        """Serve frames while any viewer is connected. A failure is logged once and the
        broadcaster starts again after BROADCAST_RETRY_S if viewers remain (review of
        2026-11-10), so connected viewers never freeze on a dead task."""
        try:
            while viewers:
                try:
                    await _broadcast()
                except Exception:
                    log.exception(
                        "state broadcaster failed; restarting in %g s while %d viewer(s) stay",
                        BROADCAST_RETRY_S,
                        len(viewers),
                    )
                    await asyncio.sleep(BROADCAST_RETRY_S)
        finally:
            broadcaster["task"] = None

    async def drop(ws: WebSocket, pending: asyncio.Task[None]) -> None:
        """Disconnect a viewer whose send is stuck: forget it, cancel the send, close it."""
        frames["dropped"] += 1
        viewers.pop(ws, None)
        instructors.discard(ws)
        pending.cancel()
        log.warning("dropped a viewer whose frame send was pending over %g s", STALLED_SEND_S)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(ws.close(code=1008), timeout=1.0)

    async def _broadcast() -> None:
        loop = asyncio.get_running_loop()
        period = 1.0 / STATE_RATE_HZ
        deadline = loop.time()
        while viewers:
            text = frame_text()
            teacher = instructor_text() if instructors else text
            now = loop.time()
            for ws, entry in list(viewers.items()):
                if entry is not None and not entry[0].done():
                    if now - entry[1] > STALLED_SEND_S:
                        task = asyncio.create_task(drop(ws, entry[0]))
                        dropping.add(task)  # keep a reference until it is done
                        task.add_done_callback(dropping.discard)
                    else:
                        frames["skipped"] += 1  # a slow viewer: it gets the next frame instead
                    continue
                body = teacher if ws in instructors else text
                viewers[ws] = (asyncio.create_task(send(ws, body)), now)
            deadline += period
            delay = deadline - loop.time()
            if delay < -period:  # fell behind: restart the schedule
                deadline = loop.time()
                delay = 0.0
            await asyncio.sleep(max(delay, 0.0))

    async def serve_viewer(ws: WebSocket) -> None:
        """Join the broadcaster until the viewer leaves (it sends nothing; this notices)."""
        viewers[ws] = None
        task = broadcaster["task"]
        if task is None or task.done():  # none yet, or it ended (or failed): start one
            broadcaster["task"] = asyncio.create_task(broadcast())
        try:
            while True:
                await ws.receive_text()
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            instructors.discard(ws)
            entry = viewers.pop(ws, None)
            if entry is not None:
                entry[0].cancel()

    @app.websocket("/ws/state")
    async def ws_state(ws: WebSocket) -> None:
        await ws.accept()
        await serve_viewer(ws)

    def instructor_refusal(token: object) -> str | None:
        """Why an instructor socket is refused, or None (PLAN 4.12). A configured token is
        always required; with none, loopback is open and anything else refused."""
        if expected is not None:
            if isinstance(token, str) and hmac.compare_digest(token.encode(), expected):
                return None
            return "instructor view: missing or wrong fault token"
        if faults_open:
            return None
        return "instructor view off: this server is bound beyond loopback and has no fault token"

    @app.websocket("/ws/instructor")
    async def ws_instructor(ws: WebSocket) -> None:
        await ws.accept()
        try:
            first = await asyncio.wait_for(ws.receive_text(), timeout=INSTRUCTOR_AUTH_S)
            sent = json.loads(first)
            token = sent.get("token") if isinstance(sent, dict) else None
        except (TimeoutError, ValueError, WebSocketDisconnect, RuntimeError):
            token = None
        refusal = instructor_refusal(token)
        if refusal is not None:
            log.warning("refused an instructor view: %s", refusal)
            with contextlib.suppress(Exception):
                await ws.send_text(json.dumps({"error": refusal, "refused": True}))
                await ws.close(code=1008)
            return
        instructors.add(ws)
        await serve_viewer(ws)

    @app.websocket("/ws/events")
    async def ws_events(ws: WebSocket) -> None:
        refusal = ws_origin_refusal(ws.url.scheme, ws.headers.get("host"), ws.headers.get("origin"))
        if refusal is not None:
            log.warning("%s", refusal)
            await ws.close(code=1008)
            return
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

        @app.get("/instructor", include_in_schema=False)
        def instructor_page() -> FileResponse:
            """The instructor view (PLAN 4.12): the same viewer, which reads its path."""
            return FileResponse(web_dist / "index.html")

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
