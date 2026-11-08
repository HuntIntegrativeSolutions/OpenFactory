# Tank level, on/off

`scenes/tank-level-onoff.yaml` is the first process scene (PLAN 3.3): a pump lifts water from a
sump into a raised head tank, and a hand valve drains the head tank back into the sump by
gravity, so the water circulates and nothing is supplied or lost. The PLC keeps the head tank
between its low and high level switches: the pump starts at low and stops at high. It is the
level control most plants start with, and the first exercise in the flow network
([ADR-0005](../adr/ADR-0005-flow-network-solver.md)).

Training and demo use only. Not a safety-rated simulation, and the program is not a safety-rated
program.

## The plant

| Component | Type | What it does |
|---|---|---|
| `TK_01` | tank | The sump: 1.0 m across, 1.2 m tall, 0.8 m of water at start |
| `P_01` | pump | 12 m³/h at 4 m (shutoff 5.3 m); about 14 m³/h against this lift |
| `PIPE_01` | pipe | 40 mm, up 2.6 m and across into the head tank's top |
| `TK_02` | tank | The head tank: 0.3 m across, 1.0 m tall, on a stand 1.6 m up; LSH at 0.75 m, LSL at 0.25 m |
| `HV_02` | valve_control | A hand valve, Kv 12, held fully open (no PLC port): drains about 3.7 m³/h |
| `PIPE_02` | pipe | 40 mm, from the hand valve back into the sump's top |

## I/O

The same addresses in both lockfiles; under `int16_scaled` the level reads 0 to 32767 for 0 to
1 m.

| Tag | Modbus | OpenPLC | Meaning |
|---|---|---|---|
| `P_01_RUN_DI` | DI:0 | `%IX100.0` | Pump running feedback (auxiliary contact) |
| `TK_02_LSH_DI` | DI:1 | `%IX100.1` | Head tank high level switch (LSH), on at 0.75 m |
| `TK_02_LSL_DI` | DI:2 | `%IX100.2` | Head tank low level switch (LSL), on at 0.25 m |
| `SIM_HEARTBEAT_DI` | DI:3 | `%IX100.3` | Simulator heartbeat, 1 Hz |
| `P_01_RUN_DO` | CO:0 | `%QX100.0` | Pump run command |
| `TK_02_LT_AI` | IR:0 | `%IW100` | Head tank level, m (0 to 1) |

The switches turn off 0.02 m back from where they turn on, so a level at a switch point does
not chatter.

## Sequence

1. The low level switch (LSL) starts the pump.
2. The pump runs on (a seal-in) until the high level switch (LSH) stops it, so the level swings
   between the two switches: on/off control with the switches as the deadband.
3. If both switches read on at once (a wiring or switch fault: the tank cannot be both low and
   high), the pump stops.

## Run it

```bash
uv run openfactory run scenes/tank-level-onoff.yaml --script examples/pyplc/tank-level-onoff.py
uv run openfactory test scenes/tank-level-onoff.yaml --script examples/pyplc/tank-level-onoff.py
```

With OpenPLC, follow [the quickstart](openplc-quickstart.md) for the container, then export
`scenes/tank-level-onoff.yaml` with `--analog-encoding int16_scaled`, configure with
`examples/openplc/tank-level-onoff.st`, and run with `--bind 0.0.0.0 --analog-encoding
int16_scaled` (the commands are in the [guides index](README.md)). Slave Devices then shows
`%IX100.0 to %IX100.3`, `%QX100.0` and `%IW100`.

## Expected result

Under the stand-in script the head tank drains from 0.5 m to LSL, and the pump starts at 20.2 s
and stops at LSH at 33.1 s; then a cycle every 51.6 s, 13 s filling and 39 s draining. The level
stays between 0.243 and 0.759 m. The scene's checks:

| Check | 60 s | 300 s |
|---|---|---|
| `level_within TK_02: 0.2 to 0.8 m` | PASS | PASS |
| `never_overflow TK_01, TK_02` | PASS | PASS |
| `never_dry P_01` | PASS | PASS |
| `max_starts P_01: 8` | 1 start | 6 starts |

`max_starts` catches a program that short-cycles the pump: one that starts it at LSL and stops it
when LSL clears, with no seal-in, starts it every few seconds, 9 times in the first minute.

## Try this

- Write the short-cycling program above and run `openfactory test`: `max_starts` fails within the
  first minute (9 starts, limit 8), while the level stays in its band.
- Inject `stuck_off` on the high switch (`--fault TK_02=stuck_off:port=hi_sw --fault-at 25`): the
  program never stops the pump and the head tank overflows (`never_overflow` fails: 54 L over in
  60 s). Add a
  backup: stop the pump when `TK_02_LT_AI` reads above 0.85 m, and alarm when the level analog
  and the switch disagree.
- Inject `jammed` on `P_01` (a seized pump): `P_01_RUN_DI` stays on, but the level keeps falling.
  Alarm when the pump has run for 10 s without the level rising.
