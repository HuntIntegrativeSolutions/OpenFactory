"""Comms faults on the protocol servers (PLAN 2.13, 2.13b).

One :class:`CommsFault` is shared by every protocol server in a run (Modbus TCP and OPC UA), so
``/api/fault`` ``{"comms": ...}``, ``--fault comms=...``, the viewer's fault menu and recordings
act on all of them at once. Each server reads it on every request; the simulation keeps
running. Not a safety-rated simulation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class CommsFault:
    """A comms fault on the protocol servers; all defaults mean healthy."""

    #: Ignore every request: no reply, the client times out.
    drop: bool = False
    #: Answer every request this much later, milliseconds.
    latency_ms: float = 0.0

    def active(self) -> bool:
        return self.drop or self.latency_ms > 0

    def describe(self) -> dict[str, Any]:
        return {"drop": self.drop, "latency_ms": self.latency_ms}
