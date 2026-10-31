"""OpenFactory example stand-in PLC for scenes/two-lane-merge.yaml (PLAN 2.7, 2.10).

Run it with no PLC at all:

    uv run openfactory run scenes/two-lane-merge.yaml --script examples/pyplc/two-lane-merge.py

A line-for-line port of examples/openplc/two-lane-merge.st: same states, same timers, same tag
names (tests/integration/test_shipped_scenes.py checks the tags agree).

Sequence of operations
  1. Run both lanes and the outfeed; enable both box sources once all three report running for 1 s.
     The lane stoppers are spring-raised: a lane's box waits at its blade until the program
     energizes the stopper (STP_x_REL_DO) for RELEASE_S, which lets exactly one box through.
  2. Idle at 0 degrees, the deck stopped. Serve a waiting box, alternating lanes when both wait.
  3. Lane A (straight on): release one box and run the deck. The box rides through onto the
     outfeed; the deck is free again when PE_TTA has seen it come and go.
  4. Lane B (from the side): turn the deck to 90; at 90, release one box and run the deck until
     PE_TTB sees it centred; stop the deck and turn back; at 0, run the deck until PE_TTA has seen
     the box come and go.

Training and demo use only. Not a safety-rated program and not a safety-rated simulation.
"""

from openfactory.pyplc import TON

BELTS_OK_DELAY_S = 1.0  # ST: BELTS_OK_DELAY := T#1s
RELEASE_S = 0.5  # ST: RELEASE := T#500ms; the blade is down 0.2 s of it, the box clears it in 0.6 s

IDLE, A_PASS, B_TURN_OUT, B_LOAD, B_TURN_BACK, B_PASS = 0, 10, 20, 21, 22, 23  # ST: STATE values

BELTS_OK_TMR = TON(BELTS_OK_DELAY_S)  # lanes and outfeed running, debounced
REL_A_TMR = TON(RELEASE_S)  # lane A stopper energized (lowered) this long
REL_B_TMR = TON(RELEASE_S)  # lane B stopper energized (lowered) this long
state = IDLE
last_b = False  # the last box served came from lane B
rel_a = False  # lane A release pulse in progress
rel_b = False  # lane B release pulse in progress
seen = False  # PE_TTA has seen the passing box arrive


def step(io, t):
    global state, last_b, rel_a, rel_b, seen

    # 1. Belts run; feed boxes once they have all reported running for BELTS_OK_DELAY_S
    io["CONV_A_RUN_DO"] = True
    io["CONV_B_RUN_DO"] = True
    io["CONV_OUT_RUN_DO"] = True
    ok = BELTS_OK_TMR(io["CONV_A_RUN_DI"] and io["CONV_B_RUN_DI"] and io["CONV_OUT_RUN_DI"], t)
    io["EMT_A_EN_DO"] = ok
    io["EMT_B_EN_DO"] = ok

    # 2. Idle at 0: serve a waiting box, alternating when both lanes wait
    if state == IDLE and io["TT_01_AT0_DI"]:
        if io["PE_A_DI"] and (not io["PE_B_DI"] or last_b):
            state, rel_a, seen, last_b = A_PASS, True, False, False
        elif io["PE_B_DI"]:
            state, last_b = B_TURN_OUT, True

    # 3. Lane A box rides straight through; done when PE_TTA has seen it come and go
    if state == A_PASS:
        if io["PE_TTA_DI"]:
            seen = True
        elif seen:
            state = IDLE

    # 4. Lane B box: turn out, load until centred, turn back, pass through
    if state == B_TURN_OUT and io["TT_01_AT90_DI"]:
        state, rel_b = B_LOAD, True
    if state == B_LOAD and io["PE_TTB_DI"]:
        state = B_TURN_BACK
    if state == B_TURN_BACK and io["TT_01_AT0_DI"]:
        state, seen = B_PASS, False
    if state == B_PASS:
        if io["PE_TTA_DI"]:
            seen = True
        elif seen:
            state = IDLE

    # Release pulses: one box per pulse
    if REL_A_TMR(rel_a, t):
        rel_a = False
    if REL_B_TMR(rel_b, t):
        rel_b = False
    io["STP_A_REL_DO"] = rel_a
    io["STP_B_REL_DO"] = rel_b
    io["TT_01_TURN_DO"] = state in (B_TURN_OUT, B_LOAD)
    io["TT_01_RUN_DO"] = state in (A_PASS, B_LOAD, B_PASS)
