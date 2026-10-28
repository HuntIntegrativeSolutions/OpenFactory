"""OpenFactory example stand-in PLC for scenes/conveyor-start-stop.yaml (PLAN 2.7, 2.10).

Run it with no PLC at all:

    uv run openfactory run scenes/conveyor-start-stop.yaml \
        --script examples/pyplc/conveyor-start-stop.py

A line-for-line port of examples/openplc/conveyor-start-stop.st: same sequence, same timer, same
tag names (tests/integration/test_shipped_scenes.py checks the tags and timers agree).

Sequence of operations
  1. Turning SS_01 to RUN starts the line: CONV_01 runs and stays running (seal-in) while SS_01
     stays in RUN and PB_STOP is not pressed. The scene starts with SS_01 in RUN.
  2. PB_STOP is normally closed (PB_STOP_DI TRUE while released); pressing it drops the seal-in.
     Turn SS_01 to OFF and back to RUN to restart.
  3. A selector reading OFF and RUN at once (a wiring fault) stops the line too.
  4. Once CONV_01 has reported running for 1 s, enable the box emitter EMT_01.
  5. PL_RUN shows the running feedback, not the command.

Training and demo use only. Not a safety-rated program and not a safety-rated simulation.
"""

from openfactory.pyplc import R_TRIG, TON

BELT_OK_DELAY_S = 1.0  # ST: BELT_OK_DELAY := T#1s

BELT_OK_TMR = TON(BELT_OK_DELAY_S)  # belt running, debounced
SS_RUN_RISE = R_TRIG()  # selector turned to RUN (also the first scan in RUN)
run_latch = False  # the line is started (seal-in)


def step(io, t):
    global run_latch

    # 1-3. Start on a turn to RUN, seal in, drop on stop, OFF, or a selector wiring fault
    run_ok = io["SS_01_RUN_DI"] and not io["SS_01_OFF_DI"] and io["PB_STOP_DI"]
    run_latch = (SS_RUN_RISE(io["SS_01_RUN_DI"]) or run_latch) and run_ok
    io["CONV_01_RUN_DO"] = run_latch

    # 4. Feed boxes only after the belt has reported running for BELT_OK_DELAY_S
    io["EMT_01_EN_DO"] = BELT_OK_TMR(run_latch and io["CONV_01_RUN_DI"], t)

    # 5. Running light from feedback
    io["PL_RUN_DO"] = io["CONV_01_RUN_DI"]
