"""Simulator heartbeat (ruled 2026-10-30, PLAN §6.1): the ``SIM_HEARTBEAT_DI`` tag.

Behavior spec
-------------
Every loaded scene has one tag no scene declares: ``SIM_HEARTBEAT_DI``, a DI owned by the
simulator itself (owner ``""``). It is a 1 Hz square wave in simulated time: false for the first
0.5 s, true for the next 0.5 s, and so on, so it changes every 25 ticks at the default 50 Hz. It is
computed from the tick count and ``dt``, never the clock (PLAN §3.2), and written by
:meth:`openfactory.core.loader.LoadedScene.step` after everything else in the tick.

It exists so a PLC can tell the simulation is down: if the simulation stops or stalls, or the
protocol server stops answering, the PLC sees the tag stop changing. A watchdog timer of a few
seconds on its edges is enough.

Every protocol serves it like any other DI. In the Modbus map it is allocated after every DI the
scene declares, so in a lockfile written before it existed it lands at the next free discrete
input address and no existing tag is renumbered (ADR-0004 decision 10). The name is reserved: a
scene that binds a tag called ``SIM_HEARTBEAT_DI`` fails to load.
"""

from __future__ import annotations

import math

from openfactory.core.iotable import Tag

HEARTBEAT_TAG = "SIM_HEARTBEAT_DI"
#: Half the 1 Hz period: the tag changes every this many simulated seconds.
HALF_PERIOD_S = 0.5
DESCRIPTION = (
    "OpenFactory simulator heartbeat — 1 Hz square wave in simulated time, changes every 0.5 s; "
    "stops changing when the simulation or its protocol server stops"
)


def heartbeat_tag() -> Tag:
    return Tag.new(HEARTBEAT_TAG, "DI", description=DESCRIPTION, owner="", value=False)


def heartbeat_value(tick: int, dt: float) -> bool:
    """The heartbeat after ``tick`` completed ticks of ``dt`` seconds."""
    return math.floor(tick * dt / HALF_PERIOD_S + 1e-9) % 2 == 1
