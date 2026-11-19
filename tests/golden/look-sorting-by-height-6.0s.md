# sorting-by-height at t = 6.00 s (tick 300)

Two-lane box sorter. Tall boxes diverted by pusher, short boxes pass through. 7 components, 2 parts in the scene.

## Checks

Declared, graded at 60.0 s by `openfactory test` (live: `GET /api/checks`); at 6.00 s the run has not.
- `parts_reached`: short to REM_01, and nowhere else. So far REM_01 has short 0.
- `parts_reached`: tall to REM_02, and nowhere else. So far REM_02 has tall 0.
- `none_lost`: no part falls out of the scene.
- `max_cycle_s`: every part is removed within 10.0 s of being made; the oldest part now is `P000001`, 4.96 s.

## Active faults

None.

## Components

- `CONV_01` (belt_conveyor): running at 0.50 m/s.
  CONV_01_RUN_DO = true, CONV_01_SPD_AO = 100.000, CONV_01_RUN_DI = true
- `PE_01` (photo_eye): beam clear, detect off.
  PE_01_DI = false
- `CYL_01` (pusher): retracted, plate 0.00 m out of 0.60 m.
  CYL_01_EXT_DO = false, CYL_01_EXT_DI = false, CYL_01_RET_DI = true
- `EMT_01` (box_emitter): enabled, 2 parts made.
  EMT_01_EN_DO = true
- `REM_01` (part_remover): removed 0 parts.
- `CONV_02` (belt_conveyor): running at 0.50 m/s.
  CONV_02_RUN_DO = true, CONV_02_SPD_AO = 100.000, CONV_02_RUN_DI = true
- `REM_02` (part_remover): removed 0 parts.
- Scene tags: SIM_HEARTBEAT_DI = false

## Program (watch values)

Published by the stand-in script with `io.watch`; not tags: no address, not exported, not seen by a protocol.

- step = 20
- boxes_in_transit = 0

## Parts

- `P000001` short, on CONV_01, 2.58 m from the infeed end, moving 0.50 m/s
- `P000002` tall, on CONV_01, 1.11 m from the infeed end, moving 0.50 m/s

## Changes in the last 5.0 s (after 1.00 s, up to 6.00 s)

- 1.04 s (tick 52): EMT_01_EN_DO = true
- SIM_HEARTBEAT_DI: true at 1.50 s to false at 6.00 s (10 changes)
