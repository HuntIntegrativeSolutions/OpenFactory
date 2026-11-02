"""Stand-in PLC for tests/scenes/demo-lift.yaml (PLAN 2.5b): at the bottom, run the deck until a
box is on it (the eye near its discharge end), then raise; at the top, run the deck until the box
has left (the eye at the bottom no longer sees it once raised, so a timer gives the hand-off
time), then lower."""

from openfactory.pyplc import TON

state = "load"
unload = TON(2.5)  # long enough for a 0.3 m box to leave a 1 m deck at 0.5 m/s


def step(io, t):
    global state
    io["EMT_01_EN_DO"] = True
    io["CONV_OUT_RUN_DO"] = True
    if state == "load":
        io["LFT_01_RAISE_DO"] = False
        loaded = io["PE_LIFT_DI"]
        io["CONV_IN_RUN_DO"] = not loaded
        io["LFT_01_RUN_DO"] = io["LFT_01_BOT_DI"] and not loaded
        if loaded and io["LFT_01_BOT_DI"]:
            state = "up"
    elif state == "up":
        io["CONV_IN_RUN_DO"] = False
        io["LFT_01_RUN_DO"] = False
        io["LFT_01_RAISE_DO"] = True
        if io["LFT_01_TOP_DI"]:
            state = "unload"
    elif state == "unload":
        io["LFT_01_RUN_DO"] = True
        if unload(True, t):
            unload(False, t)
            state = "down"
    else:  # down
        io["LFT_01_RUN_DO"] = False
        io["LFT_01_RAISE_DO"] = False
        if io["LFT_01_BOT_DI"]:
            state = "load"
