# Batch mix and heat

`scenes/batch-mix-heat.yaml` is the batch recipe of PLAN 3.3: water and a warm syrup fill a
mixing tank to a level each, an immersion heater brings the batch to 50 °C, and the PLC holds it
there. It is a sequence rather than a loop, a fill, fill, heat and hold, and it is graded on what
ends up in the tank: the share of each ingredient and the temperature, as a recipe would be
(the flow network's mixing and heat balance, [ADR-0005](../adr/ADR-0005-flow-network-solver.md)
decision 8).

Training and demo use only. Not a safety-rated simulation, and the program is not a safety-rated
program.

## The plant

| Component | Type | What it does |
|---|---|---|
| `SUP_01` | supply | Process water at 2 bar, 15 °C, ingredient `water` |
| `XV_01` | valve_onoff | Water inlet valve, Kv 4, 1 s stroke, closed limit switch |
| `SUP_02` | supply | Syrup at 2 bar, 80 °C, ingredient `syrup` (kept warm, as syrups are, to flow) |
| `XV_02` | valve_onoff | Syrup inlet valve, Kv 4, 1 s stroke, closed limit switch |
| `PIPE_01`, `PIPE_02` | pipe | 25 mm, each into the tank's top |
| `TK_01` | tank | The mixing tank: 0.4 m across, 0.6 m tall, empty at start; LSH at 0.5 m |
| `LT_01` | transmitter | Level, 0 to 0.6 m, 0.2 s lag |
| `TT_01` | transmitter | Temperature, 0 to 100 °C, 1 s lag |
| `HTR_01` | heater | 45 kW immersion heater, uncovered below 0.08 m, 15 W/K loss to ambient |
| `PL_01` | pilot_light | Batch ready |

The tank is small and the heater large so a whole batch fits in the minute the checks run: 20 L
heated by 14 K in 25 s. The tank is well mixed (one temperature, one set of fractions), with no
agitator to run.

## I/O

Under `int16_scaled` the level reads 0 to 32767 for 0 to 0.6 m and the temperature for 0 to
100 °C. Addresses are the same in both lockfiles.

| Tag | Modbus | OpenPLC | Meaning |
|---|---|---|---|
| `TK_01_LSH_DI` | DI:0 | `%IX100.0` | Mixing tank high level switch (LSH), on at 0.5 m |
| `XV_01_ZSC_DI` | DI:1 | `%IX100.1` | Water inlet valve closed (ZSC) |
| `XV_02_ZSC_DI` | DI:2 | `%IX100.2` | Syrup inlet valve closed (ZSC) |
| `SIM_HEARTBEAT_DI` | DI:3 | `%IX100.3` | Simulator heartbeat, 1 Hz |
| `HTR_01_ON_DO` | CO:0 | `%QX100.0` | Heater on command |
| `PL_READY_DO` | CO:1 | `%QX100.1` | Batch ready pilot light |
| `XV_01_OPEN_DO` | CO:2 | `%QX100.2` | Water inlet valve open command |
| `XV_02_OPEN_DO` | CO:3 | `%QX100.3` | Syrup inlet valve open command |
| `TK_01_LT_AI` | IR:0 | `%IW100` | Mixing tank level, m (0 to 0.6) |
| `TK_01_TT_AI` | IR:1 | `%IW101` | Mixing tank temperature, °C (0 to 100) |

## Sequence

The program keeps the active step in `STEP`.

| Step | Actions | Next when |
|---|---|---|
| 0 Idle | everything off | 1 s after power-up (the transmitters' first readings) and the tank is empty (below 0.02 m) |
| 10 Fill water | `XV_01` open | the level reaches 0.10 m |
| 20 Fill syrup | `XV_02` open | the level reaches 0.16 m |
| 30 Heat | heater on | the temperature reaches 50 °C |
| 40 Hold | batch ready lit; heater on below 49.5 °C, off at 50 °C | (held) |

In every step the high level switch closes both inlet valves, and the heater runs only while
the level is at 0.10 m or above, so it is never switched on uncovered. The recipe is 0.06 m of
syrup on 0.10 m of water, 37.5 % syrup by volume; each valve's 1 s close lets a little more in
after its setpoint.

## Run it

```bash
uv run openfactory run scenes/batch-mix-heat.yaml --script examples/pyplc/batch-mix-heat.py
uv run openfactory test scenes/batch-mix-heat.yaml --script examples/pyplc/batch-mix-heat.py
```

With OpenPLC, follow [the quickstart](openplc-quickstart.md) for the container, then export
`scenes/batch-mix-heat.yaml` with `--analog-encoding int16_scaled`, configure with
`examples/openplc/batch-mix-heat.st`, and run with `--bind 0.0.0.0 --analog-encoding
int16_scaled` (the commands are in the [guides index](README.md)). Slave Devices then shows
`%IX100.0 to %IX100.3`, `%QX100.0 to %QX100.3` and `%IW100 to %IW101`.

## Expected result

Under the stand-in script the water fill runs from 1.0 to 9.9 s (0.103 m), the syrup fill to
14.8 s (0.163 m, 33 % syrup, 36.5 °C), and the heat to 39.4 s, when the batch reaches 50.5 °C
and the light comes on. The valves' close brings the batch to 0.169 m and 35.5 % syrup. In the
hold the batch cools slowly (15 W/K) and the heater restarts once in 300 s. The scene's checks:

| Check | 60 s | 300 s |
|---|---|---|
| `level_within TK_01: 0.15 to 0.19 m after 20 s` | 0.169 m | 0.169 m |
| `never_overflow TK_01` | PASS | PASS |
| `fractions_within TK_01: syrup 32 to 39 % after 20 s` | 35.54 % | 35.54 % |
| `temperature_within TK_01: 48 to 52 °C after 45 s` | 50.40 to 50.48 °C | 49.49 to 50.48 °C |

The recipe checks close the easy passes (`tests/integration/test_process_scenes.py`): filling
water alone to 0.16 m fails `fractions_within` (0 % syrup), as does opening both valves together
(they pass the same flow, so the batch is half syrup); never heating fails
`temperature_within` (38 °C), and so does a heater left on (53 °C by 45 s, 61 °C by 60 s). An
empty tank fails both recipe checks, so a program that drains the batch does not pass on what an
empty tank last held.

## Try this

- Inject `stuck_off` on the heater (`--fault HTR_01=stuck_off --fault-at 12`): the batch never
  warms past 38 °C, the sequence waits in step 30 and `temperature_within` fails. Add a heat
  timeout: alarm when step 30 has lasted 45 s.
- Inject `drift` on the temperature transmitter (`--fault TT_01=drift:rate=0.5 --fault-at 12`):
  it reads high, the program ends the heat early, and the real batch holds at 43.5 °C. A second
  temperature element, or the heater's own `temp` reading, would catch it.
- Inject `jammed` on the syrup valve (`--fault XV_02=jammed --fault-at 12`): it stays open
  whatever the command, the LSH interlock cannot close it, and the tank overflows (15 L in 60 s).
  Watch `XV_02_ZSC_DI`: alarm when the valve is commanded shut and not closed after 2 s.
- Add a discharge step: a bottom outlet valve to a drain, opened on a pushbutton once the batch
  is ready, and back to step 0 when the tank is empty.
