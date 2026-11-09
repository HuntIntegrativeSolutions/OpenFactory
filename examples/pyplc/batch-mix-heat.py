"""OpenFactory example stand-in PLC for scenes/batch-mix-heat.yaml (PLAN 3.3, 2.10).

Run it with no PLC at all:

    uv run openfactory run scenes/batch-mix-heat.yaml --script examples/pyplc/batch-mix-heat.py

A line-for-line port of examples/openplc/batch-mix-heat.st: same steps, same tag names.

Sequence of operations (one batch, then held)
  0. Idle: wait 1 s after power-up for the transmitters' first readings, then start a batch if
     the mixing tank is empty (level below 0.02 m).
  10. Fill water: open XV_01 until the level reaches 0.10 m.
  20. Fill syrup: open XV_02 until the level reaches 0.16 m (the recipe: 0.06 m of syrup on 0.10
      m of water, 37.5 % syrup by volume; each valve's 1 s close adds a little).
  30. Heat: the heater runs until the temperature reaches 50 °C.
  40. Hold: the batch is ready (PL_READY_DO lit); the heater keeps it at 50 °C, on below
      49.5 °C and off at 50 °C.
  Interlocks, in every step: the high level switch (LSH) closes both inlet valves, and the
  heater runs only while the level covers it (0.10 m and above).

Training and demo use only. Not a safety-rated program and not a safety-rated simulation.
"""

from openfactory.pyplc import TON

WATER_SP_M = 0.10  # ST: WATER_SP, the level at which the water fill ends
BATCH_SP_M = 0.16  # ST: BATCH_SP, the level at which the syrup fill ends
EMPTY_M = 0.02  # ST: EMPTY_LVL, a tank below this is empty and can start a batch
TEMP_SP_C = 50.0  # ST: TEMP_SP
HOLD_BAND_C = 0.5  # ST: HOLD_DB, the heater restarts this far below the setpoint in the hold
COVER_M = 0.10  # ST: COVER_LVL, the heater's low-level permissive
STARTUP_S = 1.0  # ST: STARTUP_DELAY := T#1s

STARTUP_TMR = TON(STARTUP_S)
state = {"step": 0, "heat": False}


def step(io, t):
    level, temp, high = io["TK_01_LT_AI"], io["TK_01_TT_AI"], io["TK_01_LSH_DI"]
    s = state["step"]
    # Transitions, one per scan
    if s == 0 and STARTUP_TMR(True, t) and level < EMPTY_M:
        s = 10
    elif s == 10 and level >= WATER_SP_M:
        s = 20
    elif s == 20 and level >= BATCH_SP_M:
        s = 30
    elif s == 30 and temp >= TEMP_SP_C:
        s = 40
    state["step"] = s
    # Actions; the heater's hold is a thermostat with a deadband
    if s == 30:
        state["heat"] = True
    elif s == 40:
        if temp >= TEMP_SP_C:
            state["heat"] = False
        elif temp < TEMP_SP_C - HOLD_BAND_C:
            state["heat"] = True
    else:
        state["heat"] = False
    io["XV_01_OPEN_DO"] = s == 10 and not high
    io["XV_02_OPEN_DO"] = s == 20 and not high
    io["HTR_01_ON_DO"] = state["heat"] and level >= COVER_M
    io["PL_READY_DO"] = s == 40
