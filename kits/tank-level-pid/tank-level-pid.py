"""OpenFactory example stand-in PLC for scenes/tank-level-pid.yaml (PLAN 3.3, 2.10).

Run it with no PLC at all:

    uv run openfactory run scenes/tank-level-pid.yaml --script examples/pyplc/tank-level-pid.py

A line-for-line port of examples/openplc/tank-level-pid.st, including OpenPLC's PID function
block (its compiler's standard library, matiec lib/pid_st.txt and integral_st.txt): the error is
PV − SP, so a level loop that opens the valve on a low level has a negative gain; the integral
is a rectangular sum over the 20 ms task cycle; the output is clamped to the valve's 0 to 100 %.

Sequence of operations
  1. Read the level from the transmitter, 0 to 2 m.
  2. PI control: the valve position is KP·(e + Σe·dt / TR) with e = level − setpoint 1.0 m.
  3. The output is limited to 0 to 100 % and written to the valve.
  4. Interlock: while the high level switch (LSH) is on, the valve is closed and the controller
     is held in manual at 0 % (no windup while it is overridden).
  5. For the first second the PID runs in manual at the valve's starting position (66 %),
     tracking the level, then switches to automatic bumplessly: the transmitter reads 0 until its
     first update, and starting in automatic on that would slam the valve shut.

Training and demo use only. Not a safety-rated program and not a safety-rated simulation.
"""

from openfactory.pyplc import TON

SETPOINT_M = 1.0  # ST: SP := 1.0
KP = -400.0  # % per m; negative: the PID's error is PV - SP (reverse acting)
TR_S = 6.0  # reset time, s
START_PCT = 66.0  # the valve position the scene starts at (FV_01_POS_AO's initial value)
STARTUP_S = 1.0  # ST: STARTUP_DELAY := T#1s, manual until the level reading is live
CYCLE_S = 0.02  # the OpenPLC task interval, T#20ms


class PID:
    """matiec's PID with D = 0: ITERM integrates the error (rectangular), reset to
    TR·(X0 − error) in manual so XOUT = KP·X0 there."""

    def __init__(self) -> None:
        self.iterm = 0.0

    def __call__(self, auto: bool, pv: float, sp: float, x0: float, kp: float, tr: float) -> float:
        error = pv - sp
        if not auto:
            self.iterm = tr * (x0 - error)
        else:
            self.iterm += error * CYCLE_S
        return kp * (error + self.iterm / tr)


LIC = PID()
STARTUP_TMR = TON(STARTUP_S)  # the level reading is live


def step(io, t):
    high = io["TK_01_LSH_DI"]
    level = io["TK_01_LT_AI"]
    # 5. Manual at the starting position until the startup delay is done, then automatic
    started = STARTUP_TMR(True, t)
    # 2. PI on the level; 4. held in manual at 0 % while the high switch overrides it
    x0 = START_PCT / KP if not started else 0.0
    out = LIC(started and not high, level, SETPOINT_M, x0, KP, TR_S)
    # 3. Limit to the valve's range; 4. the interlock closes it
    io["FV_01_POS_AO"] = 0.0 if high else min(max(out, 0.0), 100.0)
