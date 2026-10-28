(*
  OpenFactory example PLC program: palletizer-lite (scenes/palletizer-lite.yaml), PLAN 2.7.
  Target: OpenPLC v3 runtime (IEC 61131-3 Structured Text), talking to the OpenFactory simulator
  as a Modbus TCP Slave Device. Setup: docs/guides/openplc-quickstart.md, then
  docs/guides/palletizer-lite.md for this scene.

  Training and demo use only. Not a safety-rated program and not a safety-rated simulation.

  Sequence of operations
    1. Run both belts and hold both stoppers raised; enable the box source once both belts have
       reported running for 1 s. Boxes queue at STP_IN, the first one under the gantry.
    2. Pick and place, one box per cycle (the gantry has no raised switch, so each rise is timed):
       with a box at PE_PICK, the gantry at the pick side, fewer than LAYER_BOXES placed in this
       layer, and no layer leaving: PICK_STEP 1 lower, 2 grip, 3 rise, 4 traverse, 5 lower,
       6 release, 7 rise, 8 return, back to 0. (STEP is a reserved word in IEC 61131-3.) Count each box placed. The layer-forming belt carries it to
       the row queueing at STP_LYR.
    3. A layer is complete when LAYER_BOXES have been placed and PE_ROW (under the fourth queued
       box) has been blocked for 1 s, which a passing box (0.6 s in the beam) cannot do. Lower
       STP_LYR for LAYER_OUT to send the layer to the discharge, raise it, and start the count
       again. Counting placements, not just watching PE_ROW, keeps a fifth box from being set
       down while the row completes and riding out with the layer.

  I/O map: Modbus addresses come from the scene's int16_scaled lockfile
  (scenes/palletizer-lite.int16_scaled.modbus.yaml) and the exported I/O list CSV. OpenPLC places
  a Slave Device's points from address 100 onward, eight bits to a byte: zero-based Modbus address
  N maps to %IX(100 + N / 8).(N MOD 8), and likewise for %QX. The scene has no analog tags, so
  both lockfiles give the same addresses.
*)
PROGRAM palletizer
  VAR
    (* Discrete inputs from the simulator (Modbus DI:0..7) *)
    CONV_IN_RUN_DI AT %IX100.0 : BOOL;  (* DI:0 Palletizer Infeed Belt Conveyor - running *)
    CONV_LYR_RUN_DI AT %IX100.1 : BOOL; (* DI:1 Layer Forming Belt Conveyor - running *)
    PE_PICK_DI AT %IX100.2 : BOOL;      (* DI:2 Infeed Box At Pick Position Photo Eye *)
    PE_ROW_DI AT %IX100.3 : BOOL;       (* DI:3 Layer Row Complete Photo Eye *)
    PP_01_AT_PICK_DI AT %IX100.4 : BOOL;  (* DI:4 Transfer Gantry - at pick side *)
    PP_01_AT_PLACE_DI AT %IX100.5 : BOOL; (* DI:5 Transfer Gantry - at place side *)
    PP_01_DOWN_DI AT %IX100.6 : BOOL;   (* DI:6 Transfer Gantry - pad lowered *)
    PP_01_HOLD_DI AT %IX100.7 : BOOL;   (* DI:7 Transfer Gantry - holding a part *)
    (* Coils to the simulator (Modbus CO:0..7) *)
    CONV_IN_RUN_DO AT %QX100.0 : BOOL;  (* CO:0 Palletizer Infeed Belt Conveyor - run *)
    CONV_LYR_RUN_DO AT %QX100.1 : BOOL; (* CO:1 Layer Forming Belt Conveyor - run *)
    EMT_01_EN_DO AT %QX100.2 : BOOL;    (* CO:2 Palletizer Infeed Box Source - enable *)
    PP_01_GRIP_DO AT %QX100.3 : BOOL;   (* CO:3 Transfer Gantry - grip *)
    PP_01_LOWER_DO AT %QX100.4 : BOOL;  (* CO:4 Transfer Gantry - lower pad *)
    PP_01_TRAV_DO AT %QX100.5 : BOOL;   (* CO:5 Transfer Gantry - traverse to place side *)
    STP_IN_RAISE_DO AT %QX100.6 : BOOL; (* CO:6 Infeed Pick Position Stopper - raise *)
    STP_LYR_RAISE_DO AT %QX100.7 : BOOL; (* CO:7 Layer Forming Stopper - raise *)
  END_VAR
  VAR
    LAYER_BOXES : INT := 4;             (* boxes per layer *)
    BELTS_OK_DELAY : TIME := T#1s;      (* run feedback must hold this long to feed boxes *)
    RISE : TIME := T#400ms;             (* pad rise, lower_time_s 0.3 plus margin *)
    SETTLE : TIME := T#200ms;           (* the released box drops onto the belt *)
    ROW_DELAY : TIME := T#1s;           (* a standing box, not a passing one, at PE_ROW *)
    LAYER_OUT : TIME := T#3s;           (* four boxes (1.2 m) clear the blade at 0.5 m/s *)
    BELTS_OK_TMR : TON;                 (* both belts running, debounced *)
    RISE_TMR : TON;                     (* the pad has had time to rise *)
    SETTLE_TMR : TON;                   (* the released box has settled on the belt *)
    ROW_TMR : TON;                      (* a box stands under PE_ROW *)
    LAYER_TMR : TON;                    (* the layer has had time to leave *)
    PICK_STEP : INT := 0;               (* pick-and-place step, 0 = waiting at the pick side *)
    LAYER_OUT_ON : BOOL;                (* a layer is leaving *)
    PLACED : INT := 0;                  (* boxes placed in the layer being formed *)
  END_VAR

  (* 1. Belts run; stoppers hold; feed boxes once both belts report running for 1 s *)
  CONV_IN_RUN_DO := TRUE;
  CONV_LYR_RUN_DO := TRUE;
  STP_IN_RAISE_DO := TRUE;
  BELTS_OK_TMR(IN := CONV_IN_RUN_DI AND CONV_LYR_RUN_DI, PT := BELTS_OK_DELAY);
  EMT_01_EN_DO := BELTS_OK_TMR.Q;

  (* 2. Pick and place, one box per cycle; the timers run only in the steps that use them *)
  RISE_TMR(IN := PICK_STEP = 3 OR PICK_STEP = 7, PT := RISE);
  SETTLE_TMR(IN := PICK_STEP = 6 AND NOT PP_01_HOLD_DI, PT := SETTLE);
  CASE PICK_STEP OF
    0: IF PE_PICK_DI AND PP_01_AT_PICK_DI AND PLACED < LAYER_BOXES AND NOT LAYER_OUT_ON THEN
         PICK_STEP := 1;
       END_IF;
    1: IF PP_01_DOWN_DI THEN PICK_STEP := 2; END_IF;
    2: IF PP_01_HOLD_DI THEN PICK_STEP := 3; END_IF;
    3: IF RISE_TMR.Q THEN PICK_STEP := 4; END_IF;
    4: IF PP_01_AT_PLACE_DI THEN PICK_STEP := 5; END_IF;
    5: IF PP_01_DOWN_DI THEN PICK_STEP := 6; END_IF;
    6: IF SETTLE_TMR.Q THEN
         PICK_STEP := 7;
         PLACED := PLACED + 1;
       END_IF;
    7: IF RISE_TMR.Q THEN PICK_STEP := 8; END_IF;
    8: IF PP_01_AT_PICK_DI THEN PICK_STEP := 0; END_IF;
  END_CASE;
  PP_01_LOWER_DO := PICK_STEP = 1 OR PICK_STEP = 2 OR PICK_STEP = 5 OR PICK_STEP = 6;
  PP_01_GRIP_DO := PICK_STEP = 2 OR PICK_STEP = 3 OR PICK_STEP = 4 OR PICK_STEP = 5;
  PP_01_TRAV_DO := PICK_STEP = 4 OR PICK_STEP = 5 OR PICK_STEP = 6 OR PICK_STEP = 7;

  (* 3. A complete row leaves as one layer *)
  ROW_TMR(IN := PE_ROW_DI, PT := ROW_DELAY);
  IF ROW_TMR.Q AND PLACED >= LAYER_BOXES AND NOT LAYER_OUT_ON THEN
    LAYER_OUT_ON := TRUE;
  END_IF;
  LAYER_TMR(IN := LAYER_OUT_ON, PT := LAYER_OUT);
  IF LAYER_TMR.Q THEN
    LAYER_OUT_ON := FALSE;
    PLACED := 0;
  END_IF;
  STP_LYR_RAISE_DO := NOT LAYER_OUT_ON;
END_PROGRAM


CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : palletizer;
  END_RESOURCE
END_CONFIGURATION
