# PlantPAx tank level

`scenes/plantpax-tank-level.yaml` is the plant of the PlantPAx demo (PLAN 3.4):
[`tank-level-pid`](tank-level-pid.md)'s level loop plus an outlet transfer pump, so each of the
four PlantPAx objects has a real component to act on: `P_AIn` on the level transmitter, `P_PIDE`
as the level controller, `P_ValveC` on the control valve and `P_Motor` on the pump. It runs three
ways: under the stand-in script, under OpenPLC, and (once PLAN 2.1 and 2.2 connect OpenFactory to a
Logix controller) under the PlantPAx L5X that `openfactory export --format plantpax` writes.

Training and demo use only. Not a safety-rated simulation, and neither program is a safety-rated
program.

## The plant

| Component | Type | What it does |
|---|---|---|
| `SUP_01` | supply | Process water at 2 bar |
| `FV_01` | valve_control | Tank inlet level control valve: Kv 6, equal percentage, 10 s stroke, 0.5 s lag, fails closed |
| `TK_01` | tank | 0.6 m across, 2 m tall, on a stand 1.5 m up, 1.0 m at start; LSH 1.8 m, LSL 0.3 m |
| `LT_01` | transmitter | Level, 0 to 2 m, 1 s lag, 0.2 % noise |
| `DR_01` | drain | A consumer drawing 2 m³/h |
| `P_01` | pump | The transfer pump, 1.5 m³/h at 3 m design point, from the tank's bottom up to a header at 3 m: about 2.75 m³/h here |
| `DR_02` | drain | The transfer header the pump delivers to |

## I/O

The same addresses in both lockfiles; under `int16_scaled` the level reads 0 to 32767 for 0 to
2 m and the valve's position 0 to 32767 for 0 to 100 %.

| Tag | Modbus | OpenPLC | Meaning |
|---|---|---|---|
| `P_01_RUN_DI` | DI:0 | `%IX100.0` | Transfer pump running feedback |
| `TK_01_LSH_DI` | DI:1 | `%IX100.1` | High level switch (LSH), 1.8 m |
| `TK_01_LSL_DI` | DI:2 | `%IX100.2` | Low level switch (LSL), 0.3 m |
| `SIM_HEARTBEAT_DI` | DI:3 | `%IX100.3` | Simulator heartbeat, 1 Hz |
| `P_01_RUN_DO` | CO:0 | `%QX100.0` | Transfer pump run command |
| `FV_01_ZT_AI` | IR:0 | `%IW100` | Valve position feedback, % |
| `TK_01_LT_AI` | IR:1 | `%IW101` | Level, m (0 to 2) |
| `FV_01_POS_AO` | HR:0 | `%QW100` | Valve position command, % |

## Control

1. A PI loop holds the level at 1.0 m through the valve: OpenPLC's PID block in the ST program,
   `P_PIDE` in the PlantPAx L5X, the same tuning (in the PIDE's units: gain 8 %/%, 10 repeats a
   minute).
2. The high level switch closes the valve and holds the controller at 0 %.
3. The transfer pump starts 20 s after power-up, the load step, and the low level switch stops
   it: its interlock against running the tank dry. **After an LSL trip the programs differ:** the
   OpenPLC and stand-in programs run the pump again as soon as LSL clears (their run command is
   "load delay done AND NOT LSL"); the PlantPAx L5X gives `P_Motor` one start command, at 20 s,
   so after a trip the pump stays off until someone starts it again (the faceplate, or a new
   `PCmd_Start`). The scene's checks do not see the difference: under the loop the level never
   falls below 0.98 m, far above LSL at 0.3 m ([details](../plantpax.md#the-pump-after-a-low-level-trip)).
4. The first second starts the loop in manual at the valve's starting position (bumpless).

## Run it

```bash
uv run openfactory run scenes/plantpax-tank-level.yaml --script examples/pyplc/plantpax-tank-level.py
uv run openfactory test scenes/plantpax-tank-level.yaml --script examples/pyplc/plantpax-tank-level.py
```

With OpenPLC, follow [the quickstart](openplc-quickstart.md) for the container, then export with
`--analog-encoding int16_scaled`, configure with `examples/openplc/plantpax-tank-level.st`, and run
with `--bind 0.0.0.0 --analog-encoding int16_scaled` (the commands are in the
[guides index](README.md)).

For Studio 5000, export the PlantPAx L5X and follow [the import checks](../plantpax.md):

```bash
uv run openfactory export scenes/plantpax-tank-level.yaml --format plantpax -o plantpax-tank-level.L5X
```

## Expected result

Under the stand-in script the level holds 1.0 m with the valve near 65 %. The pump starts at
20 s and draws 2.75 m³/h; the level dips to 0.98 m and the valve opens to about 85 %, peaking at
99 % during the recovery, and the level is back in its band by 45 s. The scene's checks:

| Check | 60 s | 300 s |
|---|---|---|
| `level_within TK_01: 0.98 to 1.02 m after 45 s` | 0.987 to 1.009 m | 0.987 to 1.009 m |
| `never_overflow TK_01` | PASS | PASS |
| `never_dry P_01` | PASS | PASS |
| `max_starts P_01: 2` | 1 start | 1 start |
| `max_reversals FV_01: 18` | 1 reversal | 14 reversals |

`max_reversals` is graded on the 300 s run (PLAN §9 rule 10).

## Try this

- Inject `drift` on `LT_01` (`--fault LT_01=drift:rate=0.01 --fault-at 60`): the loop follows
  the drifting reading and the real level walks away from 1.0 m (down to 0.76 m by 120 s;
  `level_within` fails). In the PlantPAx version, compare `P_AIn`'s value with a second
  measurement before trusting it.
- Inject `jammed` on `P_01` at 30 s (`--fault P_01=jammed --fault-at 30`): the run feedback stays
  on while nothing flows, and the loop simply closes the valve back (every check still passes).
  `P_Motor`'s fail-to-start alarm does not see it, since the pump reports running; a flow
  transmitter or the valve's position falling back would.
