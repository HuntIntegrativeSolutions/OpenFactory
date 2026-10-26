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
       under it. On each PE_01 rising edge, wait DIVERT_DELAY for the box to travel from the eye
       to the pusher plate, then extend CYL_01.
    4. Retract CYL_01 as soon as its extended limit (CYL_01_EXT_DI) is made.
    Tall boxes end up on CONV_02 and reach REM_02; short boxes stay on CONV_01 and reach REM_01.

  I/O map: Modbus addresses come from the scene's int16_scaled lockfile
  (scenes/sorting-by-height.int16_scaled.modbus.yaml) and the exported I/O list CSV
  (openfactory export ... --analog-encoding int16_scaled). OpenPLC places a Slave Device's points
  from address 100 onward, so a zero-based Modbus address N maps to %IX100.N, %QX100.N, or %QW100+N.

  Timing: PE_01 trips when a tall box's leading edge reaches x = 1.5 m; the plate starts pushing
  best when the box center is near x = 2.39 m, 2.08 s later at 0.5 m/s (the headless sorter test,
  tests/unit/test_sorter_headless.py). OpenPLC polls the simulator every 100 ms (Settings, Slave
  polling period), so the eye is seen up to 100 ms late and the extend coil written up to 100 ms
  late, about 100 ms on average; DIVERT_DELAY is 2.08 s minus that 0.10 s. A tall box blocks the
  beam for 0.6 s, well above the ~200 ms minimum pulse that 100 ms polling can catch. The emitter
  spaces boxes 3 s apart, so at most one tall box is between the eye and the plate at a time; this
  program handles one pending divert, not a queue.

  Why the emitter waits 1 s: the emitter spawns a box the moment it is enabled. When OpenPLC
  (re)connects, its input image can still hold old feedback for one poll, so an enable written
  straight from the run feedback chatters on, off, on within ~0.3 s and drops two boxes on top of
  each other; the pusher then diverts both, short box included (seen in the first 1.14 run). The
  BELTS_OK_TMR permissive delay filters that out.
*)
PROGRAM sorter
  VAR
    (* Discrete inputs from the simulator (Modbus DI:0..4) *)
    CONV_01_RUN_DI AT %IX100.0 : BOOL;  (* Infeed Belt Conveyor - running feedback *)
    CONV_02_RUN_DI AT %IX100.1 : BOOL;  (* Divert Lane Belt Conveyor - running feedback *)
    CYL_01_EXT_DI AT %IX100.2 : BOOL;   (* Tall Box Divert Pusher - extended limit *)
    CYL_01_RET_DI AT %IX100.3 : BOOL;   (* Tall Box Divert Pusher - retracted limit *)
    PE_01_DI AT %IX100.4 : BOOL;        (* Infeed Tall Box Photo Eye - beam blocked *)
    (* Coils to the simulator (Modbus CO:0..3) *)
    CONV_01_RUN_DO AT %QX100.0 : BOOL;  (* Infeed Belt Conveyor - run command *)
    CONV_02_RUN_DO AT %QX100.1 : BOOL;  (* Divert Lane Belt Conveyor - run command *)
    CYL_01_EXT_DO AT %QX100.2 : BOOL;   (* Tall Box Divert Pusher - extend solenoid *)
    EMT_01_EN_DO AT %QX100.3 : BOOL;    (* Infeed Box Source - enable *)
    (* Holding registers to the simulator (Modbus HR:0..1), int16_scaled: 0..32767 = 0..100 % *)
    CONV_01_SPD_AO AT %QW100 : INT;     (* Infeed Belt Conveyor - speed command *)
    CONV_02_SPD_AO AT %QW101 : INT;     (* Divert Lane Belt Conveyor - speed command *)
  END_VAR
  VAR
    SPEED_RAW : INT := 32767;           (* 100 % speed reference, raw counts *)
    DIVERT_DELAY : TIME := T#1980ms;    (* eye-to-plate travel, less average polling latency *)
    BELTS_OK_DELAY : TIME := T#1s;      (* run feedback must hold this long to feed boxes *)
    BELTS_OK_TMR : TON;                 (* both belts running, debounced *)
    PE_01_RISE : R_TRIG;                (* tall box arrives at PE_01 *)
    DIVERT_TMR : TON;                   (* eye-to-plate travel timer *)
    DIVERT_PENDING : BOOL;              (* a tall box is on its way to the pusher *)
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

  (* 3. Tall box at the eye: time its travel to the plate, then extend *)
  PE_01_RISE(CLK := PE_01_DI);
  IF PE_01_RISE.Q THEN
    DIVERT_PENDING := TRUE;
  END_IF;
  DIVERT_TMR(IN := DIVERT_PENDING, PT := DIVERT_DELAY);
  IF DIVERT_TMR.Q THEN
    DIVERT_PENDING := FALSE;
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
