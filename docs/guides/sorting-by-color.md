# Sorting by color

`scenes/sorting-by-color.yaml` sorts red boxes from blue ones. A color sensor taught red looks
down at each box on the main belt; a diverter arm swings across the belt to slide red boxes onto a
parallel side lane, and blue boxes ride straight on. It shows the `color_sensor` and part colors
(added for this scene, PLAN 2.7), the `diverter_arm` (PLAN 2.5), and counting parts in and out of
a section of conveyor to decide when an actuator can let go.

Training and demo use only. Not a safety-rated simulation, and the program is not a safety-rated
program.

## The plant

| Component | Type | What it does |
|---|---|---|
| `CONV_01` | belt conveyor | Main belt, 5 m at 0.5 m/s |
| `CS_01` | color sensor | At x 1.0 m, 0.4 m above a box, looking down; taught red |
| `DIV_01` | diverter arm | Pivot on the main belt's -z edge at x 2.6 m; swings 45 degrees across in 0.5 s |
| `CONV_RED` | belt conveyor | Side lane alongside the main belt (z 0.25 to 0.75 m), from x 2.6 m |
| `PE_RED` | photo eye | Across the side lane only, at x 3.4 m |
| `EMT_01` | box emitter | A box every 4 s, red or blue at even odds (`red_box`, `blue_box`) |
| `REM_MAIN`, `REM_RED` | part remover | Ends of the main belt and the side lane |

## I/O

No analog tags, so both lockfiles give the same addresses.

| Tag | Modbus | OpenPLC | Meaning |
|---|---|---|---|
| `CONV_01_RUN_DI` | DI:0 | `%IX100.0` | Main belt running |
| `CONV_RED_RUN_DI` | DI:1 | `%IX100.1` | Side lane running |
| `CS_01_PART_DI` | DI:2 | `%IX100.2` | Color sensor: a box in range |
| `CS_01_RED_DI` | DI:3 | `%IX100.3` | Color sensor: red seen |
| `DIV_01_EXT_DI` | DI:4 | `%IX100.4` | Arm extended |
| `DIV_01_RET_DI` | DI:5 | `%IX100.5` | Arm retracted |
| `PE_RED_DI` | DI:6 | `%IX100.6` | A red box arrived on the side lane |
| `CONV_01_RUN_DO` | CO:0 | `%QX100.0` | Main belt run |
| `CONV_RED_RUN_DO` | CO:1 | `%QX100.1` | Side lane run |
| `DIV_01_EXT_DO` | CO:2 | `%QX100.2` | Arm extend solenoid |
| `EMT_01_EN_DO` | CO:3 | `%QX100.3` | Box source enable |

## Sequence

1. Run both belts; feed boxes once both report running for 1 s.
2. Each time the sensor starts seeing red, start a 1.5 s timer for that box. When it expires the
   box is 0.75 m past the sensor and on its way to the arm: count it, and extend the arm while the
   count is above zero.
3. Each red box arriving on the side lane (`PE_RED` rising) counts one off. The arm retracts when
   none is left.

Why the 1.5 s delay: a swinging arm sweeps whatever it reaches. Extending the moment a red box is
seen would catch the blue box 2 m ahead of it as it passes the arm's tip. After 1.5 s that box has
cleared the arm, and the red box still has 0.85 m to go, plenty for a 0.5 s swing.

Boxes are at least 4 s apart, so one pending timer is enough. Closer boxes need a ring of timers,
as in `examples/openplc/sorting-by-height.st`, and more than that: see "Try this".

Programs: `examples/openplc/sorting-by-color.st` and `examples/pyplc/sorting-by-color.py`.

## Run it

```bash
uv run openfactory run scenes/sorting-by-color.yaml --script examples/pyplc/sorting-by-color.py
```

With OpenPLC, follow [the quickstart](openplc-quickstart.md) for the container, then export,
configure with `examples/openplc/sorting-by-color.st`, and run with
`--bind 0.0.0.0 --analog-encoding int16_scaled` (commands in the [guides index](README.md)). Slave
Devices then shows `%IX100.0 to %IX100.6` and `%QX100.0 to %QX100.3`.

## Expected result

60 s from start, under either program: 15 boxes spawned; `REM_MAIN` takes 5 blue boxes and
`REM_RED` 8 red ones, none lost and none in the wrong lane. The run summary line reads
`spawned 15, removed {'blue_box': 5, 'red_box': 8}, lost {}; by remover {'REM_MAIN': {'blue_box': 5}, 'REM_RED': {'red_box': 8}}`.

## Try this

- Inject `stuck_off` on `CS_01`. The PLC never sees red, and every box goes to `REM_MAIN`
  (measured: 5 blue and 8 red there in 60 s). How would the program notice? (`CS_01_PART_DI`
  still pulses for every box, so a red count of zero over many boxes is suspicious.)
- Set the emitter's `interval_s` to 1.5 s. Boxes are now 0.75 m apart and the one-slot program
  falls apart: in 60 s, 5 blue boxes end up on the red lane and 10 red boxes on the main belt. A
  ring of timers fixes the bookkeeping, but not the geometry: the arm needs about 2 s to slide a
  box off, and a blue box 1.5 s behind reaches it first. Slow the belt, or space the boxes.
- Teach the sensor `blue` instead, and the lanes swap.
