"""Stand-in PLC for tests/scenes/demo-reversing.yaml (PLAN 2.5b): count boxes at PE_B; send odd
ones on, and for even ones run both belts in reverse for 8 s (no new boxes meanwhile), long
enough for the box to travel back across the seam and off the infeed end."""

from openfactory.pyplc import R_TRIG, TON

edge = R_TRIG()
back = TON(8.0)
count = 0
reversing = False


def step(io, t):
    global count, reversing
    io["CONV_A_RUN_DO"] = True
    io["CONV_B_RUN_DO"] = True
    if not reversing and edge(io["PE_B_DI"]):
        count += 1
        reversing = count % 2 == 0
    if reversing and back(True, t):
        back(False, t)
        reversing = False
    io["CONV_A_REV_DO"] = reversing
    io["CONV_B_REV_DO"] = reversing
    io["EMT_01_EN_DO"] = not reversing
