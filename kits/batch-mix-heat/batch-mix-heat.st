(*
  OpenFactory example PLC program: batch-mix-heat (scenes/batch-mix-heat.yaml), the batch recipe
  of PLAN 3.3: fill two ingredients to a level each, heat to a temperature, hold.
  Target: OpenPLC v3 runtime (IEC 61131-3 Structured Text), talking to the OpenFactory simulator
  as a Modbus TCP Slave Device. Setup: docs/guides/openplc-quickstart.md, then
  docs/guides/batch-mix-heat.md for this scene.

  Training and demo use only. Not a safety-rated program and not a safety-rated simulation.

  Sequence of operations (one batch, then held); BATCH_STEP holds the active step
    0.  Idle: wait 1 s after power-up for the transmitters' first readings, then start a batch
        if the mixing tank is empty (level below 0.02 m).
    10. Fill water: open XV_01 until the level reaches 0.10 m.
    20. Fill syrup: open XV_02 until the level reaches 0.16 m (the recipe: 0.06 m of syrup on
        0.10 m of water, 37.5 % syrup by volume; each valve's 1 s close adds a little).
    30. Heat: the heater runs until the temperature reaches 50 C.
    40. Hold: the batch is ready (PL_READY_DO lit); the heater keeps it at 50 C, on below
        49.5 C and off at 50 C.
    Interlocks, in every step: the high level switch (LSH) closes both inlet valves, and the
    heater runs only while the level covers it (0.10 m and above).

  I/O map: Modbus addresses come from the scene's int16_scaled lockfile
  (scenes/batch-mix-heat.int16_scaled.modbus.yaml) and the exported I/O list CSV. OpenPLC places
  a Slave Device's points from address 100 onward, so a zero-based Modbus address N maps to
  %IX100.N, %QX100.N or %IW100+N.
*)
PROGRAM batch_mix_heat
  VAR
    (* Discrete inputs from the simulator (Modbus DI:0..3) *)
    TK_01_LSH_DI AT %IX100.0 : BOOL;    (* DI:0 Batch Mixing Tank - high level switch (LSH), 0.5 m *)
    XV_01_ZSC_DI AT %IX100.1 : BOOL;    (* DI:1 Mixing Tank Water Inlet Valve - closed limit (ZSC) *)
    XV_02_ZSC_DI AT %IX100.2 : BOOL;    (* DI:2 Mixing Tank Syrup Inlet Valve - closed limit (ZSC) *)
    SIM_HEARTBEAT_DI AT %IX100.3 : BOOL; (* DI:3 OpenFactory simulator heartbeat, 1 Hz; not used here, watch it for a sim-down watchdog *)
    (* Coils to the simulator (Modbus CO:0..3) *)
    HTR_01_ON_DO AT %QX100.0 : BOOL;    (* CO:0 Batch Mixing Tank Immersion Heater - on command *)
    PL_READY_DO AT %QX100.1 : BOOL;     (* CO:1 Batch Ready Pilot Light *)
    XV_01_OPEN_DO AT %QX100.2 : BOOL;   (* CO:2 Mixing Tank Water Inlet Valve - open command *)
    XV_02_OPEN_DO AT %QX100.3 : BOOL;   (* CO:3 Mixing Tank Syrup Inlet Valve - open command *)
    (* Input registers (Modbus IR:0..1) *)
    TK_01_LT_AI AT %IW100 : INT;        (* IR:0 Batch Mixing Tank Level Transmitter, 0 to 32767 = 0 to 0.6 m *)
    TK_01_TT_AI AT %IW101 : INT;        (* IR:1 Batch Mixing Tank Temperature Transmitter, 0 to 32767 = 0 to 100 C *)
  END_VAR
  VAR
    WATER_SP : REAL := 0.10;            (* level at which the water fill ends, m *)
    BATCH_SP : REAL := 0.16;            (* level at which the syrup fill ends, m *)
    EMPTY_LVL : REAL := 0.02;           (* below this the tank is empty and can start a batch, m *)
    TEMP_SP : REAL := 50.0;             (* batch temperature, C *)
    HOLD_DB : REAL := 0.5;              (* the heater restarts this far below TEMP_SP in the hold, C *)
    COVER_LVL : REAL := 0.10;           (* heater low-level permissive, m *)
    STARTUP_DELAY : TIME := T#1s;       (* until the transmitters' first readings *)
    STARTUP_TMR : TON;
    BATCH_STEP : INT := 0;              (* the active step: 0, 10, 20, 30, 40; not STEP, an IEC 61131-3 keyword *)
    HEAT : BOOL := FALSE;               (* heater demand before the permissive *)
    LEVEL_M : REAL;
    TEMP_C : REAL;
  END_VAR

  LEVEL_M := INT_TO_REAL(TK_01_LT_AI) * 0.6 / 32767.0;
  TEMP_C := INT_TO_REAL(TK_01_TT_AI) * 100.0 / 32767.0;
  STARTUP_TMR(IN := TRUE, PT := STARTUP_DELAY);

  (* Transitions, one per scan *)
  CASE BATCH_STEP OF
    0:  IF STARTUP_TMR.Q AND LEVEL_M < EMPTY_LVL THEN BATCH_STEP := 10; END_IF;
    10: IF LEVEL_M >= WATER_SP THEN BATCH_STEP := 20; END_IF;
    20: IF LEVEL_M >= BATCH_SP THEN BATCH_STEP := 30; END_IF;
    30: IF TEMP_C >= TEMP_SP THEN BATCH_STEP := 40; END_IF;
  END_CASE;

  (* Actions; the hold is a thermostat with a deadband *)
  IF BATCH_STEP = 30 THEN
    HEAT := TRUE;
  ELSIF BATCH_STEP = 40 THEN
    IF TEMP_C >= TEMP_SP THEN
      HEAT := FALSE;
    ELSIF TEMP_C < TEMP_SP - HOLD_DB THEN
      HEAT := TRUE;
    END_IF;
  ELSE
    HEAT := FALSE;
  END_IF;
  XV_01_OPEN_DO := (BATCH_STEP = 10) AND NOT TK_01_LSH_DI;
  XV_02_OPEN_DO := (BATCH_STEP = 20) AND NOT TK_01_LSH_DI;
  HTR_01_ON_DO := HEAT AND LEVEL_M >= COVER_LVL;
  PL_READY_DO := (BATCH_STEP = 40);
END_PROGRAM


CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : batch_mix_heat;
  END_RESOURCE
END_CONFIGURATION
