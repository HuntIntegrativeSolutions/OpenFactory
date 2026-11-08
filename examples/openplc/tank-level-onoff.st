(*
  OpenFactory example PLC program: tank-level-onoff (scenes/tank-level-onoff.yaml), the first
  process scene of PLAN 3.3.
  Target: OpenPLC v3 runtime (IEC 61131-3 Structured Text), talking to the OpenFactory simulator
  as a Modbus TCP Slave Device. Setup: docs/guides/openplc-quickstart.md, then
  docs/guides/tank-level-onoff.md for this scene.

  Training and demo use only. Not a safety-rated program and not a safety-rated simulation.

  Sequence of operations
    1. The low level switch (LSL, TK_02_LSL_DI) starts the fill pump P_01.
    2. The pump runs on (seal-in) until the high level switch (LSH, TK_02_LSH_DI) stops it, so the
       level swings between the two switches instead of chattering at one: on/off control with
       the switches as the deadband.
    3. If both switches read on at once (a wiring or switch fault: the tank cannot be both low
       and high), the pump stops.

  I/O map: Modbus addresses come from the scene's int16_scaled lockfile
  (scenes/tank-level-onoff.int16_scaled.modbus.yaml) and the exported I/O list CSV. OpenPLC
  places a Slave Device's points from address 100 onward, so a zero-based Modbus address N maps
  to %IX100.N, %QX100.N, or %IW100+N. TK_02_LT_AI is not used by the sequence; it is there to
  trend or display (raw 0 to 32767 for 0 to 1 m of level).
*)
PROGRAM tank_onoff
  VAR
    (* Discrete inputs from the simulator (Modbus DI:0..3) *)
    P_01_RUN_DI AT %IX100.0 : BOOL;     (* DI:0 Head Tank Fill Pump - running feedback *)
    TK_02_LSH_DI AT %IX100.1 : BOOL;    (* DI:1 Head Tank - high level switch (LSH) *)
    TK_02_LSL_DI AT %IX100.2 : BOOL;    (* DI:2 Head Tank - low level switch (LSL) *)
    SIM_HEARTBEAT_DI AT %IX100.3 : BOOL; (* DI:3 OpenFactory simulator heartbeat, 1 Hz; not used here, watch it for a sim-down watchdog *)
    (* Coils to the simulator (Modbus CO:0) *)
    P_01_RUN_DO AT %QX100.0 : BOOL;     (* CO:0 Head Tank Fill Pump - run command *)
    (* Input registers (Modbus IR:0) *)
    TK_02_LT_AI AT %IW100 : INT;        (* IR:0 Head Tank - level, 0 to 32767 = 0 to 1 m *)
  END_VAR
  VAR
    FILL : BOOL;                        (* the pump is filling (seal-in) *)
  END_VAR

  (* 1-2. Start at low, seal in, stop at high; 3. both switches on is a fault: stop *)
  FILL := (TK_02_LSL_DI OR FILL) AND NOT TK_02_LSH_DI;
  P_01_RUN_DO := FILL;
END_PROGRAM


CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : tank_onoff;
  END_RESOURCE
END_CONFIGURATION
