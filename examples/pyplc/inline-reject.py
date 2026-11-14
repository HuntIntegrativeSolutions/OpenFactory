"""OpenFactory example stand-in PLC for scenes/inline-reject.yaml (PLAN 2.10).

The scene was built in the scene editor from `openfactory new`, with no YAML written by hand
(tests/integration/test_editor_build.py rebuilds it). Run it with no PLC at all:

    uv run openfactory run scenes/inline-reject.yaml --script examples/pyplc/inline-reject.py

Sequence of operations
  1. Run both belt conveyors (CONV_01 feeds CONV_02 end to end).
  2. Enable the box emitter once both belts have reported running for 1 s without a break.
  3. On each PE_01 rising edge (oversize boxes only: the beam is above good ones), start that
     box's own REJECT_DELAY_S timer for its travel from the eye to the pusher plate, then extend
     CYL_01, which pushes it off the side of CONV_02 into the reject bin, REM_01. Up to
     REJECT_SLOTS boxes can be on their way at once, each in its own slot (a ring).
  4. Retract CYL_01 as soon as its extended limit is made.
  Good boxes ride on to the end of CONV_02, into REM_02.

Training and demo use only. Not a safety-rated program and not a safety-rated simulation.
"""

from openfactory.pyplc import R_TRIG, TON

TRAVEL_S = 2.08  # PE_01 to the plate at 0.5 m/s: the sorter's 1.0 m eye-to-plate layout
REJECT_DELAY_S = TRAVEL_S
BELTS_OK_DELAY_S = 1.0
REJECT_SLOTS = 4

PE_01_RISE = R_TRIG()  # an oversize box arrives at PE_01
REJECT_TMR = [TON(REJECT_DELAY_S) for _ in range(REJECT_SLOTS)]  # one eye-to-plate timer per box
BELTS_OK_TMR = TON(BELTS_OK_DELAY_S)  # both belts running, debounced
reject_pend = [False] * REJECT_SLOTS  # slot i holds an oversize box on its way to the pusher
next_slot = 0  # ring index: the slot the next oversize box takes
push_cmd = False  # pusher extend request, held until the extended limit


def step(io, t):
    global next_slot, push_cmd

    # 1. Belts run
    io["CONV_01_RUN_DO"] = True
    io["CONV_02_RUN_DO"] = True

    # 2. Feed boxes only after both belts have reported running for BELTS_OK_DELAY_S
    io["EMT_01_DO"] = BELTS_OK_TMR(io["CONV_01_RUNNING_DI"] and io["CONV_02_RUNNING_DI"], t)

    # 3. Oversize box at the eye: give it the next slot and its own travel timer
    if PE_01_RISE(io["PE_01_DI"]):
        reject_pend[next_slot] = True
        next_slot = (next_slot + 1) % REJECT_SLOTS
    for i in range(REJECT_SLOTS):
        if REJECT_TMR[i](reject_pend[i], t):
            reject_pend[i] = False
            push_cmd = True

    # 4. Retract on the extended limit
    if io["CYL_01_EXTENDED_DI"]:
        push_cmd = False
    io["CYL_01_EXTEND_DO"] = push_cmd
