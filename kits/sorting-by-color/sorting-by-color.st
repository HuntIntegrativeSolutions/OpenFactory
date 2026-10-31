(*
  OpenFactory example PLC program: sorting-by-color (scenes/sorting-by-color.yaml), PLAN 2.7.
  Target: OpenPLC v3 runtime (IEC 61131-3 Structured Text), talking to the OpenFactory simulator
  as a Modbus TCP Slave Device. Setup: docs/guides/openplc-quickstart.md, then
  docs/guides/sorting-by-color.md for this scene.

  Training and demo use only. Not a safety-rated program and not a safety-rated simulation.

  Sequence of operations
    1. Run both belts; enable the box source once both have reported running for 1 s.
    2. On each CS_01_RED_DI rising edge (a red box under the color sensor), start that box's
       EXTEND_DELAY timer. When it expires, count the box as on its way to the arm: the arm
       extends while any red box is on its way. The delay lets a blue box already past the
       sensor clear the arm before it swings out (a swinging arm sweeps whatever it reaches).
    3. Each PE_RED rising edge (a red box arriving on the side lane) counts one red box off; the
       arm retracts when none is left on its way.
    Boxes are at least 4 s (2 m) apart, so one pending timer is enough; closer boxes would need a
    ring of timers as in examples/openplc/sorting-by-height.st.

  Timing: a red box is under the sensor for 0.6 s (0.3 m at 0.5 m/s), three OpenPLC polls. After
  EXTEND_DELAY (1.5 s) it is 0.75 m past the sensor and 0.85 m from the arm, which needs 0.5 s
  to swing out; the box ahead of it, at least 2 m ahead, is already past the arm's tip.

  I/O map: Modbus addresses come from the scene's int16_scaled lockfile
  (scenes/sorting-by-color.int16_scaled.modbus.yaml) and the exported I/O list CSV. OpenPLC places
  a Slave Device's points from address 100 onward, so a zero-based Modbus address N maps to
  %IX100.N or %QX100.N. The scene has no analog tags, so both lockfiles give the same addresses.
*)
PROGRAM color_sort
  VAR
    (* Discrete inputs from the simulator (Modbus DI:0..7) *)
    CONV_01_RUN_DI AT %IX100.0 : BOOL;  (* DI:0 Sorting Line Belt Conveyor - running *)
    CONV_RED_RUN_DI AT %IX100.1 : BOOL; (* DI:1 Red Box Lane Belt Conveyor - running *)
    CS_01_PART_DI AT %IX100.2 : BOOL;   (* DI:2 Red Box Color Sensor - part in range *)
    CS_01_RED_DI AT %IX100.3 : BOOL;    (* DI:3 Red Box Color Sensor - red seen *)
    DIV_01_EXT_DI AT %IX100.4 : BOOL;   (* DI:4 Red Box Divert Arm - extended limit *)
    DIV_01_RET_DI AT %IX100.5 : BOOL;   (* DI:5 Red Box Divert Arm - retracted limit *)
    PE_RED_DI AT %IX100.6 : BOOL;       (* DI:6 Red Box Lane Arrival Photo Eye *)
    SIM_HEARTBEAT_DI AT %IX100.7 : BOOL; (* DI:7 OpenFactory simulator heartbeat, 1 Hz; not used here, watch it for a sim-down watchdog *)
    (* Coils to the simulator (Modbus CO:0..3) *)
    CONV_01_RUN_DO AT %QX100.0 : BOOL;  (* CO:0 Sorting Line Belt Conveyor - run *)
    CONV_RED_RUN_DO AT %QX100.1 : BOOL; (* CO:1 Red Box Lane Belt Conveyor - run *)
    DIV_01_EXT_DO AT %QX100.2 : BOOL;   (* CO:2 Red Box Divert Arm - extend solenoid *)
    EMT_01_EN_DO AT %QX100.3 : BOOL;    (* CO:3 Sorting Line Box Source - enable *)
  END_VAR
  VAR
    BELTS_OK_DELAY : TIME := T#1s;      (* run feedback must hold this long to feed boxes *)
    EXTEND_DELAY : TIME := T#1500ms;    (* the red box is then 0.75 m past the sensor *)
    BELTS_OK_TMR : TON;                 (* both belts running, debounced *)
    EXTEND_TMR : TON;                   (* a red box's travel from the sensor toward the arm *)
    RED_RISE : R_TRIG;                  (* a red box arrives under the color sensor *)
    LANE_RISE : R_TRIG;                 (* a red box arrives on the side lane *)
    PENDING : BOOL;                     (* a red box is between the sensor and the extend point *)
    RED_OUT : INT := 0;                 (* red boxes between the extend point and the side lane *)
  END_VAR

  (* 1. Belts run; feed boxes once both report running for 1 s *)
  CONV_01_RUN_DO := TRUE;
  CONV_RED_RUN_DO := TRUE;
  BELTS_OK_TMR(IN := CONV_01_RUN_DI AND CONV_RED_RUN_DI, PT := BELTS_OK_DELAY);
  EMT_01_EN_DO := BELTS_OK_TMR.Q;

  (* 2. A red box at the sensor: after EXTEND_DELAY it is on its way to the arm *)
  RED_RISE(CLK := CS_01_RED_DI);
  IF RED_RISE.Q THEN
    PENDING := TRUE;
  END_IF;
  EXTEND_TMR(IN := PENDING, PT := EXTEND_DELAY);
  IF EXTEND_TMR.Q THEN
    PENDING := FALSE;
    RED_OUT := RED_OUT + 1;
  END_IF;

  (* 3. A red box on the side lane is done *)
  LANE_RISE(CLK := PE_RED_DI);
  IF LANE_RISE.Q AND RED_OUT > 0 THEN
    RED_OUT := RED_OUT - 1;
  END_IF;
  DIV_01_EXT_DO := RED_OUT > 0;
END_PROGRAM


CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : color_sort;
  END_RESOURCE
END_CONFIGURATION
