"""OpenFactory example stand-in PLC for scenes/palletizer-lite.yaml (PLAN 2.7, 2.10).

Run it with no PLC at all:

    uv run openfactory run scenes/palletizer-lite.yaml --script examples/pyplc/palletizer-lite.py

A line-for-line port of examples/openplc/palletizer-lite.st: same steps, same timers, same tag
names (tests/integration/test_shipped_scenes.py checks the tags agree).

Sequence of operations
  1. Run both belts and hold both stoppers raised; enable the box source once both belts have
     reported running for 1 s. Boxes queue at STP_IN, the first one under the gantry.
  2. Pick and place, one box per cycle (the gantry has no raised switch, so each rise is timed):
     with a box at PE_PICK, the gantry at the pick side, fewer than LAYER_BOXES placed in this
     layer, and no layer leaving: lower, grip, rise, traverse, lower, release, rise, return.
     Count each box placed. The layer-forming belt carries it to the row queueing at STP_LYR.
  3. A layer is complete when LAYER_BOXES have been placed and PE_ROW (under the fourth queued
     box) has been blocked for 1 s, which a passing box (0.6 s in the beam) cannot do. Lower
     STP_LYR for LAYER_OUT_S to send the layer to the discharge, raise it, and start the count
     again.

Training and demo use only. Not a safety-rated program and not a safety-rated simulation.
"""

from openfactory.pyplc import TON

LAYER_BOXES = 4  # ST: LAYER_BOXES : INT := 4
BELTS_OK_DELAY_S = 1.0  # ST: BELTS_OK_DELAY := T#1s
RISE_S = 0.4  # ST: RISE := T#400ms; lower_time_s 0.3 plus margin
SETTLE_S = 0.2  # ST: SETTLE := T#200ms; the released box drops onto the belt
ROW_DELAY_S = 1.0  # ST: ROW_DELAY := T#1s
LAYER_OUT_S = 3.0  # ST: LAYER_OUT := T#3s; four boxes (1.2 m) clear the blade at 0.5 m/s

# ST: PICK_STEP values
WAIT, DOWN, GRIP, UP, ACROSS, PLACE, RELEASE, UP2, BACK = 0, 1, 2, 3, 4, 5, 6, 7, 8

BELTS_OK_TMR = TON(BELTS_OK_DELAY_S)  # both belts running, debounced
RISE_TMR = TON(RISE_S)  # the pad has had time to rise
SETTLE_TMR = TON(SETTLE_S)  # the released box has settled on the belt
ROW_TMR = TON(ROW_DELAY_S)  # a box stands under PE_ROW: the row is complete
LAYER_TMR = TON(LAYER_OUT_S)  # the layer has had time to leave
step_no = WAIT  # pick-and-place step
layer_out = False  # a layer is leaving
placed = 0  # boxes placed in the layer being formed


def step(io, t):
    global step_no, layer_out, placed

    # 1. Belts run; stoppers hold; feed boxes once both belts report running for 1 s
    io["CONV_IN_RUN_DO"] = True
    io["CONV_LYR_RUN_DO"] = True
    io["STP_IN_RAISE_DO"] = True
    io["EMT_01_EN_DO"] = BELTS_OK_TMR(io["CONV_IN_RUN_DI"] and io["CONV_LYR_RUN_DI"], t)

    # 2. Pick and place, one box per cycle
    room = placed < LAYER_BOXES and not layer_out
    if step_no == WAIT and io["PE_PICK_DI"] and io["PP_01_AT_PICK_DI"] and room:
        step_no = DOWN
    elif step_no == DOWN and io["PP_01_DOWN_DI"]:
        step_no = GRIP
    elif step_no == GRIP and io["PP_01_HOLD_DI"]:
        step_no = UP
    elif step_no == UP and RISE_TMR(True, t):
        step_no = ACROSS
    elif step_no == ACROSS and io["PP_01_AT_PLACE_DI"]:
        step_no = PLACE
    elif step_no == PLACE and io["PP_01_DOWN_DI"]:
        step_no = RELEASE
    elif step_no == RELEASE and SETTLE_TMR(not io["PP_01_HOLD_DI"], t):
        step_no = UP2
        placed += 1
    elif step_no == UP2 and RISE_TMR(True, t):
        step_no = BACK
    elif step_no == BACK and io["PP_01_AT_PICK_DI"]:
        step_no = WAIT
    if step_no not in (UP, UP2):
        RISE_TMR(False, t)
    if step_no != RELEASE:
        SETTLE_TMR(False, t)
    io["PP_01_LOWER_DO"] = step_no in (DOWN, GRIP, PLACE, RELEASE)
    io["PP_01_GRIP_DO"] = step_no in (GRIP, UP, ACROSS, PLACE)
    io["PP_01_TRAV_DO"] = step_no in (ACROSS, PLACE, RELEASE, UP2)

    # 3. A complete row leaves as one layer
    if ROW_TMR(io["PE_ROW_DI"], t) and placed >= LAYER_BOXES and not layer_out:
        layer_out = True
    if LAYER_TMR(layer_out, t):
        layer_out = False
        placed = 0
    io["STP_LYR_RAISE_DO"] = not layer_out
