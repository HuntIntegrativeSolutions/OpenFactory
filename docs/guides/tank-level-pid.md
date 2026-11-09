# Tank level, PID

`scenes/tank-level-pid.yaml` closes a level loop through the PLC (PLAN 3.3): a supply fills a tank
through a control valve, a consumer draws from it, and the PLC holds the level at 1.0 m with a PI
loop, the level transmitter in and the valve position out. At 20 s the consumer's demand steps
from 2 to 4.5 m³/h, and the loop has to bring the level back. The OpenPLC program uses OpenPLC's
own PID function block.

Training and demo use only. Not a safety-rated simulation, and the program is not a safety-rated
program.

## The plant

| Component | Type | What it does |
|---|---|---|
| `SUP_01` | supply | Process water at 2 bar, 3.8 m up |
| `FV_01` | valve_control | Inlet control valve: Kv 6, equal percentage, 10 s full stroke, 0.5 s lag, fails closed |
| `PIPE_01` | pipe | 40 mm, from the valve into the tank's top |
| `TK_01` | tank | 0.6 m across, 2.0 m tall, on a stand 1.5 m up; starts at 1.0 m; LSH at 1.8 m |
| `LT_01` | transmitter | Level, 0 to 2 m, 1 s lag, noise 0.2 % of span |
| `PIPE_02` | pipe | 40 mm, from the tank's bottom to the consumer |
| `DR_01` | drain | The consumer: 2 m³/h, then 4.5 m³/h from 20 s on (full demand above 1 m of head) |

## I/O

Under `int16_scaled` the analogs read 0 to 32767 over the ranges below. The level transmitter's
input register differs between the encodings (a float32 takes two registers).

| Tag | Modbus (int16 / float32) | OpenPLC | Meaning |
|---|---|---|---|
| `TK_01_LSH_DI` | DI:0 | `%IX100.0` | Tank high level switch (LSH), on at 1.8 m |
| `SIM_HEARTBEAT_DI` | DI:1 | `%IX100.1` | Simulator heartbeat, 1 Hz |
| `FV_01_ZT_AI` | IR:0 | `%IW100` | Valve position feedback (ZT), 0 to 100 % |
| `TK_01_LT_AI` | IR:1 / IR:2 | `%IW101` | Tank level (LT), 0 to 2 m |
| `FV_01_POS_AO` | HR:0 | `%QW100` | Valve position command, 0 to 100 %; starts at 66 % |

## Sequence

1. Read the level from the transmitter.
2. PI control: the valve position is KP·(e + Σe·dt / TR), e = level − 1.0 m, KP −400 %/m, TR 6 s.
   OpenPLC's PID computes its error as PV − SP, so a loop that opens the valve on a low level
   needs a negative gain.
3. Limit the output to 0 to 100 % and write it to the valve. The block has no output limit or
   anti-windup of its own.
4. Interlock: while LSH is on, close the valve and hold the PID in manual at 0 %, so it cannot
   wind up while it is overridden.
5. For the first second, run the PID in manual at the valve's starting position, tracking the
   level, then switch to automatic. The level input reads 0 until its first poll, and starting in
   automatic on that would slam the valve shut: a bumpless start.

## Run it

```bash
uv run openfactory run scenes/tank-level-pid.yaml --script examples/pyplc/tank-level-pid.py
uv run openfactory test scenes/tank-level-pid.yaml --script examples/pyplc/tank-level-pid.py
```

With OpenPLC, follow [the quickstart](openplc-quickstart.md) for the container, then export
`scenes/tank-level-pid.yaml` with `--analog-encoding int16_scaled`, configure with
`examples/openplc/tank-level-pid.st`, and run with `--bind 0.0.0.0 --analog-encoding
int16_scaled` (the commands are in the [guides index](README.md)). The program assumes OpenPLC's
`TIME_TO_REAL(T#20ms)` is 0.02, seconds, as the stand-in script does; check the loop's first
minute on your runtime before trusting the gains.

## Expected result

Under the stand-in script the level holds at 1.0 m with the valve at 62.5 %. After the load step
at 20 s it dips to 0.978 m at 35 s and is back within 1 cm of the setpoint by 46 s, the valve now
at 83.8 % passing 4.5 m³/h. The scene's checks:

| Check | 60 s | 300 s |
|---|---|---|
| `level_within TK_01: 0.98 to 1.02 m after 45 s` | 0.989 to 1.008 m | 0.989 to 1.009 m |
| `never_overflow TK_01` | PASS | PASS |
| `max_reversals FV_01: 15` | 1 reversal | 12 reversals |

The band starts at 45 s, once the step has settled: it grades where the loop ends up. A P-only
loop at the same gain (no integral) settles below the setpoint and fails it (0.956 to 0.966 m
over 300 s): the integral is what removes the offset. A program that drives the valve fully open
below the setpoint and shut above it holds the band too (0.985 to 1.008 m): the valve's 10 s
stroke turns that on/off output into a steady ramp, which acts like integral control on a tank.
It fails `max_reversals` instead: its command turns back 1151 times in 60 s, and a control
valve driven like that wears out its packing and actuator. The check counts only turns back by
more than 10 % of the span, so the PI's own small trims, the level noise times its gain, are
not counted. A real on/off level loop would use an on/off valve, as `tank-level-onoff` does.

## Try this

- Set TR to 1 s and the loop oscillates after the step, each swing larger than the last (±5 cm
  by 160 s); set it to 60 s and it takes minutes to recover (still 3 mm low at 180 s). Then try
  KP −100.
- Inject `signal_fail` on the transmitter (`--fault LT_01=signal_fail --fault-at 30`): the level
  input reads 3.6 mA, below its range (under `int16_scaled`, 0 m), and the loop drives the valve
  wide open; the level climbs to 1.33 m in 90 s. Detect an input outside its range and put the
  loop in manual, holding the last good output.
- Inject `jammed` on `FV_01` before the step (`--fault FV_01=jammed --fault-at 10`): the
  command rises but `FV_01_ZT_AI` does not follow. Alarm when command and feedback differ by more
  than 5 % for 15 s.
