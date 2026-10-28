# Two-lane merge

`scenes/two-lane-merge.yaml` merges two infeed lanes onto one outfeed through a turntable. Lane A
meets the deck straight on; lane B arrives from the side, so the deck turns 90 degrees to take each
lane-B box and turns back to discharge it. The program is a small state machine that alternates
lanes when both have a box waiting. It shows the `turntable` and `stopper` components (PLAN 2.5),
spring-raised stoppers (a binding with `invert: true`), and why a lane needs a stopper in front of
a turntable.

Training and demo use only. Not a safety-rated simulation, and the program is not a safety-rated
program.

## The plant

| Component | Type | What it does |
|---|---|---|
| `CONV_A` | belt conveyor | Lane A, 4 m along +x at 0.5 m/s, feeding the deck straight on |
| `CONV_B` | belt conveyor | Lane B, 3 m along -z at 0.5 m/s, feeding the deck from the side |
| `STP_A`, `STP_B` | stopper | Spring-raised blades 0.15 m before each lane's end |
| `PE_A`, `PE_B` | photo eye | Under each lane's waiting box |
| `TT_01` | turntable | 0.8 m deck centred at (4.4, 0); at 90 degrees it flows toward -z, taking lane B |
| `PE_TTA` | photo eye | Just past the deck centre, along z: sees boxes riding through at 0 degrees |
| `PE_TTB` | photo eye | Across the deck at z -0.16: sees a lane-B box arrive at the centre |
| `CONV_OUT` | belt conveyor | Merged outfeed, 3 m at 0.5 m/s |
| `EMT_A`, `EMT_B` | box emitter | A box every 8 s on each lane, named `a` and `b` |
| `REM_OUT` | part remover | End of the outfeed; counts `a` and `b` boxes |

Why the stoppers: a turned deck leaves a 0.15 m gap at the end of the lane it is not serving. The
headless model holds a box at the end of a conveyor whose connections are all out of line, but the
viewer's physics has no wall there, so the box would tip into the gap. Each lane's blade keeps its
next box clear of the deck until the deck lines up (`docs/components.md`, `turntable`).

## I/O

No analog tags, so both lockfiles give the same addresses. This scene has nine discrete inputs and
nine coils, so the ninth of each lands on the next OpenPLC byte: Modbus address N maps to
`%IX(100 + N / 8).(N MOD 8)`.

| Tag | Modbus | OpenPLC | Meaning |
|---|---|---|---|
| `CONV_A_RUN_DI` | DI:0 | `%IX100.0` | Lane A running |
| `CONV_B_RUN_DI` | DI:1 | `%IX100.1` | Lane B running |
| `CONV_OUT_RUN_DI` | DI:2 | `%IX100.2` | Outfeed running |
| `PE_A_DI` | DI:3 | `%IX100.3` | Lane A box waiting |
| `PE_B_DI` | DI:4 | `%IX100.4` | Lane B box waiting |
| `PE_TTA_DI` | DI:5 | `%IX100.5` | Box riding through the deck |
| `PE_TTB_DI` | DI:6 | `%IX100.6` | Lane-B box centred on the deck |
| `TT_01_AT0_DI` | DI:7 | `%IX100.7` | Turntable at 0 degrees |
| `TT_01_AT90_DI` | DI:8 | `%IX101.0` | Turntable at 90 degrees |
| `CONV_A_RUN_DO` | CO:0 | `%QX100.0` | Lane A run |
| `CONV_B_RUN_DO` | CO:1 | `%QX100.1` | Lane B run |
| `CONV_OUT_RUN_DO` | CO:2 | `%QX100.2` | Outfeed run |
| `EMT_A_EN_DO` | CO:3 | `%QX100.3` | Lane A box source enable |
| `EMT_B_EN_DO` | CO:4 | `%QX100.4` | Lane B box source enable |
| `STP_A_REL_DO` | CO:5 | `%QX100.5` | Lane A stopper: energize to release |
| `STP_B_REL_DO` | CO:6 | `%QX100.6` | Lane B stopper: energize to release |
| `TT_01_RUN_DO` | CO:7 | `%QX100.7` | Deck belt run |
| `TT_01_TURN_DO` | CO:8 | `%QX101.0` | Turn the deck to 90 degrees (released: back to 0) |

The stoppers' `raise` ports are bound `{tag: STP_A_REL_DO, invert: true}`: the blade is up when
the coil is off, the way a spring-raised stopper with a release solenoid fails safe on a power or
PLC loss.

## Sequence

1. Run both lanes and the outfeed; enable both box sources once all three report running for 1 s.
2. Idle at 0 degrees with the deck stopped. Serve a waiting box, alternating lanes when both wait.
3. Lane A: energize `STP_A_REL_DO` for 500 ms and run the deck. The box rides straight through
   onto the outfeed; the deck is free once `PE_TTA` has seen it come and go.
4. Lane B: turn to 90; at 90, energize `STP_B_REL_DO` for 500 ms and run the deck until `PE_TTB`
   sees the box centred; stop the deck and turn back; at 0, run the deck until `PE_TTA` has seen
   the box come and go.

The 500 ms release pulse lets exactly one box through: the blade takes 0.2 s to drop, and a box at
0.5 m/s is over the blade line for the next 0.6 s, so the pulse ends while the released box is
still over the line (it passes) and before the next box's front reaches it (it stops). OpenPLC's
polling delays both edges of the pulse alike.

Programs: `examples/openplc/two-lane-merge.st` and `examples/pyplc/two-lane-merge.py`.

## Run it

```bash
uv run openfactory run scenes/two-lane-merge.yaml --script examples/pyplc/two-lane-merge.py
```

With OpenPLC, follow [the quickstart](openplc-quickstart.md) for the container, then export,
configure with `examples/openplc/two-lane-merge.st`, and run with
`--bind 0.0.0.0 --analog-encoding int16_scaled` (commands in the [guides index](README.md)). Slave
Devices then shows `%IX100.0 to %IX101.0` and `%QX100.0 to %QX101.0`.

## Expected result

60 s from start, under either program: 16 boxes spawned, `REM_OUT` takes 6 `a` and 6 `b`, none
lost. The cycle settles at one lane-B box and one lane-A box every 8 s. The run summary line reads
`spawned 16, removed {'b': 6, 'a': 6}, lost {}; by remover {'REM_OUT': {'b': 6, 'a': 6}}`.

## Try this

- Shorten both emitters' `interval_s` to 3 s. The merge serves about one box every 3 to 4 s, so
  queues build on both lanes; watch the alternation keep them even.
- With boxes queued on lane A (3 s intervals), lengthen the release pulse to 1.5 s. The second box
  reaches the blade line 0.8 s into the pulse, while the blade is still down, so two boxes leave
  together and the second is still on the deck when the program thinks it is free.
- Inject `jammed` on `TT_01` while it turns. Neither `at_0` nor `at_90` ever makes, and the program
  waits forever. Add a turn timeout that stops the lanes and latches an alarm.
