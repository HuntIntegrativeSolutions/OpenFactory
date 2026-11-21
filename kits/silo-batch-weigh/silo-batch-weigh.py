"""OpenFactory example stand-in PLC for scenes/silo-batch-weigh.yaml (PLAN 5.7a, 2.10).

Run it with no PLC at all:

    uv run openfactory run scenes/silo-batch-weigh.yaml \
        --script examples/pyplc/silo-batch-weigh.py

Sequence of operations
  Silo refill, on its own: the fill gate SG_01 opens when the silo's net weight falls below
  REFILL_ON_KG and closes above REFILL_OFF_KG, so the silo is never empty and never full.

  Batching, a loop:
  1. FAST: the screw SC_01 runs and the airlock RV_01 turns at full speed until the hopper's net
     weight reaches TARGET_KG - FAST_PREACT_KG. The screw is fed faster than it carries, so it
     runs full: what is on it then (its capacity times its travel time) is still to come.
  2. SLOW: the airlock turns at SLOW_PCT until the net weight reaches TARGET_KG - SLOW_PREACT_KG,
     what the screw carries at that rate in its travel time (the in-flight allowance).
  3. EMPTYING: the airlock stops; the screw runs on for its travel time and a margin, so what
     was on it reaches the hopper, then stops.
  4. DISCHARGE: the hopper's gate SG_02 opens and stays open until the hopper is empty, then
     closes. When its closed limit switch is made the next batch starts.
  The airlock never runs unless the screw is running and not plugged.

Training and demo use only. Not a safety-rated program and not a safety-rated simulation.
"""

SILO_TARE_KG = 1800.0
HOPPER_TARE_KG = 180.0
REFILL_ON_KG = 3500.0
REFILL_OFF_KG = 4500.0
TARGET_KG = 250.0
FAST_PREACT_KG = 65.0  # the screw's load running full (10 kg/s x 5 s = 50 kg) and a margin
SLOW_PCT = 20.0  # 12 kg/s x 20 % = 2.4 kg/s
SLOW_PREACT_KG = 12.0  # 2.4 kg/s x 5 s on the screw
SCREW_RUN_ON_S = 6.0  # its travel time, 5 s, and a margin
EMPTY_KG = 0.5

refill = False
state = "FAST"
since = 0.0


def step(io, t):
    global refill, state, since

    silo = io["SILO_01_WT_AI"] - SILO_TARE_KG
    refill = (silo < REFILL_ON_KG or refill) and silo < REFILL_OFF_KG
    io["SG_01_OPEN_DO"] = refill

    net = io["WH_01_WT_AI"] - HOPPER_TARE_KG
    screw_ok = io["SC_01_RUN_DI"] and not io["SC_01_PLUG_DI"]
    if state == "FAST" and net >= TARGET_KG - FAST_PREACT_KG:
        state = "SLOW"
    elif state == "SLOW" and net >= TARGET_KG - SLOW_PREACT_KG:
        state, since = "EMPTYING", t
    elif state == "EMPTYING" and t - since >= SCREW_RUN_ON_S:
        state = "DISCHARGE"
    elif state == "DISCHARGE" and net <= EMPTY_KG:
        state = "CLOSING"
    elif state == "CLOSING" and io["SG_02_ZSC_DI"]:
        state = "FAST"

    io["SC_01_RUN_DO"] = state in ("FAST", "SLOW", "EMPTYING")
    feeding = state in ("FAST", "SLOW") and screw_ok
    io["RV_01_RUN_DO"] = feeding
    io["RV_01_SPD_AO"] = 100.0 if state == "FAST" else SLOW_PCT
    io["SG_02_OPEN_DO"] = state == "DISCHARGE"
