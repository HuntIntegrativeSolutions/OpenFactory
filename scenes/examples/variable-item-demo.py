"""Stand-in PLC for scenes/examples/variable-item-demo.yaml (PLAN 5.2c): run the box line; start
both variable items at 1 s while their selectors are in auto, MX_01 at 60 % and MX_02 at 80 %,
and stop MX_01 at 30 s. Each item's running and speed feedback come from the variable_item
composite, not from here; the script only watches them: a fail-to-start alarm when an item is
commanded for 5 s without running, or running with its speed feedback 10 % off the command, and
a note when a selector is in hand."""

from openfactory.pyplc import TON

fail_1, fail_2 = TON(5.0), TON(5.0)
alarms = {"MX_01": False, "MX_02": False}
in_hand = {"MX_01": False, "MX_02": False}


def step(io, t):
    io["CONV_01_RUN_DO"] = True
    io["EMT_01_EN_DO"] = True

    run_1 = io["MX_01_AUTO_DI"] and 1.0 <= t < 30.0
    io["MX_01_RUN_DO"] = run_1
    io["MX_01_SPD_AO"] = 60.0
    off_1 = io["MX_01_RUN_DI"] and abs(io["MX_01_SPD_AI"] - 60.0) > 10.0
    alarms["MX_01"] = fail_1((run_1 and not io["MX_01_RUN_DI"]) or off_1, t)
    in_hand["MX_01"] = io["MX_01_HAND_DI"]

    run_2 = io["MX_02_AUTO_DI"] and t >= 1.0
    io["MX_02_RUN_DO"] = run_2
    io["MX_02_SPD_AO"] = 80.0
    off_2 = io["MX_02_RUN_DI"] and abs(io["MX_02_SPD_AI"] - 80.0) > 10.0
    alarms["MX_02"] = fail_2((run_2 and not io["MX_02_RUN_DI"]) or off_2, t)
    in_hand["MX_02"] = io["MX_02_HAND_DI"]
