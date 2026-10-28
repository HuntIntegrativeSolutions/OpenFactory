(*
  OpenFactory example PLC program: conveyor-start-stop (scenes/conveyor-start-stop.yaml), the
  hello-world scene of PLAN 2.7.
  Target: OpenPLC v3 runtime (IEC 61131-3 Structured Text), talking to the OpenFactory simulator
  as a Modbus TCP Slave Device. Setup: docs/guides/openplc-quickstart.md, then
  docs/guides/conveyor-start-stop.md for this scene.

  Training and demo use only. Not a safety-rated program and not a safety-rated simulation.
  A real stop circuit is hard-wired; a PLC stop input like this one is an operator convenience.

  Sequence of operations
    1. Turning SS_01 to RUN starts the line: CONV_01 runs and stays running (seal-in) while SS_01
       stays in RUN and PB_STOP is not pressed. The scene starts with SS_01 in RUN, so the line
       starts on the first scan that sees it there.
    2. PB_STOP is wired normally closed (the scene binds it with invert: true): PB_STOP_DI is TRUE
       while the button is released, and a broken wire reads as a press, so it fails safe.
       Pressing it drops the seal-in. To restart, turn SS_01 to OFF and back to RUN.
    3. A selector reading OFF and RUN at once (a wiring fault) stops the line too.
    4. Once CONV_01 has reported running for 1 s, enable the box emitter EMT_01.
    5. PL_RUN shows the running feedback, not the command.
    Boxes ride the belt end to end and reach REM_01.

  I/O map: Modbus addresses come from the scene's int16_scaled lockfile
  (scenes/conveyor-start-stop.int16_scaled.modbus.yaml) and the exported I/O list CSV. OpenPLC
  places a Slave Device's points from address 100 onward, so a zero-based Modbus address N maps to
  %IX100.N or %QX100.N. This scene has no analog tags, so the float32 and int16_scaled lockfiles
  give the same addresses (ADR-0004 decision 8).

  Why the emitter waits 1 s: see examples/openplc/sorting-by-height.st. When OpenPLC (re)connects,
  its input image can hold old feedback for one poll, and an enable written straight from it can
  chatter and drop two boxes on top of each other.
*)
PROGRAM start_stop
  VAR
    (* Discrete inputs from the simulator (Modbus DI:0..3) *)
    CONV_01_RUN_DI AT %IX100.0 : BOOL;  (* DI:0 Transfer Belt Conveyor - running feedback *)
    PB_STOP_DI AT %IX100.1 : BOOL;      (* DI:1 Transfer Conveyor Stop Pushbutton - NC, TRUE = released *)
    SS_01_OFF_DI AT %IX100.2 : BOOL;    (* DI:2 Off/Run Selector Switch - in OFF *)
    SS_01_RUN_DI AT %IX100.3 : BOOL;    (* DI:3 Off/Run Selector Switch - in RUN *)
    (* Coils to the simulator (Modbus CO:0..2) *)
    CONV_01_RUN_DO AT %QX100.0 : BOOL;  (* CO:0 Transfer Belt Conveyor - run command *)
    EMT_01_EN_DO AT %QX100.1 : BOOL;    (* CO:1 Transfer Belt Box Source - enable *)
    PL_RUN_DO AT %QX100.2 : BOOL;       (* CO:2 Transfer Conveyor Running Pilot Light *)
  END_VAR
  VAR
    BELT_OK_DELAY : TIME := T#1s;       (* running feedback must hold this long to feed boxes *)
    BELT_OK_TMR : TON;                  (* belt running, debounced *)
    SS_RUN_RISE : R_TRIG;               (* selector turned to RUN (also the first scan in RUN) *)
    RUN_OK : BOOL;                      (* selector in RUN only, stop button released *)
    RUN_LATCH : BOOL;                   (* the line is started (seal-in) *)
  END_VAR

  (* 1-3. Start on a turn to RUN, seal in, drop on stop, OFF, or a selector wiring fault *)
  RUN_OK := SS_01_RUN_DI AND NOT SS_01_OFF_DI AND PB_STOP_DI;
  SS_RUN_RISE(CLK := SS_01_RUN_DI);
  RUN_LATCH := (SS_RUN_RISE.Q OR RUN_LATCH) AND RUN_OK;
  CONV_01_RUN_DO := RUN_LATCH;

  (* 4. Feed boxes only after the belt has reported running for BELT_OK_DELAY *)
  BELT_OK_TMR(IN := RUN_LATCH AND CONV_01_RUN_DI, PT := BELT_OK_DELAY);
  EMT_01_EN_DO := BELT_OK_TMR.Q;

  (* 5. Running light from feedback *)
  PL_RUN_DO := CONV_01_RUN_DI;
END_PROGRAM


CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : start_stop;
  END_RESOURCE
END_CONFIGURATION
