"""OpenFactory example stand-in PLC for scenes/two-tank-cascade.yaml (PLAN 3.3, 2.10).

Run it with no PLC at all:

    uv run openfactory run scenes/two-tank-cascade.yaml \
        --script examples/pyplc/two-tank-cascade.py

A line-for-line port of examples/openplc/two-tank-cascade.st, with OpenPLC's PID function block
ported as in examples/pyplc/tank-level-pid.py (error PV − SP, so the gains are negative;
rectangular integral over the 20 ms task).

Sequence of operations
  1. Read both levels from their transmitters: the upper tank 0 to 2 m, the lower 0 to 1.5 m.
  2. Outer loop (LIC_02): PI on the lower tank's level, setpoint 0.8 m; its output is the upper
     tank's level setpoint, limited to 0.05 to 1.6 m.
  3. Inner loop (LIC_01): PI on the upper tank's level against that setpoint; its output is the
     inlet valve's position, limited to 0 to 100 %.
  4. Interlock: while either tank's high level switch is on, the valve is closed and both
     controllers are held in manual (no windup while they are overridden).
  5. For the first second both controllers run in manual at the plant's starting point (the
     valve at 63 %, the upper level at 0.165 m), then switch to automatic bumplessly. After
     that, the outer controller's manual value tracks the upper level, so a return from the
     interlock is bumpless too.

Training and demo use only. Not a safety-rated program and not a safety-rated simulation.
"""

from openfactory.pyplc import TON

SP_LOWER_M = 0.8  # ST: SP2 := 0.8
KP_OUTER = -4.0  # m of upper setpoint per m of lower error (negative: error is PV - SP)
TR_OUTER_S = 8.0
KP_INNER = -400.0  # % per m
TR_INNER_S = 3.0
UPPER_SP_MIN, UPPER_SP_MAX = 0.05, 1.6  # m
START_PCT, START_UPPER_M = 63.0, 0.165  # the plant's starting point
STARTUP_S = 1.0  # ST: STARTUP_DELAY := T#1s
CYCLE_S = 0.02  # the OpenPLC task interval, T#20ms


class PID:
    """OpenPLC's PID with D = 0 (matiec lib/pid_st.txt), as in tank-level-pid.py."""

    def __init__(self) -> None:
        self.iterm = 0.0

    def __call__(self, auto: bool, pv: float, sp: float, x0: float, kp: float, tr: float) -> float:
        error = pv - sp
        if not auto:
            self.iterm = tr * (x0 - error)
        else:
            self.iterm += error * CYCLE_S
        return kp * (error + self.iterm / tr)


LIC_02 = PID()  # outer: the lower tank
LIC_01 = PID()  # inner: the upper tank
STARTUP_TMR = TON(STARTUP_S)


def step(io, t):
    high = io["TK_01_LSH_DI"] or io["TK_02_LSH_DI"]
    upper, lower = io["TK_01_LT_AI"], io["TK_02_LT_AI"]
    # 5. Manual at the starting point until the startup delay is done
    started = STARTUP_TMR(True, t)
    auto = started and not high
    # 2. Outer loop: the lower level sets the upper level's setpoint
    x0_outer = START_UPPER_M / KP_OUTER if not started else upper / KP_OUTER
    upper_sp = LIC_02(auto, lower, SP_LOWER_M, x0_outer, KP_OUTER, TR_OUTER_S)
    upper_sp = min(max(upper_sp, UPPER_SP_MIN), UPPER_SP_MAX)
    # 3. Inner loop: the upper level positions the valve
    x0_inner = START_PCT / KP_INNER if not started else 0.0
    out = LIC_01(auto, upper, upper_sp, x0_inner, KP_INNER, TR_INNER_S)
    # 3. Limit to the valve's range; 4. the interlock closes it
    io["FV_01_POS_AO"] = 0.0 if high else min(max(out, 0.0), 100.0)
