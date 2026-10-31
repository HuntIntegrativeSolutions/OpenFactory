# Conveyor start/stop (hello world)

`scenes/conveyor-start-stop.yaml` is the smallest useful OpenFactory scene: one belt conveyor, a
box source, and an operator station with an OFF/RUN selector switch, a red stop pushbutton, and a
green running light. The PLC program is a seal-in start/stop circuit, the first rung most PLC
courses teach.

Training and demo use only. Not a safety-rated simulation, and the program is not a safety-rated
program: a real stop circuit is hard-wired, and a PLC stop input like this one is an operator
convenience.

## The plant

| Component | Type | What it does |
|---|---|---|
| `CONV_01` | belt conveyor | 5 m long, 0.5 m/s; runs while `CONV_01_RUN_DO` is on |
| `EMT_01` | box emitter | A 0.3 m box every 3 s while enabled |
| `REM_01` | part remover | Takes boxes off the discharge end and counts them |
| `SS_01` | selector switch | Two positions, OFF (a) and RUN (b); the scene starts in RUN |
| `PB_STOP` | pushbutton | Red, momentary, wired normally closed |
| `PL_RUN` | pilot light | Green, lit while the PLC says the belt is running |

## I/O

The same addresses in both lockfiles: the scene has no analog tags, so `float32` and
`int16_scaled` differ only in name (ADR-0004 decision 8).

| Tag | Modbus | OpenPLC | Meaning |
|---|---|---|---|
| `CONV_01_RUN_DI` | DI:0 | `%IX100.0` | Belt running feedback |
| `PB_STOP_DI` | DI:1 | `%IX100.1` | Stop pushbutton, NC: TRUE while released |
| `SS_01_OFF_DI` | DI:2 | `%IX100.2` | Selector in OFF |
| `SS_01_RUN_DI` | DI:3 | `%IX100.3` | Selector in RUN |
| `SIM_HEARTBEAT_DI` | DI:4 | `%IX100.4` | Simulator heartbeat, 1 Hz (every scene has it; PLAN §6.1) |
| `CONV_01_RUN_DO` | CO:0 | `%QX100.0` | Belt run command |
| `EMT_01_EN_DO` | CO:1 | `%QX100.1` | Box source enable |
| `PL_RUN_DO` | CO:2 | `%QX100.2` | Running light |

`PB_STOP_DI` shows the binding transforms of PLAN 2.17. The scene binds the button with
`{tag: PB_STOP_DI, invert: true}`, the way a stop button is wired in a panel (normally closed),
so the PLC sees TRUE while nobody is pressing it and a broken wire reads as a press. The I/O list
CSV shows `NC (inverted)` in its `transform` column.

## Sequence

1. Turning `SS_01` to RUN starts the line. `CONV_01` runs and stays running (the seal-in) while
   `SS_01` stays in RUN and `PB_STOP` is not pressed. Because the scene starts in RUN, the program
   sees a turn to RUN on its first scan and starts the line by itself.
2. Pressing `PB_STOP` drops the seal-in. The line stays stopped with the selector still in RUN;
   turn it to OFF and back to RUN to restart. A held run request never restarts a machine on its
   own after a stop.
3. A selector that reads OFF and RUN at once (a wiring fault) stops the line too.
4. Boxes are fed only after the belt has reported running for 1 s (the same permissive as the
   sorter, which keeps OpenPLC's first poll from dropping two boxes together).
5. `PL_RUN` follows the running feedback, not the run command, so a belt that is commanded but
   does not report running shows dark on the panel (and stops the box feed).

Programs: `examples/openplc/conveyor-start-stop.st` (OpenPLC) and
`examples/pyplc/conveyor-start-stop.py` (the same logic as a stand-in script).

## Run it

With no PLC:

```bash
uv run openfactory run scenes/conveyor-start-stop.yaml --script examples/pyplc/conveyor-start-stop.py
```

Open <http://127.0.0.1:8000/>. Click `PB_STOP` to stop the belt, then click `SS_01` twice (OFF,
then RUN) to restart it.

With OpenPLC, follow [the quickstart](openplc-quickstart.md) for the container, then:

```bash
uv run openfactory export scenes/conveyor-start-stop.yaml --analog-encoding int16_scaled \
    -o conveyor-start-stop-int16.csv
uv run python tools/openplc_configure.py --program examples/openplc/conveyor-start-stop.st \
    --csv conveyor-start-stop-int16.csv --device-ip <WSL host IP>
uv run openfactory run scenes/conveyor-start-stop.yaml --bind 0.0.0.0 --analog-encoding int16_scaled
```

OpenPLC's Slave Devices page then shows `%IX100.0 to %IX100.4` and `%QX100.0 to %QX100.2`.

## Expected result

60 s from start, under either program: 20 boxes spawned, 17 taken by `REM_01`, three still on the
belt, none lost. The run summary line reads
`spawned 20, removed {'box': 17}, lost {}; by remover {'REM_01': {'box': 17}}`.

## Try this

- Inject `stuck_on` on `PB_STOP` (right-click it in the viewer): the contact reads pressed, the
  NC binding turns that into FALSE, and the line stops. Now clear it, restart, and inject
  `stuck_off`: the contact can never open, `PB_STOP_DI` stays TRUE, and pressing the button no
  longer stops the line. That failure is invisible to this program, which is why real stop
  circuits are hard-wired and monitored.
- Inject `stuck_off` on `CONV_01` (its running feedback). The belt still runs, but `PL_RUN` goes
  dark and the box feed stops after the permissive drops.
- Inject `jammed` on `CONV_01`. The boxes stop, yet the running feedback stays on (a jammed belt
  reports honestly that its drive is running). What would you add to the program to detect a
  jam? (A photo eye at the discharge and a timer is the usual answer.)
- Remove `invert: true` from the stop button binding and run it again. What does the PLC see, and
  why is a normally open stop button a bad idea?
