"""OpenFactory example stand-in PLC for scenes/tank-level-onoff.yaml (PLAN 3.3, 2.10).

Run it with no PLC at all:

    uv run openfactory run scenes/tank-level-onoff.yaml \
        --script examples/pyplc/tank-level-onoff.py

A line-for-line port of examples/openplc/tank-level-onoff.st: same sequence, same tag names.

Sequence of operations
  1. The low level switch (LSL, TK_02_LSL_DI) starts the fill pump P_01.
  2. The pump runs on (seal-in) until the high level switch (LSH, TK_02_LSH_DI) stops it, so the
     level swings between the two switches instead of chattering at one: on/off control with
     the switches as the deadband.
  3. If both switches read on at once (a wiring or switch fault: the tank cannot be both low and
     high), the pump stops.

Training and demo use only. Not a safety-rated program and not a safety-rated simulation.
"""

fill = False  # the pump is filling (seal-in)


def step(io, t):
    global fill

    low, high = io["TK_02_LSL_DI"], io["TK_02_LSH_DI"]
    # 1-2. Start at low, seal in, stop at high; 3. both switches on is a fault: stop
    fill = (low or fill) and not high
    io["P_01_RUN_DO"] = fill
