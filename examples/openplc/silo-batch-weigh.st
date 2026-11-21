(*
  OpenFactory example PLC program: silo-batch-weigh (scenes/silo-batch-weigh.yaml), the bulk
  solids scene of PLAN 5.7a: an airlock and a screw fill a weigh hopper to a batch weight, then a
  gate empties it into a mixer; the silo refills itself through its roof gate.
  Target: OpenPLC v3 runtime (IEC 61131-3 Structured Text), talking to the OpenFactory simulator
  as a Modbus TCP Slave Device. Setup: docs/guides/openplc-quickstart.md, then
  docs/guides/silo-batch-weigh.md for this scene. A port of examples/pyplc/silo-batch-weigh.py:
  same sequence, same tag names.

  Training and demo use only. Not a safety-rated program and not a safety-rated simulation.

  Sequence of operations; BATCH_STEP holds the active step
    Silo refill, on its own: SG_01 opens when the silo's net weight falls below REFILL_ON_KG and
    closes above REFILL_OFF_KG.
    1. FAST (10): the screw runs and the airlock turns at 100 % until the hopper's net weight
       reaches TARGET_KG - FAST_PREACT_KG (the screw runs full: 50 kg is still on it).
    2. SLOW (20): the airlock turns at SLOW_PCT until the net weight reaches
       TARGET_KG - SLOW_PREACT_KG (what the screw carries at that rate in its travel time).
    3. EMPTYING (30): the airlock stops; the screw runs on for SCREW_RUN_ON, then stops.
    4. DISCHARGE (40): the hopper's gate SG_02 opens until the hopper is empty.
    5. CLOSING (50): when the gate's closed limit switch is made, the next batch starts.
    The airlock runs only while the screw runs and is not plugged.

  I/O map: Modbus addresses come from the scene's int16_scaled lockfile
  (scenes/silo-batch-weigh.int16_scaled.modbus.yaml) and the exported I/O list CSV. OpenPLC
  places a Slave Device's points from address 100 onward, eight bits a byte: a zero-based
  discrete address N maps to %IX(100 + N / 8).(N mod 8), a register N to %IW100+N.
*)
PROGRAM silo_batch_weigh
  VAR
    (* Discrete inputs from the simulator (Modbus DI:0..12) *)
    RV_01_PLUG_DI AT %IX100.0 : BOOL;   (* DI:0 Silo Discharge Rotary Airlock - plug switch *)
    RV_01_RUN_DI AT %IX100.1 : BOOL;    (* DI:1 Silo Discharge Rotary Airlock - running *)
    SC_01_PLUG_DI AT %IX100.2 : BOOL;   (* DI:2 Weigh Hopper Feed Screw Conveyor - plug switch *)
    SC_01_RUN_DI AT %IX100.3 : BOOL;    (* DI:3 Weigh Hopper Feed Screw Conveyor - running *)
    SC_01_ZSS_DI AT %IX100.4 : BOOL;    (* DI:4 Weigh Hopper Feed Screw Conveyor - zero-speed switch *)
    SG_01_ZSC_DI AT %IX100.5 : BOOL;    (* DI:5 Silo Fill Gate - closed limit (ZSC) *)
    SG_01_ZSO_DI AT %IX100.6 : BOOL;    (* DI:6 Silo Fill Gate - open limit (ZSO) *)
    SG_02_ZSC_DI AT %IX100.7 : BOOL;    (* DI:7 Weigh Hopper Discharge Gate - closed limit (ZSC) *)
    SG_02_ZSO_DI AT %IX101.0 : BOOL;    (* DI:8 Weigh Hopper Discharge Gate - open limit (ZSO) *)
    SILO_01_LSH_DI AT %IX101.1 : BOOL;  (* DI:9 Flour Storage Silo - high level switch (LSH) *)
    SILO_01_LSL_DI AT %IX101.2 : BOOL;  (* DI:10 Flour Storage Silo - low level switch (LSL) *)
    WH_01_LSH_DI AT %IX101.3 : BOOL;    (* DI:11 Batch Weigh Hopper - high level switch (LSH) *)
    SIM_HEARTBEAT_DI AT %IX101.4 : BOOL; (* DI:12 OpenFactory simulator heartbeat, 1 Hz; not used here, watch it for a sim-down watchdog *)
    (* Coils to the simulator (Modbus CO:0..3) *)
    RV_01_RUN_DO AT %QX100.0 : BOOL;    (* CO:0 Silo Discharge Rotary Airlock - run *)
    SC_01_RUN_DO AT %QX100.1 : BOOL;    (* CO:1 Weigh Hopper Feed Screw Conveyor - run *)
    SG_01_OPEN_DO AT %QX100.2 : BOOL;   (* CO:2 Silo Fill Gate - open (held) *)
    SG_02_OPEN_DO AT %QX100.3 : BOOL;   (* CO:3 Weigh Hopper Discharge Gate - open (held) *)
    (* Input registers (Modbus IR:0..2) *)
    SILO_01_LT_AI AT %IW100 : INT;      (* IR:0 Flour Storage Silo level, 0 to 32767 = 0 to 4.5 m *)
    SILO_01_WT_AI AT %IW101 : INT;      (* IR:1 Flour Storage Silo weight, 0 to 32767 = 0 to 10000 kg *)
    WH_01_WT_AI AT %IW102 : INT;        (* IR:2 Batch Weigh Hopper weight, 0 to 32767 = 0 to 750 kg *)
    (* Holding registers (Modbus HR:0) *)
    RV_01_SPD_AO AT %QW100 : INT;       (* HR:0 Silo Discharge Rotary Airlock speed, 0 to 32767 = 0 to 100 % *)
  END_VAR
  VAR
    SILO_TARE_KG : REAL := 1800.0;
    HOPPER_TARE_KG : REAL := 180.0;
    REFILL_ON_KG : REAL := 3500.0;
    REFILL_OFF_KG : REAL := 4500.0;
    TARGET_KG : REAL := 250.0;
    FAST_PREACT_KG : REAL := 65.0;      (* the screw's load running full (10 kg/s x 5 s) and a margin *)
    SLOW_PCT : REAL := 20.0;            (* 12 kg/s x 20 % = 2.4 kg/s *)
    SLOW_PREACT_KG : REAL := 12.0;      (* 2.4 kg/s x 5 s on the screw *)
    SCREW_RUN_ON : TIME := T#6s;        (* its travel time, 5 s, and a margin *)
    EMPTY_KG : REAL := 0.5;
    RUN_ON_TMR : TON;
    BATCH_STEP : INT := 10;             (* 10 FAST, 20 SLOW, 30 EMPTYING, 40 DISCHARGE, 50 CLOSING *)
    REFILL : BOOL := FALSE;
    SCREW_OK : BOOL;
    SILO_KG : REAL;
    NET_KG : REAL;
    SPEED_PCT : REAL;
  END_VAR

  SILO_KG := INT_TO_REAL(SILO_01_WT_AI) * 10000.0 / 32767.0 - SILO_TARE_KG;
  REFILL := (SILO_KG < REFILL_ON_KG OR REFILL) AND SILO_KG < REFILL_OFF_KG;
  SG_01_OPEN_DO := REFILL;

  NET_KG := INT_TO_REAL(WH_01_WT_AI) * 750.0 / 32767.0 - HOPPER_TARE_KG;
  SCREW_OK := SC_01_RUN_DI AND NOT SC_01_PLUG_DI;
  RUN_ON_TMR(IN := (BATCH_STEP = 30), PT := SCREW_RUN_ON);

  (* Transitions, one per scan *)
  CASE BATCH_STEP OF
    10: IF NET_KG >= TARGET_KG - FAST_PREACT_KG THEN BATCH_STEP := 20; END_IF;
    20: IF NET_KG >= TARGET_KG - SLOW_PREACT_KG THEN BATCH_STEP := 30; END_IF;
    30: IF RUN_ON_TMR.Q THEN BATCH_STEP := 40; END_IF;
    40: IF NET_KG <= EMPTY_KG THEN BATCH_STEP := 50; END_IF;
    50: IF SG_02_ZSC_DI THEN BATCH_STEP := 10; END_IF;
  END_CASE;

  (* Actions *)
  SC_01_RUN_DO := (BATCH_STEP = 10) OR (BATCH_STEP = 20) OR (BATCH_STEP = 30);
  RV_01_RUN_DO := ((BATCH_STEP = 10) OR (BATCH_STEP = 20)) AND SCREW_OK;
  IF BATCH_STEP = 10 THEN
    SPEED_PCT := 100.0;
  ELSE
    SPEED_PCT := SLOW_PCT;
  END_IF;
  RV_01_SPD_AO := REAL_TO_INT(SPEED_PCT * 32767.0 / 100.0);
  SG_02_OPEN_DO := (BATCH_STEP = 40);
END_PROGRAM


CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : silo_batch_weigh;
  END_RESOURCE
END_CONFIGURATION
