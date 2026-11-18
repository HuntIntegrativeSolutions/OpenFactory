"""OpenFactory stand-in PLC for scenes/plants/wash-plant/wash-plant.yaml (PLAN 5.4d).

One script for the plant and for each area run alone (``--area LOAD`` or ``--area WASH``): a
tag of the other area is absent from an area run, so every section first asks whether its
tags are there (``"TAG" in io``).

Sequence of operations
  LOAD: 1. Start the dust extraction fan FAN_101 when its selector is in AUTO (in HAND it runs
           without the PLC).
        2. Run the infeed belt CV_101 while the fan runs and, in the plant, while the wash belt
           CV_201 runs (so cartons are never pushed onto a stopped belt).
        3. Once CV_101 has reported running for 1 s, enable the carton source EMT_101.
  WASH: 4. Start the vapour fan FAN_201 in AUTO, as FAN_101; run the spray pump P_201 while the
           fan runs and the tank holds water above 0.15 m (no dry running).
        5. Run the wash belt CV_201 while the spray pump runs.
        6. Open the make-up valve XV_201 at the low level switch and close it at the high one.

Training and demo use only. Not a safety-rated program and not a safety-rated simulation.
"""

from openfactory.pyplc import TON

BELT_OK_DELAY_S = 1.0
DRY_LEVEL_M = 0.15

BELT_OK_TMR = TON(BELT_OK_DELAY_S)
filling = False


def step(io, t):
    global filling

    if "CV_101_RUN_DO" in io:  # the LOAD area is here
        io["FAN_101_START_DO"] = io["FAN_101_AUTO_DI"] and not io["FAN_101_HAND_DI"]  # 1
        downstream = True  # 2: alone, nothing downstream to wait for
        if "CV_201_RUN_DI" in io:  # in the plant, the wash belt must run
            downstream = io["CV_201_RUN_DI"]
        io["CV_101_RUN_DO"] = io["FAN_101_RUN_DI"] and downstream
        io["EMT_101_EN_DO"] = BELT_OK_TMR(io["CV_101_RUN_DI"], t)  # 3

    if "CV_201_RUN_DO" in io:  # the WASH area is here
        io["FAN_201_START_DO"] = io["FAN_201_AUTO_DI"] and not io["FAN_201_HAND_DI"]  # 4
        io["P_201_RUN_DO"] = io["FAN_201_RUN_DI"] and io["TK_201_LVL_AI"] > DRY_LEVEL_M
        io["CV_201_RUN_DO"] = io["P_201_RUN_DI"]  # 5
        if io["TK_201_LSL_DI"]:  # 6
            filling = True
        if io["TK_201_LSH_DI"]:
            filling = False
        io["XV_201_OPEN_DO"] = filling
