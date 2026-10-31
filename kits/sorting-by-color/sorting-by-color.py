"""OpenFactory example stand-in PLC for scenes/sorting-by-color.yaml (PLAN 2.7, 2.10).

Run it with no PLC at all:

    uv run openfactory run scenes/sorting-by-color.yaml --script examples/pyplc/sorting-by-color.py

A line-for-line port of examples/openplc/sorting-by-color.st: same sequence, same timers, same tag
names (tests/integration/test_shipped_scenes.py checks the tags agree).

Sequence of operations
  1. Run both belts; enable the box source once both have reported running for 1 s.
  2. On each CS_01_RED_DI rising edge (a red box under the color sensor), start that box's
     EXTEND_DELAY_S timer. When it expires, count the box as on its way to the arm: the arm
     extends while any red box is on its way. The delay lets a blue box already past the sensor
     clear the arm before it swings out (a swinging arm sweeps whatever it reaches).
  3. Each PE_RED rising edge (a red box arriving on the side lane) counts one red box off; the arm
     retracts when none is left on its way.
  Boxes are at least 4 s (2 m) apart, so one pending timer is enough; closer boxes would need a
  ring of timers as in examples/pyplc/sorting-by-height.py.

Training and demo use only. Not a safety-rated program and not a safety-rated simulation.
"""

from openfactory.pyplc import R_TRIG, TON

BELTS_OK_DELAY_S = 1.0  # ST: BELTS_OK_DELAY := T#1s
EXTEND_DELAY_S = 1.5  # ST: EXTEND_DELAY := T#1500ms; the red box is then 0.75 m past the sensor

BELTS_OK_TMR = TON(BELTS_OK_DELAY_S)  # both belts running, debounced
EXTEND_TMR = TON(EXTEND_DELAY_S)  # a red box's travel from the sensor toward the arm
RED_RISE = R_TRIG()  # a red box arrives under the color sensor
LANE_RISE = R_TRIG()  # a red box arrives on the side lane
pending = False  # a red box is between the sensor and the extend point
red_out = 0  # red boxes between the extend point and the side lane


def step(io, t):
    global pending, red_out

    # 1. Belts run; feed boxes once both report running for 1 s
    io["CONV_01_RUN_DO"] = True
    io["CONV_RED_RUN_DO"] = True
    io["EMT_01_EN_DO"] = BELTS_OK_TMR(io["CONV_01_RUN_DI"] and io["CONV_RED_RUN_DI"], t)

    # 2. A red box at the sensor: after EXTEND_DELAY_S it is on its way to the arm
    if RED_RISE(io["CS_01_RED_DI"]):
        pending = True
    if EXTEND_TMR(pending, t):
        pending = False
        red_out += 1

    # 3. A red box on the side lane is done
    if LANE_RISE(io["PE_RED_DI"]) and red_out > 0:
        red_out -= 1
    io["DIV_01_EXT_DO"] = red_out > 0
