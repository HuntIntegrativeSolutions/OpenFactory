"""Stand-in PLC for tests/scenes/demo-pick-place.yaml (PLAN 2.10 script, PLAN 2.5 demo).

Sequence: with a box at the stopper (PE_A), lower, grip, raise, traverse to the place side, lower,
release, raise, and return. Each raise is timed (the gantry has no raised switch). Training and
demo use only. Not a safety-rated program.
"""

from openfactory.pyplc import TON

RAISE_S = 0.4  # lower_time_s 0.3 plus margin
SETTLE_S = 0.2
state = "wait"  # wait, down, grip, up, across, place, release, up2, back
timer = TON(RAISE_S)
settle = TON(SETTLE_S)


def step(io, t):
    global state
    io["CONV_A_RUN_DO"] = True
    io["CONV_B_RUN_DO"] = True
    io["STP_A_RAISE_DO"] = True
    io["EMT_01_EN_DO"] = True
    if state == "wait" and io["PE_A_DI"] and io["PP_01_AT_PICK_DI"]:
        state = "down"
    elif state == "down" and io["PP_01_DOWN_DI"]:
        state = "grip"
    elif state == "grip" and io["PP_01_HOLD_DI"]:
        state = "up"
    elif state == "up" and timer(True, t):
        state = "across"
    elif state == "across" and io["PP_01_AT_PLACE_DI"]:
        state = "place"
    elif state == "place" and io["PP_01_DOWN_DI"]:
        state = "release"
    elif state == "release" and settle(not io["PP_01_HOLD_DI"], t):
        state = "up2"
    elif state == "up2" and timer(True, t):
        state = "back"
    elif state == "back" and io["PP_01_AT_PICK_DI"]:
        state = "wait"
    if state not in ("up", "up2"):
        timer(False, t)
    if state != "release":
        settle(False, t)
    io["PP_01_LOWER_DO"] = state in ("down", "grip", "place", "release")
    io["PP_01_GRIP_DO"] = state in ("grip", "up", "across", "place")
    io["PP_01_TRAV_DO"] = state in ("across", "place", "release", "up2")
