"""Stand-in PLC for tests/scenes/counter-display.yaml (PLAN 2.5b): run the belt and emitter, count
rising edges of the photo eye, and write the count to the counter display."""

from openfactory.pyplc import R_TRIG

edge = R_TRIG()
count = 0


def step(io, t):
    global count
    io["CONV_01_RUN_DO"] = True
    io["EMT_01_EN_DO"] = True
    if edge(io["PE_01_DI"]):
        count += 1
    io["CNT_01_VAL_AO"] = float(count)
