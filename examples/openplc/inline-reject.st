(*
  OpenFactory example PLC program: inline-reject (scenes/inline-reject.yaml).
  Target: OpenPLC v3 runtime (IEC 61131-3 Structured Text), talking to the OpenFactory simulator
  as a Modbus TCP Slave Device. Setup: docs/guides/openplc-quickstart.md.

  Training and demo use only. Not a safety-rated program and not a safety-rated simulation.

  The scene was built in the scene editor from `openfactory new`, with no YAML written by hand
  (docs/guides/inline-reject.md). This program is a line-for-line port of the stand-in script,
  examples/pyplc/inline-reject.py.

  Sequence of operations
    1. Run both belt conveyors (CONV_01 feeds CONV_02 end to end).
    2. Enable the box emitter once both belts have reported running for 1 s without a break.
    3. PE_01 sits 0.3 m above CONV_02: oversize boxes (0.4 m) break the beam, good ones (0.2 m)
       pass under it. On each PE_01 rising edge, start that box's own REJECT_DELAY timer for its
       travel from the eye to the pusher plate, then extend CYL_01 when it expires; the plate
       pushes the box off the side of CONV_02 into the reject bin, REM_01. Each oversize box
       takes the next of four slots (a ring: REJECT_PEND_n and REJECT_TMR_n).
    4. Retract CYL_01 as soon as its extended limit (CYL_01_EXTENDED_DI) is made.
    Good boxes ride on to the end of CONV_02 and reach REM_02.

  I/O map: Modbus addresses come from the scene's lockfiles (scenes/inline-reject.*.modbus.yaml;
  this scene has no analog tags, so both give the same addresses). OpenPLC places a Slave
  Device's points from address 100 onward, so a zero-based Modbus address N maps to %IX100.N or
  %QX100.N.

  Timing: the eye and the plate are 1.0 m apart along CONV_02, as on sorting-by-height, so the
  box is due at the plate 2.08 s after it trips the eye at 0.5 m/s; REJECT_DELAY is 2.08 s minus
  OpenPLC's average 0.10 s polling latency, as the sorter's DIVERT_DELAY is.
*)
PROGRAM inline_reject
  VAR
    (* Discrete inputs from the simulator (Modbus DI:0..4) *)
    CONV_01_RUNNING_DI AT %IX100.0 : BOOL;  (* DI:0 Infeed Belt Conveyor - running feedback *)
    CONV_02_RUNNING_DI AT %IX100.1 : BOOL;  (* DI:1 Outfeed Belt Conveyor - running feedback *)
    CYL_01_EXTENDED_DI AT %IX100.2 : BOOL;  (* DI:2 Oversize Box Reject Pusher - extended limit *)
    PE_01_DI AT %IX100.3 : BOOL;            (* DI:3 Oversize Box Photo Eye - beam blocked *)
    SIM_HEARTBEAT_DI AT %IX100.4 : BOOL;    (* DI:4 OpenFactory simulator heartbeat, 1 Hz; not used here *)
    (* Coils to the simulator (Modbus CO:0..3) *)
    CONV_01_RUN_DO AT %QX100.0 : BOOL;      (* CO:0 Infeed Belt Conveyor - run command *)
    CONV_02_RUN_DO AT %QX100.1 : BOOL;      (* CO:1 Outfeed Belt Conveyor - run command *)
    CYL_01_EXTEND_DO AT %QX100.2 : BOOL;    (* CO:2 Oversize Box Reject Pusher - extend solenoid *)
    EMT_01_DO AT %QX100.3 : BOOL;           (* CO:3 Infeed Box Source - enable *)
  END_VAR
  VAR
    REJECT_DELAY : TIME := T#1980ms;    (* eye-to-plate travel, less average polling latency *)
    BELTS_OK_DELAY : TIME := T#1s;      (* run feedback must hold this long to feed boxes *)
    BELTS_OK_TMR : TON;                 (* both belts running, debounced *)
    PE_01_RISE : R_TRIG;                (* an oversize box arrives at PE_01 *)
    REJECT_TMR_0 : TON;                 (* eye-to-plate travel timer, slot 0 *)
    REJECT_TMR_1 : TON;                 (* eye-to-plate travel timer, slot 1 *)
    REJECT_TMR_2 : TON;                 (* eye-to-plate travel timer, slot 2 *)
    REJECT_TMR_3 : TON;                 (* eye-to-plate travel timer, slot 3 *)
    REJECT_PEND_0 : BOOL;               (* slot 0 holds an oversize box on its way to the pusher *)
    REJECT_PEND_1 : BOOL;               (* slot 1 holds an oversize box on its way to the pusher *)
    REJECT_PEND_2 : BOOL;               (* slot 2 holds an oversize box on its way to the pusher *)
    REJECT_PEND_3 : BOOL;               (* slot 3 holds an oversize box on its way to the pusher *)
    NEXT_SLOT : INT := 0;               (* ring index: the slot the next oversize box takes, 0..3 *)
    PUSH_CMD : BOOL;                    (* pusher extend request, held until the extended limit *)
  END_VAR

  (* 1. Belts run *)
  CONV_01_RUN_DO := TRUE;
  CONV_02_RUN_DO := TRUE;

  (* 2. Feed boxes only after both belts have reported running for BELTS_OK_DELAY *)
  BELTS_OK_TMR(IN := CONV_01_RUNNING_DI AND CONV_02_RUNNING_DI, PT := BELTS_OK_DELAY);
  EMT_01_DO := BELTS_OK_TMR.Q;

  (* 3. Oversize box at the eye: give it the next slot and its own travel timer *)
  PE_01_RISE(CLK := PE_01_DI);
  IF PE_01_RISE.Q THEN
    CASE NEXT_SLOT OF
      0: REJECT_PEND_0 := TRUE;
      1: REJECT_PEND_1 := TRUE;
      2: REJECT_PEND_2 := TRUE;
      3: REJECT_PEND_3 := TRUE;
    END_CASE;
    NEXT_SLOT := (NEXT_SLOT + 1) MOD 4;
  END_IF;

  (* 3a. Each slot's box is due at the plate: extend *)
  REJECT_TMR_0(IN := REJECT_PEND_0, PT := REJECT_DELAY);
  IF REJECT_TMR_0.Q THEN
    REJECT_PEND_0 := FALSE;
    PUSH_CMD := TRUE;
  END_IF;
  REJECT_TMR_1(IN := REJECT_PEND_1, PT := REJECT_DELAY);
  IF REJECT_TMR_1.Q THEN
    REJECT_PEND_1 := FALSE;
    PUSH_CMD := TRUE;
  END_IF;
  REJECT_TMR_2(IN := REJECT_PEND_2, PT := REJECT_DELAY);
  IF REJECT_TMR_2.Q THEN
    REJECT_PEND_2 := FALSE;
    PUSH_CMD := TRUE;
  END_IF;
  REJECT_TMR_3(IN := REJECT_PEND_3, PT := REJECT_DELAY);
  IF REJECT_TMR_3.Q THEN
    REJECT_PEND_3 := FALSE;
    PUSH_CMD := TRUE;
  END_IF;

  (* 4. Retract on the extended limit *)
  IF CYL_01_EXTENDED_DI THEN
    PUSH_CMD := FALSE;
  END_IF;
  CYL_01_EXTEND_DO := PUSH_CMD;
END_PROGRAM


CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : inline_reject;
  END_RESOURCE
END_CONFIGURATION
