(*
  OpenFactory example PLC program: two-tank-cascade (scenes/two-tank-cascade.yaml), cascade level
  control of two tanks in series (PLAN 3.3), with two of OpenPLC's PID function blocks.
  Target: OpenPLC v3 runtime (IEC 61131-3 Structured Text), talking to the OpenFactory simulator
  as a Modbus TCP Slave Device. Setup: docs/guides/openplc-quickstart.md, then
  docs/guides/two-tank-cascade.md for this scene.

  Training and demo use only. Not a safety-rated program and not a safety-rated simulation.

  The PID block is the one in OpenPLC's compiler library (matiec lib/pid_st.txt); see
  examples/openplc/tank-level-pid.st for its form. Its error is PV - SP, so both gains are
  negative. It has no output limit or anti-windup: LIMIT clamps both outputs here, and the
  interlock holds both blocks in manual. [TBD: verify on the first OpenPLC run] that
  TIME_TO_REAL(T#20ms) is 0.02 (seconds); the gains assume it, as the stand-in script does.

  Sequence of operations
    1. Read both levels: the upper tank raw 0 to 32767 for 0 to 2 m, the lower for 0 to 1.5 m.
    2. Outer loop (LIC_02): PI on the lower tank's level, setpoint 0.8 m; its output is the upper
       tank's level setpoint, limited to 0.05 to 1.6 m.
    3. Inner loop (LIC_01): PI on the upper tank's level against that setpoint; its output is the
       inlet valve's position, limited to 0 to 100 %.
    4. Interlock: while either tank's high level switch is on, the valve is closed and both
       controllers are held in manual (no windup while they are overridden).
    5. For the first second both controllers run in manual at the plant's starting point (the
       valve at 63 %, the upper level at 0.165 m), then switch to automatic bumplessly. After
       that, the outer block's manual value tracks the upper level, so a return from the
       interlock is bumpless too.

  I/O map: Modbus addresses come from the scene's int16_scaled lockfile
  (scenes/two-tank-cascade.int16_scaled.modbus.yaml) and the exported I/O list CSV. OpenPLC
  places a Slave Device's points from address 100 onward, so a zero-based Modbus address N maps
  to %IX100.N, %IW100+N, or %QW100+N.
*)
PROGRAM tank_cascade
  VAR
    (* Discrete inputs from the simulator (Modbus DI:0..2) *)
    TK_01_LSH_DI AT %IX100.0 : BOOL;    (* DI:0 Upper Tank - high level switch (LSH), 1.8 m *)
    TK_02_LSH_DI AT %IX100.1 : BOOL;    (* DI:1 Lower Tank - high level switch (LSH), 1.35 m *)
    SIM_HEARTBEAT_DI AT %IX100.2 : BOOL; (* DI:2 OpenFactory simulator heartbeat, 1 Hz; not used here, watch it for a sim-down watchdog *)
    (* Input registers (Modbus IR:0..2) *)
    FV_01_ZT_AI AT %IW100 : INT;        (* IR:0 Upper Tank Inlet Control Valve - position feedback, 0 to 100 % *)
    TK_01_LT_AI AT %IW101 : INT;        (* IR:1 Upper Tank Level Transmitter, 0 to 32767 = 0 to 2 m *)
    TK_02_LT_AI AT %IW102 : INT;        (* IR:2 Lower Tank Level Transmitter, 0 to 32767 = 0 to 1.5 m *)
    (* Holding registers to the simulator (Modbus HR:0) *)
    FV_01_POS_AO AT %QW100 : INT;       (* HR:0 Upper Tank Inlet Control Valve - position command, 0 to 100 % *)
  END_VAR
  VAR
    SP2 : REAL := 0.8;                  (* lower tank level setpoint, m *)
    KP_OUTER : REAL := -4.0;            (* m of upper setpoint per m of lower error *)
    TR_OUTER : REAL := 8.0;             (* s *)
    KP_INNER : REAL := -400.0;          (* % per m *)
    TR_INNER : REAL := 3.0;             (* s *)
    UPPER_SP_MIN : REAL := 0.05;        (* m *)
    UPPER_SP_MAX : REAL := 1.6;         (* m *)
    START_PCT : REAL := 63.0;           (* valve position at start, % *)
    START_UPPER : REAL := 0.165;        (* upper level at start, m *)
    STARTUP_DELAY : TIME := T#1s;       (* manual until the level readings are live *)
    STARTUP_TMR : TON;
    LIC_02 : PID;                       (* outer: the lower tank *)
    LIC_01 : PID;                       (* inner: the upper tank *)
    UPPER_M : REAL;
    LOWER_M : REAL;
    HIGH : BOOL;
    AUTO_MODE : BOOL;
    X0_OUTER : REAL;
    X0_INNER : REAL;
    UPPER_SP : REAL;
    OUT_PCT : REAL;
  END_VAR

  (* 1. Levels in metres *)
  UPPER_M := INT_TO_REAL(TK_01_LT_AI) * 2.0 / 32767.0;
  LOWER_M := INT_TO_REAL(TK_02_LT_AI) * 1.5 / 32767.0;
  HIGH := TK_01_LSH_DI OR TK_02_LSH_DI;

  (* 5. Manual at the starting point until the startup delay is done *)
  STARTUP_TMR(IN := TRUE, PT := STARTUP_DELAY);
  AUTO_MODE := STARTUP_TMR.Q AND NOT HIGH;
  IF STARTUP_TMR.Q THEN
    X0_OUTER := UPPER_M / KP_OUTER;      (* in manual the block outputs KP * X0 *)
    X0_INNER := 0.0;
  ELSE
    X0_OUTER := START_UPPER / KP_OUTER;
    X0_INNER := START_PCT / KP_INNER;
  END_IF;

  (* 2. Outer loop: the lower level sets the upper level's setpoint *)
  LIC_02(AUTO := AUTO_MODE, PV := LOWER_M, SP := SP2, X0 := X0_OUTER,
         KP := KP_OUTER, TR := TR_OUTER, TD := 0.0, CYCLE := T#20ms);
  UPPER_SP := LIMIT(UPPER_SP_MIN, LIC_02.XOUT, UPPER_SP_MAX);

  (* 3. Inner loop: the upper level positions the valve *)
  LIC_01(AUTO := AUTO_MODE, PV := UPPER_M, SP := UPPER_SP, X0 := X0_INNER,
         KP := KP_INNER, TR := TR_INNER, TD := 0.0, CYCLE := T#20ms);

  (* 3. Limit to the valve's range; 4. the interlock closes it *)
  IF HIGH THEN
    OUT_PCT := 0.0;
  ELSE
    OUT_PCT := LIMIT(0.0, LIC_01.XOUT, 100.0);
  END_IF;
  FV_01_POS_AO := REAL_TO_INT(OUT_PCT * 32767.0 / 100.0);
END_PROGRAM


CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : tank_cascade;
  END_RESOURCE
END_CONFIGURATION
