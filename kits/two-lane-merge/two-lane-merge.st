(*
  OpenFactory example PLC program: two-lane-merge (scenes/two-lane-merge.yaml), PLAN 2.7.
  Target: OpenPLC v3 runtime (IEC 61131-3 Structured Text), talking to the OpenFactory simulator
  as a Modbus TCP Slave Device. Setup: docs/guides/openplc-quickstart.md, then
  docs/guides/two-lane-merge.md for this scene.

  Training and demo use only. Not a safety-rated program and not a safety-rated simulation.

  Sequence of operations
    1. Run both lanes and the outfeed; enable both box sources once all three report running for
       1 s. The lane stoppers are spring-raised (the scene binds raise with invert: true): a
       lane's box waits at its blade until the program energizes STP_x_REL_DO for RELEASE, which
       lets exactly one box through.
    2. STATE 0, idle at 0 degrees with the deck stopped: serve a waiting box (PE_A or PE_B),
       alternating lanes when both wait.
    3. STATE 10, lane A (straight on): release one box and run the deck. The box rides through
       onto the outfeed; the deck is free again when PE_TTA has seen it come and go.
    4. Lane B (from the side): STATE 20 turns the deck to 90; STATE 21, at 90, releases one box
       and runs the deck until PE_TTB sees it centred; STATE 22 stops the deck and turns back;
       STATE 23, at 0, runs the deck until PE_TTA has seen the box come and go.

  Release pulse: the blade takes 0.2 s to drop, and a box at 0.5 m/s is over the blade line for
  0.6 s after that, so a 500 ms pulse ends while the released box is still over the line (it
  passes) and before the next box's front reaches it (it stops). OpenPLC's polling delays both
  edges of the pulse alike, so it does not change the window.

  I/O map: Modbus addresses come from the scene's int16_scaled lockfile
  (scenes/two-lane-merge.int16_scaled.modbus.yaml) and the exported I/O list CSV. OpenPLC places a
  Slave Device's points from address 100 onward, eight bits to a byte: zero-based Modbus address
  N maps to %IX(100 + N / 8).(N MOD 8), so DI:8 is %IX101.0, and likewise for %QX. The scene has
  no analog tags, so both lockfiles give the same addresses.
*)
PROGRAM merge
  VAR
    (* Discrete inputs from the simulator (Modbus DI:0..9) *)
    CONV_A_RUN_DI AT %IX100.0 : BOOL;   (* DI:0 Lane A Infeed Belt Conveyor - running *)
    CONV_B_RUN_DI AT %IX100.1 : BOOL;   (* DI:1 Lane B Infeed Belt Conveyor - running *)
    CONV_OUT_RUN_DI AT %IX100.2 : BOOL; (* DI:2 Merged Outfeed Belt Conveyor - running *)
    PE_A_DI AT %IX100.3 : BOOL;         (* DI:3 Lane A Box Waiting Photo Eye *)
    PE_B_DI AT %IX100.4 : BOOL;         (* DI:4 Lane B Box Waiting Photo Eye *)
    PE_TTA_DI AT %IX100.5 : BOOL;       (* DI:5 Turntable Straight-Through Photo Eye *)
    PE_TTB_DI AT %IX100.6 : BOOL;       (* DI:6 Turntable Side Load Centred Photo Eye *)
    TT_01_AT0_DI AT %IX100.7 : BOOL;    (* DI:7 Lane Merge Turntable - at 0 degrees *)
    TT_01_AT90_DI AT %IX101.0 : BOOL;   (* DI:8 Lane Merge Turntable - at 90 degrees *)
    SIM_HEARTBEAT_DI AT %IX101.1 : BOOL; (* DI:9 OpenFactory simulator heartbeat, 1 Hz; not used here, watch it for a sim-down watchdog *)
    (* Coils to the simulator (Modbus CO:0..8) *)
    CONV_A_RUN_DO AT %QX100.0 : BOOL;   (* CO:0 Lane A Infeed Belt Conveyor - run *)
    CONV_B_RUN_DO AT %QX100.1 : BOOL;   (* CO:1 Lane B Infeed Belt Conveyor - run *)
    CONV_OUT_RUN_DO AT %QX100.2 : BOOL; (* CO:2 Merged Outfeed Belt Conveyor - run *)
    EMT_A_EN_DO AT %QX100.3 : BOOL;     (* CO:3 Lane A Box Source - enable *)
    EMT_B_EN_DO AT %QX100.4 : BOOL;     (* CO:4 Lane B Box Source - enable *)
    STP_A_REL_DO AT %QX100.5 : BOOL;    (* CO:5 Lane A Release Stopper - energize to release *)
    STP_B_REL_DO AT %QX100.6 : BOOL;    (* CO:6 Lane B Release Stopper - energize to release *)
    TT_01_RUN_DO AT %QX100.7 : BOOL;    (* CO:7 Lane Merge Turntable - deck belt run *)
    TT_01_TURN_DO AT %QX101.0 : BOOL;   (* CO:8 Lane Merge Turntable - turn to 90 *)
  END_VAR
  VAR
    BELTS_OK_DELAY : TIME := T#1s;      (* run feedback must hold this long to feed boxes *)
    RELEASE : TIME := T#500ms;          (* stopper release pulse: one box *)
    BELTS_OK_TMR : TON;                 (* lanes and outfeed running, debounced *)
    REL_A_TMR : TON;                    (* lane A stopper energized this long *)
    REL_B_TMR : TON;                    (* lane B stopper energized this long *)
    STATE : INT := 0;                   (* 0 idle, 10 A pass, 20 B turn out, 21 B load, 22 B turn back, 23 B pass *)
    LAST_B : BOOL;                      (* the last box served came from lane B *)
    REL_A : BOOL;                       (* lane A release pulse in progress *)
    REL_B : BOOL;                       (* lane B release pulse in progress *)
    SEEN : BOOL;                        (* PE_TTA has seen the passing box arrive *)
    FEED_OK : BOOL;                     (* belts proven running *)
  END_VAR

  (* 1. Belts run; feed boxes once they have all reported running for BELTS_OK_DELAY *)
  CONV_A_RUN_DO := TRUE;
  CONV_B_RUN_DO := TRUE;
  CONV_OUT_RUN_DO := TRUE;
  BELTS_OK_TMR(IN := CONV_A_RUN_DI AND CONV_B_RUN_DI AND CONV_OUT_RUN_DI, PT := BELTS_OK_DELAY);
  FEED_OK := BELTS_OK_TMR.Q;
  EMT_A_EN_DO := FEED_OK;
  EMT_B_EN_DO := FEED_OK;

  (* 2. Idle at 0: serve a waiting box, alternating when both lanes wait *)
  IF STATE = 0 AND TT_01_AT0_DI THEN
    IF PE_A_DI AND (NOT PE_B_DI OR LAST_B) THEN
      STATE := 10;
      REL_A := TRUE;
      SEEN := FALSE;
      LAST_B := FALSE;
    ELSIF PE_B_DI THEN
      STATE := 20;
      LAST_B := TRUE;
    END_IF;
  END_IF;

  (* 3. Lane A box rides straight through; done when PE_TTA has seen it come and go *)
  IF STATE = 10 THEN
    IF PE_TTA_DI THEN
      SEEN := TRUE;
    ELSIF SEEN THEN
      STATE := 0;
    END_IF;
  END_IF;

  (* 4. Lane B box: turn out, load until centred, turn back, pass through *)
  IF STATE = 20 AND TT_01_AT90_DI THEN
    STATE := 21;
    REL_B := TRUE;
  END_IF;
  IF STATE = 21 AND PE_TTB_DI THEN
    STATE := 22;
  END_IF;
  IF STATE = 22 AND TT_01_AT0_DI THEN
    STATE := 23;
    SEEN := FALSE;
  END_IF;
  IF STATE = 23 THEN
    IF PE_TTA_DI THEN
      SEEN := TRUE;
    ELSIF SEEN THEN
      STATE := 0;
    END_IF;
  END_IF;

  (* Release pulses: one box per pulse *)
  REL_A_TMR(IN := REL_A, PT := RELEASE);
  IF REL_A_TMR.Q THEN
    REL_A := FALSE;
  END_IF;
  REL_B_TMR(IN := REL_B, PT := RELEASE);
  IF REL_B_TMR.Q THEN
    REL_B := FALSE;
  END_IF;
  STP_A_REL_DO := REL_A;
  STP_B_REL_DO := REL_B;
  TT_01_TURN_DO := STATE = 20 OR STATE = 21;
  TT_01_RUN_DO := STATE = 10 OR STATE = 21 OR STATE = 23;
END_PROGRAM


CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : merge;
  END_RESOURCE
END_CONFIGURATION
