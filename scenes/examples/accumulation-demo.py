"""Stand-in PLC for scenes/examples/accumulation-demo.yaml (PLAN 2.5b): feed boxes until four have
entered zone 1, then stop the infeed; once all four zone eyes have been blocked for 2 s, release.
The zone logic (which zone runs) is inside the accumulation conveyor, not here."""

from openfactory.pyplc import R_TRIG, TON

entered = R_TRIG()
full = TON(2.0)
count = 0
release = False


def step(io, t):
    global count, release
    if entered(io["ACC_Z1_DI"]):
        count += 1
    feeding = count < 4
    io["EMT_01_EN_DO"] = feeding
    io["CONV_IN_RUN_DO"] = feeding
    zones = [io[f"ACC_Z{n}_DI"] for n in range(1, 5)]
    if full(all(zones), t):
        release = True
    io["ACC_REL_DO"] = release
