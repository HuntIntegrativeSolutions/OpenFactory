(*
  OpenFactory example PLC program: buffer-and-release (scenes/buffer-and-release.yaml), PLAN 2.7.
  Target: OpenPLC v3 runtime (IEC 61131-3 Structured Text), talking to the OpenFactory simulator
  as a Modbus TCP Slave Device. Setup: docs/guides/openplc-quickstart.md, then
  docs/guides/buffer-and-release.md for this scene.

  Training and demo use only. Not a safety-rated program and not a safety-rated simulation.

  Sequence of operations
    1. Run both belts. Hold STP_01 raised, so boxes queue nose to tail on the buffer belt.
    2. Enable the box emitter once both belts have reported running for 1 s.
    3. A batch is ready when PE_FULL (under the third queued box) has been blocked for 1.5 s, which
       a moving box (1 s in the beam at 0.3 m/s) cannot do, and the blade has been back up for at
       least 2 s since the last batch.
    4. Release: lower STP_01 and count PE_REL rising edges on the outfeed belt. The outfeed runs
       three times faster than the buffer, so each released box pulls a gap and the eye clears
       between boxes. At the third, raise the blade: the fourth box is still behind the blade line.

  Timing margin: when the third box of a batch reaches PE_REL, the fourth box's front is about
  0.10 m behind the blade line, 0.33 s at 0.3 m/s. OpenPLC sees the eye up to 100 ms late and
  writes the raise up to 100 ms later, which that margin covers. The blade holds parts from the
  moment it starts rising; a box already over the blade line passes.

  I/O map: Modbus addresses come from the scene's int16_scaled lockfile
  (scenes/buffer-and-release.int16_scaled.modbus.yaml) and the exported I/O list CSV. OpenPLC
  places a Slave Device's points from address 100 onward, so a zero-based Modbus address N maps
  to %IX100.N or %QX100.N. The scene has no analog tags, so both lockfiles give the same addresses.
*)
PROGRAM buffer_release
  VAR
    (* Discrete inputs from the simulator (Modbus DI:0..5) *)
    CONV_BUF_RUN_DI AT %IX100.0 : BOOL; (* DI:0 Buffer Belt Conveyor - running feedback *)
    CONV_OUT_RUN_DI AT %IX100.1 : BOOL; (* DI:1 Outfeed Belt Conveyor - running feedback *)
    PE_FULL_DI AT %IX100.2 : BOOL;      (* DI:2 Buffer Batch Ready Photo Eye - beam blocked *)
    PE_REL_DI AT %IX100.3 : BOOL;       (* DI:3 Outfeed Release Count Photo Eye - beam blocked *)
    STP_01_DN_DI AT %IX100.4 : BOOL;    (* DI:4 Buffer Release Stopper - lowered limit *)
    STP_01_UP_DI AT %IX100.5 : BOOL;    (* DI:5 Buffer Release Stopper - raised limit *)
    (* Coils to the simulator (Modbus CO:0..3) *)
    CONV_BUF_RUN_DO AT %QX100.0 : BOOL; (* CO:0 Buffer Belt Conveyor - run command *)
    CONV_OUT_RUN_DO AT %QX100.1 : BOOL; (* CO:1 Outfeed Belt Conveyor - run command *)
    EMT_01_EN_DO AT %QX100.2 : BOOL;    (* CO:2 Buffer Belt Box Source - enable *)
    STP_01_RAISE_DO AT %QX100.3 : BOOL; (* CO:3 Buffer Release Stopper - raise solenoid *)
  END_VAR
  VAR
    BATCH : INT := 3;                   (* boxes per release *)
    BELTS_OK_DELAY : TIME := T#1s;      (* run feedback must hold this long to feed boxes *)
    FULL_DELAY : TIME := T#1500ms;      (* a standing box, not a passing one, at PE_FULL *)
    PAUSE : TIME := T#2s;               (* blade up at least this long between batches *)
    BELTS_OK_TMR : TON;                 (* both belts running, debounced *)
    FULL_TMR : TON;                     (* a box stands under PE_FULL: three are queued *)
    PAUSE_TMR : TON;                    (* the blade has been back up PAUSE since the last batch *)
    PE_REL_RISE : R_TRIG;               (* a released box reaches PE_REL *)
    RELEASING : BOOL;                   (* a batch is on its way out *)
    RELEASED : INT;                     (* boxes counted out in this batch *)
  END_VAR

  (* 1. Belts run *)
  CONV_BUF_RUN_DO := TRUE;
  CONV_OUT_RUN_DO := TRUE;

  (* 2. Feed boxes only after both belts have reported running for BELTS_OK_DELAY *)
  BELTS_OK_TMR(IN := CONV_BUF_RUN_DI AND CONV_OUT_RUN_DI, PT := BELTS_OK_DELAY);
  EMT_01_EN_DO := BELTS_OK_TMR.Q;

  (* 3. A batch is ready: three queued, and the blade up long enough since the last batch *)
  FULL_TMR(IN := PE_FULL_DI, PT := FULL_DELAY);
  PAUSE_TMR(IN := STP_01_UP_DI AND NOT RELEASING, PT := PAUSE);
  IF FULL_TMR.Q AND PAUSE_TMR.Q AND NOT RELEASING THEN
    RELEASING := TRUE;
    RELEASED := 0;
  END_IF;

  (* 4. Count the batch out on the outfeed; raise the blade at the last one *)
  PE_REL_RISE(CLK := PE_REL_DI);
  IF PE_REL_RISE.Q AND RELEASING THEN
    RELEASED := RELEASED + 1;
    IF RELEASED >= BATCH THEN
      RELEASING := FALSE;
    END_IF;
  END_IF;
  STP_01_RAISE_DO := NOT RELEASING;
END_PROGRAM


CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : buffer_release;
  END_RESOURCE
END_CONFIGURATION
