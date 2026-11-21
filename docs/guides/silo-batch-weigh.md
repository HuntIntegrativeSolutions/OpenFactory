# Silo batch weighing

`scenes/silo-batch-weigh.yaml` is the first bulk-solids scene (PLAN 5.7a): a rotary airlock under
a flour silo feeds a screw conveyor that fills a weigh hopper on load cells to a batch weight,
and a slide gate under the hopper drops each batch into a mixer. A delivery line refills the
silo through a slide gate on its roof. The material is a mass, never parts: the solids network
moves kilograms a tick, and the screw carries what it takes for its travel time
([ADR-0006](../adr/ADR-0006-bulk-solids-mass-flow.md)), so the PLC meets the batching problem a
real one does: what is already on the screw when the feeder stops still arrives.

Training and demo use only. Not a safety-rated simulation, and the program is not a safety-rated
program. The scene models no dust, no explosion, no structural load and no safety function.

## The plant

| Component | Type | What it does |
|---|---|---|
| `LN_01` | bulk_supply | The flour delivery line on the silo roof, never exhausted |
| `SG_01` | slide_gate | The silo fill gate: 15 kg/s open, 1 s to open or close |
| `SILO_01` | bin | The silo: 2.0 m across, 3.0 m of wall over a 1.5 m cone, about 6,200 kg of flour full; 4,000 kg at start, on load cells with 1,800 kg of tare |
| `RV_01` | rotary_airlock | Under the silo: 12 kg/s at full speed, with a speed command |
| `SC_01` | screw_conveyor | 3.0 m long, rising 10°: 10 kg/s, 5 s from inlet to outlet; it holds at most 50 kg |
| `WH_01` | bin | The weigh hopper: 1.0 m across, 0.8 m of wall over a 0.6 m cone, about 450 kg full, 180 kg of tare, load cells read to 750 kg |
| `SG_02` | slide_gate | Under the hopper: 25 kg/s open, 0.8 s to open or close |
| `MX_01` | bulk_discharge | The mixer's flour inlet, taking whatever arrives |

The material is `flour`, 550 kg/m³, declared once in the scene's `materials:`.

## I/O

The same addresses in both lockfiles. Under `int16_scaled` the silo's weight reads 0 to 32767
for 0 to 10,000 kg (0.31 kg a count), the hopper's for 0 to 750 kg (0.023 kg a count), the silo
level for 0 to 4.5 m and the airlock speed for 0 to 100 %.

| Tag | Modbus | Meaning |
|---|---|---|
| `RV_01_PLUG_DI` | DI:0 | Airlock plug switch: its outlet cannot take what it brings |
| `RV_01_RUN_DI` | DI:1 | Airlock running feedback |
| `SC_01_PLUG_DI` | DI:2 | Screw plug switch: blocked at its outlet |
| `SC_01_RUN_DI` | DI:3 | Screw running feedback |
| `SC_01_ZSS_DI` | DI:4 | Screw zero-speed switch: on while it turns |
| `SG_01_ZSC_DI` | DI:5 | Silo fill gate closed |
| `SG_01_ZSO_DI` | DI:6 | Silo fill gate open |
| `SG_02_ZSC_DI` | DI:7 | Hopper discharge gate closed |
| `SG_02_ZSO_DI` | DI:8 | Hopper discharge gate open |
| `SILO_01_LSH_DI` | DI:9 | Silo high level switch (LSH) |
| `SILO_01_LSL_DI` | DI:10 | Silo low level switch (LSL) |
| `WH_01_LSH_DI` | DI:11 | Hopper high level switch (LSH) |
| `SIM_HEARTBEAT_DI` | DI:12 | Simulator heartbeat, 1 Hz |
| `RV_01_RUN_DO` | CO:0 | Airlock run command |
| `SC_01_RUN_DO` | CO:1 | Screw run command |
| `SG_01_OPEN_DO` | CO:2 | Silo fill gate open command (held) |
| `SG_02_OPEN_DO` | CO:3 | Hopper discharge gate open command (held) |
| `SILO_01_LT_AI` | IR:0 | Silo level from its outlet, m |
| `SILO_01_WT_AI` | IR:2 | Silo weight, kg (contents and tare) |
| `WH_01_WT_AI` | IR:4 | Hopper weight, kg (contents and tare) |
| `RV_01_SPD_AO` | HR:0 | Airlock speed command, % |

The analog addresses are the `float32` lockfile's (two registers each); the `int16_scaled`
lockfile has one register each, IR:0 to IR:2. Every port is in kg, kg/s, m or %: a plant's t/h
is a scaling in the PLC.

## Sequence

The stand-in PLC (`examples/pyplc/silo-batch-weigh.py`). This scene has no OpenPLC program yet.

1. **Silo refill**, on its own: the fill gate opens when the silo's net weight falls below
   3,500 kg and closes above 4,500 kg.
2. **Fast fill:** the screw runs and the airlock turns at full speed until the hopper's net
   weight reaches 185 kg (the batch, 250 kg, less 65 kg). The airlock feeds faster than the
   screw carries, so the screw runs full: 50 kg is still on it.
3. **Slow fill:** the airlock turns at 20 % (2.4 kg/s) until the net weight reaches 238 kg: what
   the screw carries at that rate in its 5 s (12 kg) is still to come.
4. **Emptying:** the airlock stops; the screw runs on for 6 s, so what was on it arrives, then
   stops.
5. **Discharge:** the hopper's gate opens until the hopper is empty, then closes; when its closed
   limit switch is made, the next batch starts. The airlock runs only while the screw runs and is
   not plugged.

## Run it

```bash
uv run openfactory run scenes/silo-batch-weigh.yaml --script examples/pyplc/silo-batch-weigh.py
uv run openfactory test scenes/silo-batch-weigh.yaml --script examples/pyplc/silo-batch-weigh.py
```

Any Modbus TCP PLC can run it from the exported I/O list (the commands are in the
[guides index](README.md)).

## Expected result

Under the stand-in script each batch weighs 250.09 kg, the first discharged at 35.7 s and then
one every 46.9 s. The silo stays between about 3,500 and 4,500 kg. The scene's checks:

| Check | 60 s | 300 s |
|---|---|---|
| `batch_within WH_01: 250 ± 5 kg` | 1 batch, 250.09 kg | 6 batches, 250.09 kg each |
| `never_empty SILO_01` | PASS | PASS |
| `never_full SILO_01` | PASS | PASS |

A batch is the hopper's mass when a link starts taking from it, so a program that opens the
discharge gate early, or one that stops the airlock at 250 kg and forgets the 50 kg on the
screw, fails `batch_within`.

## Try this

- Stop the airlock at the target with no preact (set both preacts to 0): every batch comes out
  300.45 kg, the screw's 50 kg heavy, and `batch_within` fails.
- Inject `bridge` on the silo (`--fault SILO_01=bridge --fault-at 20`): the airlock turns but
  nothing comes out; the hopper's weight stops rising. Alarm when the airlock has run for 10 s
  with no gain in weight.
- Inject `plugged` on the screw (`--fault SC_01=plugged --fault-at 20`): it stops conveying and
  `SC_01_PLUG_DI` comes on; the program stops the airlock, so the screw is never overfilled.
- Inject `trip` on the airlock (`--fault RV_01=trip --fault-at 20`): `RV_01_RUN_DI` drops while
  `RV_01_RUN_DO` is on. Alarm on a run command with no running feedback.
