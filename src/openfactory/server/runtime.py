"""Running scene: the simulation thread and what the web server reads from it (PLAN 1.9).

Behavior spec
-------------
A :class:`Runtime` owns one :class:`~openfactory.core.loader.LoadedScene` and steps it with the
fixed-step :class:`~openfactory.core.scheduler.Scheduler` on a background thread (realtime by
default, or as fast as possible). After every tick, still holding the step lock, it publishes a new
:class:`dict` snapshot (:attr:`Runtime.snapshot`) of everything the viewer needs:

``{"t", "tick", "components": {id: state()}, "parts": [{id, name, size, position, velocity,
carrier}], "io": {tag: value}}``. A part's ``velocity`` (m/s, world frame) is its belt-direction
speed while carried or its fall speed while falling, so the viewer can start a newly seen part's
physics body moving as the core's part already is (PLAN 2.0a).

The snapshot is built fresh each tick and never mutated afterwards, so the web server can serialize
the latest one from its own thread at any rate (20 Hz for ``/ws/state``) without locking or racing
the simulation.

Viewer events (PLAN §3.3, ``/ws/events``) go through :meth:`Runtime.apply_event`:

- ``{"type": "press", "id": PB}`` presses a pushbutton (applied on the next tick);
- ``{"type": "select", "id": SS, "value": "a" | "center" | "b"}`` turns a selector switch;
- ``{"type": "sensor", "id": PE, "value": bool}`` and ``{"type": "part", "id": ...}`` are reports
  from the browser physics. The headless model is authoritative for PLC tags through Milestone 2;
  browser sensor events are parity-only (PLAN §3.3). So these are recorded with the core's time
  and tick, counted, and exposed for parity checks, but never change tags.

Every event is recorded in :attr:`Runtime.events` (the last 1000) and counted by type and id.

With a ``controller`` (:class:`~openfactory.pyplc.ScriptPlc`, PLAN 2.10) the runtime scans it at
the simulated time before every tick, under the same lock. An exception from the script is kept
in :attr:`Runtime.fault` and stops the run.

For the live parity check (``tools/parity_trace.py --live``), the runtime also records the core's
own raw photo-eye beam edges, ``{"id", "blocked", "tick"}`` after the tick that changed them, in
:attr:`Runtime.sensor_edges` (the last 1000). These are the undebounced beam states the parity
traces compare, not the debounced DI the PLC reads.
"""

from __future__ import annotations

import threading
import traceback
from collections import Counter, deque
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from openfactory.core.geometry import rotate_y
from openfactory.core.loader import LoadedScene
from openfactory.core.scheduler import DEFAULT_RATE_HZ, RunStats, Scheduler
from openfactory.pyplc import ScriptPlc

EVENT_LOG_SIZE = 1000


class ViewerEvent(BaseModel):
    """One message on ``/ws/events``. Extra keys (part details, positions) are kept."""

    model_config = ConfigDict(extra="allow")

    type: Literal["sensor", "part", "press", "select"]
    id: str
    t: float | None = None
    value: bool | str | None = None


class Runtime:
    def __init__(
        self,
        loaded: LoadedScene,
        *,
        rate_hz: float = DEFAULT_RATE_HZ,
        realtime: bool = True,
        controller: ScriptPlc | None = None,
    ) -> None:
        self.loaded = loaded
        #: A scripted stand-in PLC (PLAN 2.10), scanned before every tick.
        self.controller = controller
        #: Traceback of a controller exception; the run stops when one is set.
        self.fault: str | None = None
        self.lock = threading.Lock()
        self.scheduler = Scheduler(self._step, rate_hz, realtime=realtime)
        self.snapshot: dict[str, Any] = self._take_snapshot()
        self.events: deque[dict[str, Any]] = deque(maxlen=EVENT_LOG_SIZE)
        self.event_counts: Counter[str] = Counter()
        self.stats: RunStats | None = None
        self._thread: threading.Thread | None = None
        self._eyes = [c for c in loaded.components if c.type == "photo_eye"]
        self._beam = {c.id: bool(getattr(c, "blocked", False)) for c in self._eyes}
        self.sensor_edges: deque[dict[str, Any]] = deque(maxlen=EVENT_LOG_SIZE)

    # -- simulation thread -----------------------------------------------------------------------

    def _step(self, dt: float) -> None:
        with self.lock:
            if self.controller is not None:
                try:
                    self.controller.scan(self.loaded.time)
                except Exception:  # a faulted program stops the run, it never hangs it
                    self.fault = traceback.format_exc()
                    self.scheduler.stop()
                    return
            self.loaded.step(dt)
            for eye in self._eyes:
                blocked = bool(eye.blocked)  # type: ignore[attr-defined]
                if blocked != self._beam[eye.id]:
                    self._beam[eye.id] = blocked
                    self.sensor_edges.append(
                        {"id": eye.id, "blocked": blocked, "tick": self.loaded.tick}
                    )
            self.snapshot = self._take_snapshot()

    def _take_snapshot(self) -> dict[str, Any]:
        loaded = self.loaded
        return {
            "t": round(loaded.time, 6),
            "tick": loaded.tick,
            "components": {c.id: c.state() for c in loaded.components},
            "parts": [
                {
                    "id": p.id,
                    "name": p.name,
                    "size": list(p.size),
                    "position": list(p.position),
                    "velocity": self._velocity(p),
                    "carrier": p.carrier,
                }
                for p in loaded.world.parts.values()
            ],
            "io": loaded.io.values(),
        }

    def _velocity(self, part: Any) -> list[float]:
        if part.carrier is None:
            return [0.0, part.vy, 0.0]
        rotation = self.loaded.component(part.carrier).config.rotation_deg
        fx, _, fz = rotate_y((1.0, 0.0, 0.0), rotation)
        return [fx * part.v_flow, 0.0, fz * part.v_flow]

    def start(self, duration_s: float | None = None) -> None:
        """Run the scheduler on a daemon thread until :meth:`stop` (or ``duration_s``)."""
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("runtime already started")

        def run() -> None:
            self.stats = self.scheduler.run(duration_s)

        self._thread = threading.Thread(target=run, name="openfactory-sim", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self.scheduler.stop()
        if self._thread is not None:
            self._thread.join(timeout)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # -- static scene description ----------------------------------------------------------------

    def scene_document(self) -> dict[str, Any]:
        """The resolved scene for ``GET /api/scene``: metadata, geometry, connections, tags."""
        scene = self.loaded.scene
        descriptions = {c.id: c.description for c in scene.components}
        return {
            "openfactory": scene.openfactory,
            "scene": scene.scene.model_dump(),
            "plc": {"protocol": scene.plc.protocol, "role": scene.plc.role},
            "dt": self.scheduler.dt,
            "components": [
                {**c.geometry(), "description": descriptions[c.id]} for c in self.loaded.components
            ],
            "connections": [{"from": c.from_, "to": c.to} for c in scene.connections],
            "tags": [
                {
                    "name": t.name,
                    "kind": t.kind,
                    "dtype": t.dtype,
                    "owner": t.owner,
                    "description": t.description,
                }
                for t in self.loaded.io.tags()
            ],
            "warnings": list(self.loaded.warnings),
        }

    # -- viewer events ---------------------------------------------------------------------------

    def apply_event(self, raw: dict[str, Any]) -> ViewerEvent:
        """Validate, record, and act on one viewer event. Raises ValueError if it is invalid."""
        event = ViewerEvent.model_validate(raw)
        with self.lock:
            comp = next((c for c in self.loaded.components if c.id == event.id), None)
            if comp is None:
                raise ValueError(f"unknown component {event.id!r}")
            if event.type == "press":
                if comp.type != "pushbutton":
                    raise ValueError(f"{event.id} is a {comp.type}, not a pushbutton")
                comp.press()  # type: ignore[attr-defined]
            elif event.type == "select":
                if comp.type != "selector_switch":
                    raise ValueError(f"{event.id} is a {comp.type}, not a selector_switch")
                comp.set_position(str(event.value))  # type: ignore[attr-defined]
            self.events.append(
                {**event.model_dump(), "core_t": self.loaded.time, "core_tick": self.loaded.tick}
            )
            self.event_counts[f"{event.type}:{event.id}"] += 1
        return event

    def parity(self) -> dict[str, Any]:
        """Core beam edges and browser sensor events, for ``tools/parity_trace.py --live``."""
        with self.lock:
            return {
                "tick": self.loaded.tick,
                "dt": self.scheduler.dt,
                "core": list(self.sensor_edges),
                "viewer": [e for e in self.events if e["type"] == "sensor"],
            }

    def status(self) -> dict[str, Any]:
        snap = self.snapshot
        return {
            "scene": self.loaded.scene.scene.name,
            "t": snap["t"],
            "tick": snap["tick"],
            "running": self.running,
            "parts": len(snap["parts"]),
            "spawned": self.loaded.world.spawned,
            "removed": dict(self.loaded.world.removed),
            "lost": dict(self.loaded.world.lost),
            "events": dict(self.event_counts),
        }
