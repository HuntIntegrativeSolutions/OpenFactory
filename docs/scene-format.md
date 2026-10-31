# Scene format reference

The full scene format is specified in `docs/PLAN.md` §4, and the machine-readable form is
`web/public/scene.schema.json` (generated from `src/openfactory/core/schema.py`). Components and
their parameters are in `docs/components.md`. This page covers the scene-level blocks that need
more explanation than §4 gives.

OpenFactory is a training and demo tool, not a safety-rated simulation.

## `checks:`: exercise checks (PLAN 2.11)

A scene may declare what a correct control program achieves. `openfactory test` runs the scene,
grades the run against these checks, prints one PASS or FAIL line per check and a verdict, and exits
1 if any check fails:

```bash
uv run openfactory test scenes/<scene>.yaml --script examples/pyplc/<scene>.py   # a stand-in script, fast
uv run openfactory test scenes/<scene>.yaml --plc --bind 0.0.0.0 --analog-encoding int16_scaled  # a real PLC, real time
```

The block goes after `plc:` and before `components:` in the shipped scenes:

```yaml
checks:
  duration_s: 60                                 # how long `test` runs (default 60)
  parts_reached: {REM_01: short, REM_02: [tall]} # each name reaches only that remover
  none_lost: true                                # no part falls out of the scene
  max_cycle_s: 12                                # spawn to remover, and oldest part left
```

Every key is optional. `--duration` on `test` overrides `duration_s`.

| Key | Passes when |
|---|---|
| `parts_reached` | For each remover and each part name under it: **at least one** part of that name reached that remover, **and no** part of that name reached any other remover. |
| `none_lost` | No part fell out of the scene (`lost` in the run summary is empty). |
| `max_cycle_s` | Every part a remover took spent at most this long from its spawn to its removal, **and** no part still in the scene at the end of the run is older than this. |

Two failure rules close the easy passes:

- **A stopped line fails `parts_reached`.** "At least one part reached it" is part of the check, so
  a program that never starts the belts, or a plant that delivers nothing, does not pass merely
  because nothing went the wrong way.
- **A jammed line fails `max_cycle_s`.** The limit applies to the parts still in the scene as well
  as to the parts removed, so a line where nothing is ever removed fails on the parts piling up
  behind the jam instead of passing with no removals to measure.

What the scene names is checked when it loads, so a typo fails loudly instead of grading wrong:
a `parts_reached` key must be a `part_remover` in the scene, each part name must be one some
`box_emitter` makes, and a name may appear under only one remover.

Setting `max_cycle_s`: run the scene under its stand-in script, note the longest
spawn-to-remover time and the oldest part left, and set the limit about 25 to 30 % above the
larger. The shipped scenes record the measured value in a comment on the line. Check it with a
300 s run as well (`--duration 300`, PLAN §9 rule 10): a line over capacity backs up, its longest
time keeps growing, and a 60 s run can pass it by stopping early.

Faults can be part of a graded run: `--fault ID=KIND[:key=value,...]` and `--fault-at T` inject one
at simulated time T, so an exercise can ask how a program handles a jammed pusher or a stuck eye.
`run` and `test` also print a statistics block (throughput, utilization, actuator cycles, dwell,
losses) just before the run summary line; `GET /api/stats` serves the same block while a scene
runs.

## `exam: switch`: exam mode (PLAN 2.11)

A DI binding may carry `exam: switch`:

```yaml
- id: PE_01
  type: photo_eye
  io:
    detect: {tag: PE_01_DI, exam: switch}
```

Without `--exam` it changes nothing. With `openfactory run SCENE --exam`, each such tag follows an
operator switch instead of the simulation, starting off. The viewer shows an exam panel (top
right) with one switch per tag, so an instructor can test a program against hand-driven inputs
while trainees watch the plant: the sensor still sees the parts in 3D, but the PLC sees the switch.
A switch takes effect between ticks and the program sees it on its next scan, so hold it for at
least one PLC scan, as you would a real switch. In exam mode state frames carry no fault markers
(no alarm-yellow label, a misaligned eye drawn where it belongs), so trainees have to find an
injected fault from the PLC's side; the instructor still sees active faults in the fault menu and
at `GET /api/fault`.

`GET /api/exam` lists the switches; `POST /api/exam` with `{"tag", "on"}` turns one. Turning a
switch is an instructor action, guarded like `/api/fault`: open on loopback, and beyond loopback
it needs the fault token (`--fault-token` or `[server] fault_token`). Only a DI binding takes
`exam`; `switch` is the only value. The shipped sorter marks `PE_01_DI`.

## Recordings (PLAN 2.12)

A recording (`run --record FILE.ofrec`) contains the full scene file, comments included, so sharing
a recording shares the scene.
