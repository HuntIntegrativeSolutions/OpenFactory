"""OpenFactory example stand-in PLC for scenes/buffer-and-release.yaml (PLAN 2.7, 2.10).

Run it with no PLC at all:

    uv run openfactory run scenes/buffer-and-release.yaml \
        --script examples/pyplc/buffer-and-release.py

A line-for-line port of examples/openplc/buffer-and-release.st: same sequence, same timers, same
tag names (tests/integration/test_shipped_scenes.py checks the tags agree).

Sequence of operations
  1. Run both belts. Hold STP_01 raised, so boxes queue nose to tail on the buffer belt.
  2. Enable the box emitter once both belts have reported running for 1 s.
  3. A batch is ready when PE_FULL (under the third queued box) has been blocked for 1.5 s, which a
     moving box (1 s in the beam) cannot do, and the blade has been back up for at least 2 s.
  4. Release: lower STP_01 and count PE_REL rising edges on the outfeed belt. The outfeed runs
     three times faster than the buffer, so each released box pulls a gap and the eye clears
     between boxes. At the third, raise the blade: the fourth box is still behind the blade line.

Training and demo use only. Not a safety-rated program and not a safety-rated simulation.
"""

from openfactory.pyplc import R_TRIG, TON

BATCH = 3  # ST: BATCH : INT := 3
BELTS_OK_DELAY_S = 1.0  # ST: BELTS_OK_DELAY := T#1s
FULL_DELAY_S = 1.5  # ST: FULL_DELAY := T#1500ms
PAUSE_S = 2.0  # ST: PAUSE := T#2s

BELTS_OK_TMR = TON(BELTS_OK_DELAY_S)  # both belts running, debounced
FULL_TMR = TON(FULL_DELAY_S)  # a box stands under PE_FULL: three are queued
PAUSE_TMR = TON(PAUSE_S)  # the blade has been back up this long since the last batch
PE_REL_RISE = R_TRIG()  # a released box reaches PE_REL
releasing = False  # a batch is on its way out
released = 0  # boxes counted out in this batch


def step(io, t):
    global releasing, released

    # 1. Belts run
    io["CONV_BUF_RUN_DO"] = True
    io["CONV_OUT_RUN_DO"] = True

    # 2. Feed boxes only after both belts have reported running for BELTS_OK_DELAY_S
    io["EMT_01_EN_DO"] = BELTS_OK_TMR(io["CONV_BUF_RUN_DI"] and io["CONV_OUT_RUN_DI"], t)

    # 3. A batch is ready: three queued, and the blade up long enough since the last batch
    full = FULL_TMR(io["PE_FULL_DI"], t)
    paused = PAUSE_TMR(io["STP_01_UP_DI"] and not releasing, t)
    if full and paused and not releasing:
        releasing = True
        released = 0

    # 4. Count the batch out on the outfeed; raise the blade at the last one
    if PE_REL_RISE(io["PE_REL_DI"]) and releasing:
        released += 1
        if released >= BATCH:
            releasing = False
    io["STP_01_RAISE_DO"] = not releasing
