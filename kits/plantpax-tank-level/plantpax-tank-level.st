(*
  OpenFactory example PLC program: plantpax-tank-level (scenes/plantpax-tank-level.yaml), the
  PlantPAx demo plant of PLAN 3.4 controlled without PlantPAx: tank-level-pid's level loop closed
  with OpenPLC's PID function block, plus the transfer pump. The PlantPAx version of the same
  control is the L5X from `openfactory export ... --format plantpax` (docs/plantpax.md).
  Target: OpenPLC v3 runtime (IEC 61131-3 Structured Text), talking to the OpenFactory simulator
  as a Modbus TCP Slave Device. Setup: docs/guides/openplc-quickstart.md, then
  docs/guides/plantpax-tank-level.md for this scene.

  Training and demo use only. Not a safety-rated program and not a safety-rated simulation.

  The PID block is the one in OpenPLC's compiler library (matiec lib/pid_st.txt, from the IEC
  61131-3 2nd edition draft): XOUT := KP * (ERROR + ITERM.XOUT / TR + DTERM.XOUT * TD) with
  ERROR := PV - SP. The error is PV minus SP, so a loop that opens the inlet valve on a low level
  needs a negative KP. Its integral (lib/integral_st.txt) is a rectangular sum of ERROR times
  TIME_TO_REAL(CYCLE). It has no output limit and no anti-windup: LIMIT clamps the output here,
  and the interlock holds the block in manual so it cannot wind up while overridden.
  [TBD: verify on the first OpenPLC run] that TIME_TO_REAL(T#20ms) is 0.02 (seconds) in the
  runtime; the gains below assume seconds, as the stand-in script does.

  Sequence of operations
    1. Read the level from the transmitter, raw 0 to 32767 for 0 to 2 m.
    2. PI control: the valve position is KP * (e + sum(e * 0.02 s) / TR) with e = level - 1.0 m.
    3. The output is limited to 0 to 100 % and written to the valve, raw 0 to 32767.
    4. Interlock: while the high level switch (LSH) is on, the valve is closed and the PID is
       held in manual at 0 % (no windup while it is overridden).
    5. For the first second the PID runs in manual at the valve's starting position (66 %),
       tracking the level, then switches to automatic bumplessly: the level input reads 0 until
       its first poll, and starting in automatic on that would slam the valve shut.
    6. The transfer pump P_01 starts 20 s after power-up (the demo's load step) and runs while
       the low level switch (LSL) is off: LSL is its interlock against running the tank dry.

  I/O map: Modbus addresses come from the scene's int16_scaled lockfile
  (scenes/plantpax-tank-level.int16_scaled.modbus.yaml) and the exported I/O list CSV. OpenPLC places
  a Slave Device's points from address 100 onward, so a zero-based Modbus address N maps to
  %IX100.N, %IW100+N, or %QW100+N.
*)
PROGRAM plantpax_tank
  VAR
    (* Discrete inputs from the simulator (Modbus DI:0..3) *)
    P_01_RUN_DI AT %IX100.0 : BOOL;     (* DI:0 Level Control Tank Transfer Pump - running feedback *)
    TK_01_LSH_DI AT %IX100.1 : BOOL;    (* DI:1 Level Control Tank - high level switch (LSH), 1.8 m *)
    TK_01_LSL_DI AT %IX100.2 : BOOL;    (* DI:2 Level Control Tank - low level switch (LSL), 0.3 m *)
    SIM_HEARTBEAT_DI AT %IX100.3 : BOOL; (* DI:3 OpenFactory simulator heartbeat, 1 Hz; not used here, watch it for a sim-down watchdog *)
    (* Coils to the simulator (Modbus CO:0) *)
    P_01_RUN_DO AT %QX100.0 : BOOL;     (* CO:0 Level Control Tank Transfer Pump - run command *)
    (* Input registers (Modbus IR:0..1) *)
    FV_01_ZT_AI AT %IW100 : INT;        (* IR:0 Tank Inlet Level Control Valve - position feedback, 0 to 32767 = 0 to 100 % *)
    TK_01_LT_AI AT %IW101 : INT;        (* IR:1 Level Control Tank Level Transmitter, 0 to 32767 = 0 to 2 m *)
    (* Holding registers to the simulator (Modbus HR:0) *)
    FV_01_POS_AO AT %QW100 : INT;       (* HR:0 Tank Inlet Level Control Valve - position command, 0 to 32767 = 0 to 100 % *)
  END_VAR
  VAR
    SP : REAL := 1.0;                   (* level setpoint, m *)
    KP : REAL := -400.0;                (* % per m; negative: the PID's error is PV - SP *)
    TR : REAL := 6.0;                   (* reset time, s *)
    START_PCT : REAL := 66.0;           (* valve position at start, % *)
    STARTUP_DELAY : TIME := T#1s;       (* manual until the level reading is live *)
    STARTUP_TMR : TON;
    LOAD_DELAY : TIME := T#20s;         (* the transfer pump starts: the demo's load step *)
    LOAD_TMR : TON;
    LIC : PID;                          (* the level controller *)
    LEVEL_M : REAL;
    MANUAL_X0 : REAL;
    OUT_PCT : REAL;
  END_VAR

  (* 1. Level in metres *)
  LEVEL_M := INT_TO_REAL(TK_01_LT_AI) * 2.0 / 32767.0;

  (* 5. Manual at the starting position until the startup delay is done *)
  STARTUP_TMR(IN := TRUE, PT := STARTUP_DELAY);
  IF STARTUP_TMR.Q THEN
    MANUAL_X0 := 0.0;
  ELSE
    MANUAL_X0 := START_PCT / KP;         (* in manual the block outputs KP * X0 *)
  END_IF;

  (* 2. PI on the level; 4. held in manual at 0 % while the high switch overrides it *)
  LIC(AUTO := STARTUP_TMR.Q AND NOT TK_01_LSH_DI, PV := LEVEL_M, SP := SP, X0 := MANUAL_X0,
      KP := KP, TR := TR, TD := 0.0, CYCLE := T#20ms);

  (* 3. Limit to the valve's range; 4. the interlock closes it *)
  IF TK_01_LSH_DI THEN
    OUT_PCT := 0.0;
  ELSE
    OUT_PCT := LIMIT(0.0, LIC.XOUT, 100.0);
  END_IF;
  FV_01_POS_AO := REAL_TO_INT(OUT_PCT * 32767.0 / 100.0);

  (* 6. The transfer pump: started 20 s after power-up, interlocked off by the low level switch *)
  LOAD_TMR(IN := TRUE, PT := LOAD_DELAY);
  P_01_RUN_DO := LOAD_TMR.Q AND NOT TK_01_LSL_DI;
END_PROGRAM


CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : plantpax_tank;
  END_RESOURCE
END_CONFIGURATION
