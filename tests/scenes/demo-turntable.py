"""Stand-in PLC for tests/scenes/demo-turntable.yaml (PLAN 2.10 script, PLAN 2.5 demo).

Every other box goes to the side. Sequence for a side box: when PE_TT sees it centred on the deck,
stop the infeed and the deck and turn to 90; at 90, run the deck for 2.5 s to discharge it onto
CONV_SIDE; then stop the deck and turn back; at 0, resume. Straight boxes ride through at 0.
Training and demo use only. Not a safety-rated program.
"""

from openfactory.pyplc import R_TRIG, TON

DISCHARGE_S = 2.5
PE_RISE = R_TRIG()
DISCHARGE_TMR = TON(DISCHARGE_S)
state = "pass"  # pass | turn_out | discharge | turn_back
count = 0


def step(io, t):
    global state, count
    io["EMT_01_EN_DO"] = True
    io["CONV_OUT_RUN_DO"] = True
    io["CONV_SIDE_RUN_DO"] = True
    if state == "pass" and PE_RISE(io["PE_TT_DI"]):
        count += 1
        if count % 2 == 0:
            state = "turn_out"
    if state == "turn_out" and io["TT_01_AT90_DI"]:
        state = "discharge"
    if DISCHARGE_TMR(state == "discharge", t):
        state = "turn_back"
    if state == "turn_back" and io["TT_01_AT0_DI"]:
        state = "pass"
    io["TT_01_TURN_DO"] = state in ("turn_out", "discharge")
    io["TT_01_RUN_DO"] = state in ("pass", "discharge")
    io["CONV_IN_RUN_DO"] = state == "pass"
