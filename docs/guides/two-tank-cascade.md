# Two tanks in series, cascade

`scenes/two-tank-cascade.yaml` puts two tanks in series (PLAN 3.3): a supply fills the upper tank
through a control valve, the upper tank drains by gravity through a narrow line into the top of
the lower one, and a consumer draws from the lower tank. Because the line enters the lower tank
from above, the flow between them depends on the upper level alone: non-interacting tanks, two
lags in series. At 10 s the consumer's demand steps from 2 to 3 m³/h. The PLC holds the lower
tank at 0.8 m with cascade control: an outer PI on the lower level sets the upper level's
setpoint, and an inner PI on the upper level positions the valve.

Training and demo use only. Not a safety-rated simulation, and the program is not a safety-rated
program.

## The plant

| Component | Type | What it does |
|---|---|---|
| `SUP_01` | supply | Process water at 2 bar, 4.8 m up |
| `FV_01` | valve_control | Inlet control valve: Kv 6, equal percentage, 10 s full stroke; starts at 63 % |
| `TK_01` | tank | Upper tank: 0.35 m across, 2.0 m tall, 2.4 m up; starts at 0.165 m; LSH at 1.8 m |
| `LT_01` | transmitter | Upper level, 0 to 2 m, 1 s lag, 0.2 % noise |
| `PIPE_12` | pipe | 25 mm gravity line from the upper tank's bottom into the lower tank's top |
| `TK_02` | tank | Lower tank: 0.4 m across, 1.5 m tall, its top level with the upper tank's bottom; starts at 0.8 m; LSH at 1.35 m |
| `LT_02` | transmitter | Lower level, 0 to 1.5 m, 1 s lag, 0.2 % noise |
| `DR_01` | drain | The consumer: 2 m³/h, then 3 m³/h from 10 s on |

The upper level sets the flow between the tanks: 0.165 m passes 2 m³/h, 0.34 m passes 3.

## I/O

| Tag | Modbus (int16 / float32) | OpenPLC | Meaning |
|---|---|---|---|
| `TK_01_LSH_DI` | DI:0 | `%IX100.0` | Upper tank high level switch (LSH) |
| `TK_02_LSH_DI` | DI:1 | `%IX100.1` | Lower tank high level switch (LSH) |
| `SIM_HEARTBEAT_DI` | DI:2 | `%IX100.2` | Simulator heartbeat, 1 Hz |
| `FV_01_ZT_AI` | IR:0 | `%IW100` | Valve position feedback (ZT), 0 to 100 % |
| `TK_01_LT_AI` | IR:1 / IR:2 | `%IW101` | Upper tank level, 0 to 2 m |
| `TK_02_LT_AI` | IR:2 / IR:4 | `%IW102` | Lower tank level, 0 to 1.5 m |
| `FV_01_POS_AO` | HR:0 | `%QW100` | Valve position command, 0 to 100 % |

## Sequence

1. Read both levels.
2. Outer loop: PI on the lower level (setpoint 0.8 m, KP −4 m/m, TR 8 s). Its output is the
   upper level's setpoint, limited to 0.05 to 1.6 m.
3. Inner loop: PI on the upper level against that setpoint (KP −400 %/m, TR 3 s). Its output is
   the valve position, limited to 0 to 100 %. Both are OpenPLC PID blocks, whose error is
   PV − SP, so both gains are negative.
4. Interlock: while either high switch is on, close the valve and hold both blocks in manual.
5. For the first second run both in manual at the starting point, then switch to automatic
   bumplessly. After that the outer block's manual value tracks the upper level, so leaving the
   interlock is bumpless too.

## Run it

```bash
uv run openfactory run scenes/two-tank-cascade.yaml --script examples/pyplc/two-tank-cascade.py
uv run openfactory test scenes/two-tank-cascade.yaml --script examples/pyplc/two-tank-cascade.py
```

With OpenPLC, follow [the quickstart](openplc-quickstart.md) for the container, then export with
`--analog-encoding int16_scaled`, configure with `examples/openplc/two-tank-cascade.st`, and run
with `--bind 0.0.0.0 --analog-encoding int16_scaled` (the commands are in the
[guides index](README.md)). As in [Tank level, PID](tank-level-pid.md), check the first minute
on your runtime before trusting the gains: they assume `TIME_TO_REAL(T#20ms)` is 0.02.

## Expected result

Under the stand-in script the lower tank holds 0.8 m. After the load step at 10 s it dips to
0.779 m at 27 s while the outer loop raises the upper setpoint and the upper level climbs to
0.48 m at 37 s, refilling the lower tank. The lower level is back within 1 cm by 43 s and settles
with the upper level at 0.34 m and the valve at 80.7 %. The scene's checks:

| Check | 60 s | 300 s |
|---|---|---|
| `level_within TK_02: 0.78 to 0.82 m after 40 s` | 0.788 to 0.802 m | 0.788 to 0.809 m |
| `never_overflow TK_01, TK_02` | PASS | PASS |

The band starts at 40 s, once the step has settled. A single PI loop from the lower level
straight to the valve fails it with every tuning tried (KP −50 to −600 %/m, TR 10 to 60 s): two
lags in series make a tight single loop oscillate (0.36 to 1.0 m) and a gentle one too slow to
recover in time (0.70 to 0.74 m at 60 s). The inner loop takes the first lag out of the outer
loop's way.

## Try this

- Replace the cascade with a single PI loop and try to pass the band.
- Speed up the outer loop (KP −6, TR 12 s): it settles faster in the first minute, then rings
  for good: over 300 s the lower level swings 0.59 to 0.88 m. A cascade's outer loop has to be
  several times slower than its inner one.
- Inject `jammed` on `FV_01` (`--fault FV_01=jammed --fault-at 5`): the inner loop winds its
  output up while the stem stays put, and the lower level settles low (0.73 to 0.75 m). Alarm
  when the valve's command and feedback disagree.
