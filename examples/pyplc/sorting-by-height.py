"""OpenFactory example stand-in PLC for scenes/sorting-by-height.yaml (PLAN 2.10).

Run it with no PLC at all:

    uv run openfactory run scenes/sorting-by-height.yaml \
        --script examples/pyplc/sorting-by-height.py

This is a line-for-line port of examples/openplc/sorting-by-height.st, the OpenPLC program, so the
two stay in step: same sequence, same timers, same tag names. tests/unit/test_pyplc.py checks that
the constants agree with the ST file. The only intended difference is DIVERT_DELAY_S. The ST
program subtracts OpenPLC's average 0.10 s Modbus polling latency (T#1980ms); a script scans the I/O
table directly every 20 ms tick, with no polling latency, so it uses the full 2.08 s
eye-to-plate travel time.

Sequence of operations
  1. Run both belt conveyors and write both speed references, 100 % each.
  2. Enable the box emitter once both belts have reported running for 1 s without a break.
  3. On each PE_01 rising edge (tall boxes only: the beam is above short ones), start that box's
     own DIVERT_DELAY_S timer for its travel from the eye to the pusher plate, then extend CYL_01.
     Up to DIVERT_SLOTS boxes can be on their way at once, each in its own slot (a ring), so a
     second tall box tripping the eye before the first is pushed keeps its divert.
  4. Retract CYL_01 as soon as its extended limit is made.

Training and demo use only. Not a safety-rated program and not a safety-rated simulation.
"""

from openfactory.pyplc import R_TRIG, TON

SPEED_PCT = 100.0  # ST: SPEED_RAW := 32767 (100 % in int16_scaled raw counts)
TRAVEL_S = 2.08  # PE_01 to the plate at 0.5 m/s (tests/unit/test_sorter_headless.py)
OPENPLC_POLL_LATENCY_S = 0.10  # what the ST program subtracts; zero for a script
DIVERT_DELAY_S = TRAVEL_S  # ST: DIVERT_DELAY := T#1980ms = TRAVEL_S - OPENPLC_POLL_LATENCY_S
BELTS_OK_DELAY_S = 1.0  # ST: BELTS_OK_DELAY := T#1s

DIVERT_SLOTS = 4  # ST: DIVERT_PEND_0..3 and DIVERT_TMR_0..3

PE_01_RISE = R_TRIG()  # tall box arrives at PE_01
DIVERT_TMR = [TON(DIVERT_DELAY_S) for _ in range(DIVERT_SLOTS)]  # one eye-to-plate timer per box
BELTS_OK_TMR = TON(BELTS_OK_DELAY_S)  # both belts running, debounced
divert_pend = [False] * DIVERT_SLOTS  # slot i holds a tall box on its way to the pusher
next_slot = 0  # ring index: the slot the next tall box takes
divert_overrun = False  # a tall box found its slot still busy (more than DIVERT_SLOTS in transit)
push_cmd = False  # pusher extend request, held until the extended limit


def step(io, t):
    global next_slot, divert_overrun, push_cmd

    # 1. Belts run at 100 %
    io["CONV_01_RUN_DO"] = True
    io["CONV_02_RUN_DO"] = True
    io["CONV_01_SPD_AO"] = SPEED_PCT
    io["CONV_02_SPD_AO"] = SPEED_PCT

    # 2. Feed boxes only after both belts have reported running for BELTS_OK_DELAY_S
    io["EMT_01_EN_DO"] = BELTS_OK_TMR(io["CONV_01_RUN_DI"] and io["CONV_02_RUN_DI"], t)

    # 3. Tall box at the eye: give it the next slot and its own travel timer; extend when it is due
    if PE_01_RISE(io["PE_01_DI"]):
        if divert_pend[next_slot]:
            divert_overrun = True  # the slot's earlier box keeps its timer; this box is lost
        divert_pend[next_slot] = True
        next_slot = (next_slot + 1) % DIVERT_SLOTS
    for i in range(DIVERT_SLOTS):
        if DIVERT_TMR[i](divert_pend[i], t):
            divert_pend[i] = False
            push_cmd = True

    # 4. Retract on the extended limit
    if io["CYL_01_EXT_DI"]:
        push_cmd = False
    io["CYL_01_EXT_DO"] = push_cmd
