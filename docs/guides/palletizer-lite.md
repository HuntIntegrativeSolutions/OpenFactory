# Palletizer lite

`scenes/palletizer-lite.yaml` forms layers of four boxes. A two-axis gantry picks each box at the
end of the infeed and sets it on a layer-forming belt, where boxes queue in a row against a
stopper; each complete row is released to the discharge as one layer. It shows the
`pick_and_place` component (PLAN 2.5), a step sequencer for a gantry with no raised switch, and
counting placements so a layer never takes a fifth box.

"Lite" means one layer, one row, no pallets, and no stacking: OpenFactory's headless model has no
part-on-part stacking (PLAN 2.0 limits), so a stacked pallet cannot be simulated yet. The program
structure (pick, count, release a layer) is the one a stacking palletizer uses.

Training and demo use only. Not a safety-rated simulation, and the program is not a safety-rated
program.

## The plant

| Component | Type | What it does |
|---|---|---|
| `CONV_IN` | belt conveyor | Infeed, 2.4 m at 0.5 m/s |
| `STP_IN` | stopper | Holds the first box centred under the gantry (x 2.05 m) |
| `PE_PICK` | photo eye | Under the box at the pick position |
| `PP_01` | pick and place | Pad over the pick point; traverses 1.0 m toward +z in 1 s, lowers 0.25 m in 0.3 s |
| `CONV_LYR` | belt conveyor | Layer-forming belt at z 1.0 m, 3 m at 0.5 m/s; boxes are set down at x 2.05 m |
| `STP_LYR` | stopper | At x 4.2 m; a row of four queues from there back to x 3.0 m |
| `PE_ROW` | photo eye | Under the fourth queued box (x 3.15 m) |
| `EMT_01` | box emitter | A 0.3 m × 0.2 m box every 6 s, under the line's capacity |
| `REM_LYR` | part remover | End of the layer-forming belt; counts boxes |

## I/O

Eight discrete inputs and eight coils, no analog tags; both lockfiles give the same addresses.

| Tag | Modbus | OpenPLC | Meaning |
|---|---|---|---|
| `CONV_IN_RUN_DI` | DI:0 | `%IX100.0` | Infeed running |
| `CONV_LYR_RUN_DI` | DI:1 | `%IX100.1` | Layer belt running |
| `PE_PICK_DI` | DI:2 | `%IX100.2` | Box at the pick position |
| `PE_ROW_DI` | DI:3 | `%IX100.3` | Fourth box of the row in place |
| `PP_01_AT_PICK_DI` | DI:4 | `%IX100.4` | Gantry at the pick side |
| `PP_01_AT_PLACE_DI` | DI:5 | `%IX100.5` | Gantry at the place side |
| `PP_01_DOWN_DI` | DI:6 | `%IX100.6` | Pad lowered |
| `PP_01_HOLD_DI` | DI:7 | `%IX100.7` | Gripper holding a box |
| `SIM_HEARTBEAT_DI` | DI:8 | `%IX100.8` | Simulator heartbeat, 1 Hz (every scene has it; PLAN §6.1) |
| `CONV_IN_RUN_DO` | CO:0 | `%QX100.0` | Infeed run |
| `CONV_LYR_RUN_DO` | CO:1 | `%QX100.1` | Layer belt run |
| `EMT_01_EN_DO` | CO:2 | `%QX100.2` | Box source enable |
| `PP_01_GRIP_DO` | CO:3 | `%QX100.3` | Grip |
| `PP_01_LOWER_DO` | CO:4 | `%QX100.4` | Lower the pad |
| `PP_01_TRAV_DO` | CO:5 | `%QX100.5` | Traverse to the place side (released: return) |
| `STP_IN_RAISE_DO` | CO:6 | `%QX100.6` | Infeed stopper raise |
| `STP_LYR_RAISE_DO` | CO:7 | `%QX100.7` | Layer stopper raise |

## Sequence

1. Run both belts and hold both stoppers raised; feed boxes once both belts report running for
   1 s. Boxes queue at `STP_IN`, the first one under the gantry.
2. Pick and place, one box per cycle, in nine steps: wait, lower, grip, rise, traverse, lower,
   release, rise, return. The gantry has no raised switch, so each rise is timed (0.4 s for a
   0.3 s stroke). A cycle starts only with a box at `PE_PICK`, the gantry at the pick side, fewer
   than four boxes placed in this layer, and no layer leaving. Each completed release counts one
   box placed.
3. A layer is complete when four boxes have been placed and `PE_ROW` has been blocked for 1 s (a
   passing box blocks it for 0.6 s). Lower `STP_LYR` for 3 s to send the layer out, raise it, and
   start counting again.

Why count placements: watching `PE_ROW` alone, the gantry sets a fifth box down while the fourth
is still riding to the row, and that box rides out with the layer. The first version of this
program did exactly that (layers of five, measured headless).

The ST program calls its step variable `PICK_STEP`: `STEP` is a reserved word in IEC 61131-3 (an
SFC keyword), and OpenPLC's compiler rejects it.

Programs: `examples/openplc/palletizer-lite.st` and `examples/pyplc/palletizer-lite.py`.

## Run it

```bash
uv run openfactory run scenes/palletizer-lite.yaml --script examples/pyplc/palletizer-lite.py
```

With OpenPLC, follow [the quickstart](openplc-quickstart.md) for the container, then export,
configure with `examples/openplc/palletizer-lite.st`, and run with
`--bind 0.0.0.0 --analog-encoding int16_scaled` (commands in the [guides index](README.md)). Slave
Devices then shows `%IX100.0 to %IX100.8` and `%QX100.0 to %QX100.7`.

## Expected result

60 s from start under the stand-in script: 10 boxes spawned and `REM_LYR` takes 8, two layers of
four, none lost. (Under OpenPLC at the original 4 s interval the result matched the script exactly;
the 6 s interval has not yet been re-run under OpenPLC.) The first layer leaves at about 27 s and the second at about 51 s. The run summary line
reads `spawned 10, removed {'box': 8}, lost {}; by remover {'REM_LYR': {'box': 8}}`.

Why a box every 6 s: a layer of four takes the gantry and the layer stopper about 19 s, roughly
4.8 s a box. At the original 4 s interval the infeed backed up without limit, so the longest
spawn-to-remover time kept growing (27 s at 60 s, 47 s at 300 s) and the scene passed its
`max_cycle_s` only because the check ran for 60 s. At 6 s the longest is 27.4 s (the first box of
a layer waiting for the other three) at 60, 300 and 600 s alike.

## Try this

- Inject `slow` on `PP_01` (half speed). The gantry now needs more than 6 s a box, so the line is
  over capacity again: in 60 s only 5 boxes leave, and
  `openfactory test ... --duration 300 --fault PP_01=slow` fails `max_cycle_s` (measured: 70 s
  spawn to remover).
- Inject `jammed` on `PP_01` while it holds a box mid-traverse. Neither `at_pick` nor `at_place`
  makes and the sequence waits forever: add a step timeout and an alarm.
- Change the layer to three boxes. What else must move?
