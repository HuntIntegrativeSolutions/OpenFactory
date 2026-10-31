(*
  OpenFactory example PLC program: sorting-by-height (scenes/sorting-by-height.yaml).
  Target: OpenPLC v3 runtime (IEC 61131-3 Structured Text), talking to the OpenFactory simulator
  as a Modbus TCP Slave Device. Setup: docs/guides/openplc-quickstart.md.

  Training and demo use only. Not a safety-rated program and not a safety-rated simulation.

  Sequence of operations
    1. Run both belt conveyors (CONV_01 infeed, CONV_02 divert lane) and write both speed
       references, 100 % each.
    2. Enable the box emitter once both belts have reported running for 1 s without a break.
    3. PE_01 sits 0.3 m above the belt: tall boxes (0.4 m) break the beam, short ones (0.2 m) pass
       under it. On each PE_01 rising edge, start that box's own DIVERT_DELAY timer for its travel
       from the eye to the pusher plate, then extend CYL_01 when it expires. Each tall box takes
       the next of four divert slots (a ring: DIVERT_PEND_n and DIVERT_TMR_n), so a second tall
       box tripping the eye before the first is pushed keeps its own divert.
    4. Retract CYL_01 as soon as its extended limit (CYL_01_EXT_DI) is made.
    Tall boxes end up on CONV_02 and reach REM_02; short boxes stay on CONV_01 and reach REM_01.

  I/O map: Modbus addresses come from the scene's int16_scaled lockfile
  (scenes/sorting-by-height.int16_scaled.modbus.yaml) and the exported I/O list CSV
  (openfactory export ... --analog-encoding int16_scaled). OpenPLC places a Slave Device's points
  from address 100 onward, so a zero-based Modbus address N maps to %IX100.N, %QX100.N, or %QW100+N.
  Digital addresses (DI, CO) are identical in the float32 and int16_scaled lockfiles (ADR-0004
  decision 8); only the holding registers differ (float32 uses two per tag).

  Timing: PE_01 trips when a tall box's leading edge reaches x = 1.5 m; the plate starts pushing
  best when the box center is near x = 2.39 m, 2.08 s later at 0.5 m/s (the headless sorter test,
  tests/unit/test_sorter_headless.py). OpenPLC polls the simulator every 100 ms (Settings, Slave
  polling period), so the eye is seen up to 100 ms late and the extend coil written up to 100 ms
  late, about 100 ms on average; DIVERT_DELAY is 2.08 s minus that 0.10 s. A tall box blocks the
  beam for 0.6 s, well above the ~200 ms minimum pulse that 100 ms polling can catch.

  Divert queue: boxes are 0.3 m long at 0.5 m/s, so tall boxes can trip the eye about 0.8 s apart
  at the closest (box plus a gap that clears the beam), and up to three can be between the eye and
  the plate at once. Four slots cover that. A fifth box in transit would find its slot still busy:
  DIVERT_OVERRUN latches TRUE (watch it in the OpenPLC monitor) and that box is not diverted. The
  pusher needs 0.8 s to extend and retract, so tall boxes closer than that cannot all be pushed
  whatever the program does. tests/scenes/sorter-tall-pairs.yaml (every box tall, 1.5 s apart)
  exercises two pending diverts at once.

  Why the emitter waits 1 s: the emitter spawns a box the moment it is enabled. When OpenPLC
  (re)connects, its input image can still hold old feedback for one poll, so an enable written
  straight from the run feedback chatters on, off, on within ~0.3 s and drops two boxes on top of
  each other; the pusher then diverts both, short box included (seen in the first 1.14 run). The
  BELTS_OK_TMR permissive delay filters that out.
*)
PROGRAM sorter
  VAR
    (* Discrete inputs from the simulator (Modbus DI:0..5, same in every encoding) *)
    CONV_01_RUN_DI AT %IX100.0 : BOOL;  (* DI:0 Infeed Belt Conveyor - running feedback *)
    CYL_01_EXT_DI AT %IX100.1 : BOOL;   (* DI:1 Tall Box Divert Pusher - extended limit *)
    CYL_01_RET_DI AT %IX100.2 : BOOL;   (* DI:2 Tall Box Divert Pusher - retracted limit *)
    PE_01_DI AT %IX100.3 : BOOL;        (* DI:3 Infeed Tall Box Photo Eye - beam blocked *)
    CONV_02_RUN_DI AT %IX100.4 : BOOL;  (* DI:4 Divert Lane Belt Conveyor - running feedback *)
    SIM_HEARTBEAT_DI AT %IX100.5 : BOOL; (* DI:5 OpenFactory simulator heartbeat, 1 Hz; not used here, watch it for a sim-down watchdog *)
    (* Coils to the simulator (Modbus CO:0..3, same in every encoding) *)
    CONV_01_RUN_DO AT %QX100.0 : BOOL;  (* CO:0 Infeed Belt Conveyor - run command *)
    CYL_01_EXT_DO AT %QX100.1 : BOOL;   (* CO:1 Tall Box Divert Pusher - extend solenoid *)
    EMT_01_EN_DO AT %QX100.2 : BOOL;    (* CO:2 Infeed Box Source - enable *)
    CONV_02_RUN_DO AT %QX100.3 : BOOL;  (* CO:3 Divert Lane Belt Conveyor - run command *)
    (* Holding registers to the simulator (Modbus HR:0..1), int16_scaled: 0..32767 = 0..100 % *)
    CONV_01_SPD_AO AT %QW100 : INT;     (* HR:0 Infeed Belt Conveyor - speed command *)
    CONV_02_SPD_AO AT %QW101 : INT;     (* HR:1 Divert Lane Belt Conveyor - speed command *)
  END_VAR
  VAR
    SPEED_RAW : INT := 32767;           (* 100 % speed reference, raw counts *)
    DIVERT_DELAY : TIME := T#1980ms;    (* eye-to-plate travel, less average polling latency *)
    BELTS_OK_DELAY : TIME := T#1s;      (* run feedback must hold this long to feed boxes *)
    BELTS_OK_TMR : TON;                 (* both belts running, debounced *)
    PE_01_RISE : R_TRIG;                (* tall box arrives at PE_01 *)
    DIVERT_TMR_0 : TON;                 (* eye-to-plate travel timer, divert slot 0 *)
    DIVERT_TMR_1 : TON;                 (* eye-to-plate travel timer, divert slot 1 *)
    DIVERT_TMR_2 : TON;                 (* eye-to-plate travel timer, divert slot 2 *)
    DIVERT_TMR_3 : TON;                 (* eye-to-plate travel timer, divert slot 3 *)
    DIVERT_PEND_0 : BOOL;               (* slot 0 holds a tall box on its way to the pusher *)
    DIVERT_PEND_1 : BOOL;               (* slot 1 holds a tall box on its way to the pusher *)
    DIVERT_PEND_2 : BOOL;               (* slot 2 holds a tall box on its way to the pusher *)
    DIVERT_PEND_3 : BOOL;               (* slot 3 holds a tall box on its way to the pusher *)
    NEXT_SLOT : INT := 0;               (* ring index: the slot the next tall box takes, 0..3 *)
    DIVERT_OVERRUN : BOOL;              (* latched: a tall box found its slot busy and was lost *)
    PUSH_CMD : BOOL;                    (* pusher extend request, held until the extended limit *)
  END_VAR

  (* 1. Belts run at 100 % *)
  CONV_01_RUN_DO := TRUE;
  CONV_02_RUN_DO := TRUE;
  CONV_01_SPD_AO := SPEED_RAW;
  CONV_02_SPD_AO := SPEED_RAW;

  (* 2. Feed boxes only after both belts have reported running for BELTS_OK_DELAY *)
  BELTS_OK_TMR(IN := CONV_01_RUN_DI AND CONV_02_RUN_DI, PT := BELTS_OK_DELAY);
  EMT_01_EN_DO := BELTS_OK_TMR.Q;

  (* 3. Tall box at the eye: give it the next slot and its own travel timer *)
  PE_01_RISE(CLK := PE_01_DI);
  IF PE_01_RISE.Q THEN
    CASE NEXT_SLOT OF
      0: DIVERT_OVERRUN := DIVERT_OVERRUN OR DIVERT_PEND_0; DIVERT_PEND_0 := TRUE;
      1: DIVERT_OVERRUN := DIVERT_OVERRUN OR DIVERT_PEND_1; DIVERT_PEND_1 := TRUE;
      2: DIVERT_OVERRUN := DIVERT_OVERRUN OR DIVERT_PEND_2; DIVERT_PEND_2 := TRUE;
      3: DIVERT_OVERRUN := DIVERT_OVERRUN OR DIVERT_PEND_3; DIVERT_PEND_3 := TRUE;
    END_CASE;
    NEXT_SLOT := (NEXT_SLOT + 1) MOD 4;
  END_IF;

  (* 3a. Each slot's box is due at the plate: extend *)
  DIVERT_TMR_0(IN := DIVERT_PEND_0, PT := DIVERT_DELAY);
  IF DIVERT_TMR_0.Q THEN
    DIVERT_PEND_0 := FALSE;
    PUSH_CMD := TRUE;
  END_IF;
  DIVERT_TMR_1(IN := DIVERT_PEND_1, PT := DIVERT_DELAY);
  IF DIVERT_TMR_1.Q THEN
    DIVERT_PEND_1 := FALSE;
    PUSH_CMD := TRUE;
  END_IF;
  DIVERT_TMR_2(IN := DIVERT_PEND_2, PT := DIVERT_DELAY);
  IF DIVERT_TMR_2.Q THEN
    DIVERT_PEND_2 := FALSE;
    PUSH_CMD := TRUE;
  END_IF;
  DIVERT_TMR_3(IN := DIVERT_PEND_3, PT := DIVERT_DELAY);
  IF DIVERT_TMR_3.Q THEN
    DIVERT_PEND_3 := FALSE;
    PUSH_CMD := TRUE;
  END_IF;

  (* 4. Retract on the extended limit *)
  IF CYL_01_EXT_DI THEN
    PUSH_CMD := FALSE;
  END_IF;
  CYL_01_EXT_DO := PUSH_CMD;
END_PROGRAM


CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : sorter;
  END_RESOURCE
END_CONFIGURATION
