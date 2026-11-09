# PlantPAx demo: the L5X and what to verify on import (PLAN 3.4)

`openfactory export SCENE --format plantpax` writes a Studio 5000 partial-import L5X that
controls an OpenFactory scene with Rockwell's Library of Process Objects (4.x `P_` Add-On
Instructions). Two scenes have a PlantPAx map: `plantpax-tank-level` (all four objects, guide:
[PlantPAx tank level](guides/plantpax-tank-level.md)) and `tank-level-pid` (no pump, so no
`P_Motor`; its kit carries the file, PLAN 3.8). The design and the reasons for each choice are in
[the proposal](research/plantpax-demo-design.md), accepted 2026-11-09.

```bash
uv run openfactory export scenes/plantpax-tank-level.yaml --format plantpax -o plantpax-tank-level.L5X
```

This is a training and demo artifact, not a client deliverable: it needs HIS Controls PE review
before anyone else sees it, and OpenFactory is not a safety-rated simulation. The interlocks in it
are process interlocks for a demo, not safety functions.

**The live run waits on PLAN 2.1 and 2.2**, OpenFactory reaching a Logix controller's tags
(Logix Emulate or a bench controller). Until then the file is generated, imported and reviewed,
not run against the plant.

## What the file contains

| Part | Content |
|---|---|
| Controller tags | The scene's I/O, exactly as `--format l5x` writes them (BOOL and REAL, described, with ranges) |
| Instance tags | `DEMO_LT_101` (`P_AIn`), `DEMO_LIC_101` (`P_PIDE`), `DEMO_LV_101` (`P_ValveC`), `DEMO_P_101` (`P_Motor`), each with an ISA-5.1 description and **no `<Data>`** |
| Support tags | `DEMO_LOAD_TMR` (TIMER), `DEMO_LOAD_DELAY_MS` (DINT), `DEMO_LOAD_ONS` (BOOL): the demo's load step, the transfer pump starting 20 s after power-up |
| Program `PlantPAx_Demo` | `MainRoutine`: `Init` on the first scan only (`S:FS`), then `Control` every scan |
| Routine `Init` | One commented rung per object: ranges, the PI tuning carried over from `tank-level-pid` (gain 8 %/%, 10 repeats a minute, dependent form, reverse acting), the setpoint 1.0 m, the interlock CVs, program ownership and automatic mode |
| Routine `Control` | The scene's tags wired to the objects in signal order (level → `P_AIn` → `P_PIDE` → `P_ValveC` → valve; pump feedback and the low-level interlock → `P_Motor` → run), each object called once |

**It does not contain:** the Add-On Instruction definitions or the library's data types (Rockwell
distributes them; import the library into the project first), I/O modules (the plant is
OpenFactory, reached through tags), rationalized alarms (none are configured: a scene has no
rationalized setpoints, ISA-18.2), HMI, or anything safety-related.

## Verify on import (Studio 5000)

The owner verifies, in order, before the demo is shown (ruled 2026-11-09):

1. **The library.** Rockwell's Library of Process Objects is in the project, version **4.10.03 or
   later** (a public advisory reports critical anomalies in `P_PIDE` 4.10.02), the same version
   whose manual the names are checked against.
2. **The import.** The partial import completes with no errors and no warnings. The program
   `PlantPAx_Demo` exists; schedule it in a task (the file does not name one).
3. **Every parameter name**, against the official PROCES-RM013 for that library version, in
   Studio 5000 (no red rungs). The names in the table below were read from a third-party copy of
   the manual, because Rockwell's site refused automated download.
4. **The instances without `<Data>`** imported with the library's own defaults
   `[TBD: confirm on the first import]`.
5. **Each AOI call's operand count.** The file calls each object with its instance tag only
   (`P_AIn(DEMO_LT_101);`). An object with Required parameters needs them, and the import says so.
6. **`P_PIDE`**: the gain form (`Cfg_Depend` set: dependent, so `Cfg_IGain` 10 is repeats per
   minute) and the action (`Cfg_CtrlAction` cleared: CV rises as the level falls), both
   `[TBD: verify the sense of each bit]`; the PV and CV ranges; program ownership and automatic
   mode after the first scan; the setpoint `PSet_SP` 1.0 m.
7. **`P_ValveC`**: CV and raw ranges 0 to 100 % both sides (the scene's AO is already in %),
   position feedback from `FV_01_ZT_AI`, `Cfg_IntlkCV` 0 with `Inp_IntlkOK` off while the high
   level switch is on, and what it does with bad feedback.
8. **`P_Motor`**: run feedback from `P_01_RUN_DI` (the scene reports running at once), the
   5 s fail-to-start time, the low-level interlock, and the one program start at 20 s.
9. **`Init` runs once**, on the first scan only, and nothing else uses `S:FS`.
10. **SoftwareRevision 35.00** (or `--controller` and the revision of your project) and the
    controller or emulator type match.
11. **The live run** (after PLAN 2.2): the level holds 1.0 m across the pump's start as the
    stand-in script does, within the scene's checks (`openfactory test --plc`).

## Ignition UDTs and Perspective faceplates (PLAN 3.8)

```bash
uv run openfactory export scenes/tank-level-pid.yaml --format plantpax-udts --device Bench_L83 -o udts.json
uv run openfactory export scenes/tank-level-pid.yaml --format plantpax-faceplates -o faceplates.json
```

Both are in the `tank-level-pid` and `plantpax-tank-level` kits.

- **UDTs** (Ignition 8.3 tag JSON): one `<AOI>_UDT` per object type, whose members are **exactly
  the cited names below** for that object, read-only, reading
  `ns=1;s=[{Device}]{InstanceName}.<Member>` through Ignition's Logix driver. `Device` is a UDT
  parameter (`--device`, default `PlantPAxDemo`): the gateway's device alias, never an address.
  History on `P_AIn.Val` only. One instance per object in folder `DEMO` (`LT_101` with
  `InstanceName` `DEMO_LT_101`, ...). No alarms.
- **Faceplates** (one Perspective view per object type, `PlantPAx/<AOI>_Faceplate`, in one JSON
  document): header, the object's readouts bound through the `tagPath` parameter (`{Root}/...`,
  so one view serves every instance), an alarm line and a breadcrumb, in the ISA-101 palette.
  **Display only:** operator commands and setpoints go through each object's `OCmd_`/`OSet_`
  members, local tags whose names were not in the extracted lists; they are left out until you
  verify them in Studio 5000. Not yet loaded into a Designer.

Verify in the gateway: the UDT import, Good quality on every member once the controller runs the
L5X, and each faceplate opened with `tagPath` `[default]DEMO/<instance>`.

## The source table

Every member a rung names, and where Rockwell's public manual lists it. Names only: no
description or default is copied from the manual, whose reproduction is not permitted. The table
is generated from `src/openfactory/export/plantpax.py` (`CITED`); `tests/unit/test_plantpax.py`
checks this page carries it as the code has it, that every member a rung names is in it, and that
it lists nothing the rungs do not use.

| Object | Member | Where RM013 lists it |
|---|---|---|
| `P_AIn` | `Cfg_InpRawMax` | PROCES-RM013I-EN-P, April 2023 (library 4.10.06), workbook "IO Processing 4.1", Input Parameters |
| `P_AIn` | `Cfg_InpRawMin` | PROCES-RM013I-EN-P, April 2023 (library 4.10.06), workbook "IO Processing 4.1", Input Parameters |
| `P_AIn` | `Cfg_PVEUMax` | PROCES-RM013I-EN-P, April 2023 (library 4.10.06), workbook "IO Processing 4.1", Input Parameters |
| `P_AIn` | `Cfg_PVEUMin` | PROCES-RM013I-EN-P, April 2023 (library 4.10.06), workbook "IO Processing 4.1", Input Parameters |
| `P_AIn` | `Inp_PV` | PROCES-RM013I-EN-P, April 2023 (library 4.10.06), workbook "IO Processing 4.1", Input Parameters |
| `P_AIn` | `Sts_PVBad` | PROCES-RM013I-EN-P, April 2023 (library 4.10.06), workbook "IO Processing 4.1", Output Parameters |
| `P_AIn` | `Val` | PROCES-RM013I-EN-P, April 2023 (library 4.10.06), workbook "IO Processing 4.1", Output Parameters |
| `P_Motor` | `Cfg_FailToStartT` | PROCES-RM013I-EN-P, April 2023 (library 4.10.06), workbook "Motors 4.1", Input Parameters |
| `P_Motor` | `Cfg_HasRunFdbk` | PROCES-RM013I-EN-P, April 2023 (library 4.10.06), workbook "Motors 4.1", Input Parameters |
| `P_Motor` | `Cfg_UseRunFdbk` | PROCES-RM013I-EN-P, April 2023 (library 4.10.06), workbook "Motors 4.1", Input Parameters |
| `P_Motor` | `Inp_IntlkOK` | PROCES-RM013I-EN-P, April 2023 (library 4.10.06), workbook "Motors 4.1", Input Parameters |
| `P_Motor` | `Inp_RunFdbk` | PROCES-RM013I-EN-P, April 2023 (library 4.10.06), workbook "Motors 4.1", Input Parameters |
| `P_Motor` | `Out_Run` | PROCES-RM013I-EN-P, April 2023 (library 4.10.06), workbook "Motors 4.1", Output Parameters |
| `P_Motor` | `PCmd_Prog` | PROCES-RM013I-EN-P, April 2023 (library 4.10.06), workbook "Motors 4.1", Input Parameters |
| `P_Motor` | `PCmd_Start` | PROCES-RM013I-EN-P, April 2023 (library 4.10.06), workbook "Motors 4.1", Input Parameters |
| `P_PIDE` | `Cfg_CVEUMax` | PROCES-RM013I-EN-P, April 2023 (library 4.10.06), workbook "Regulatory_Procedural Control 4.1", Input Parameters |
| `P_PIDE` | `Cfg_CVEUMin` | PROCES-RM013I-EN-P, April 2023 (library 4.10.06), workbook "Regulatory_Procedural Control 4.1", Input Parameters |
| `P_PIDE` | `Cfg_CtrlAction` | PROCES-RM013I-EN-P, April 2023 (library 4.10.06), workbook "Regulatory_Procedural Control 4.1", Input Parameters |
| `P_PIDE` | `Cfg_DGain` | PROCES-RM013I-EN-P, April 2023 (library 4.10.06), workbook "Regulatory_Procedural Control 4.1", Input Parameters |
| `P_PIDE` | `Cfg_Depend` | PROCES-RM013I-EN-P, April 2023 (library 4.10.06), workbook "Regulatory_Procedural Control 4.1", Input Parameters |
| `P_PIDE` | `Cfg_IGain` | PROCES-RM013I-EN-P, April 2023 (library 4.10.06), workbook "Regulatory_Procedural Control 4.1", Input Parameters |
| `P_PIDE` | `Cfg_IntlkCV` | PROCES-RM013I-EN-P, April 2023 (library 4.10.06), workbook "Regulatory_Procedural Control 4.1", Input Parameters |
| `P_PIDE` | `Cfg_MaxCV` | PROCES-RM013I-EN-P, April 2023 (library 4.10.06), workbook "Regulatory_Procedural Control 4.1", Input Parameters |
| `P_PIDE` | `Cfg_MinCV` | PROCES-RM013I-EN-P, April 2023 (library 4.10.06), workbook "Regulatory_Procedural Control 4.1", Input Parameters |
| `P_PIDE` | `Cfg_PGain` | PROCES-RM013I-EN-P, April 2023 (library 4.10.06), workbook "Regulatory_Procedural Control 4.1", Input Parameters |
| `P_PIDE` | `Cfg_PVEUMax` | PROCES-RM013I-EN-P, April 2023 (library 4.10.06), workbook "Regulatory_Procedural Control 4.1", Input Parameters |
| `P_PIDE` | `Cfg_PVEUMin` | PROCES-RM013I-EN-P, April 2023 (library 4.10.06), workbook "Regulatory_Procedural Control 4.1", Input Parameters |
| `P_PIDE` | `Inp_IntlkOK` | PROCES-RM013I-EN-P, April 2023 (library 4.10.06), workbook "Regulatory_Procedural Control 4.1", Input Parameters |
| `P_PIDE` | `Inp_PV` | PROCES-RM013I-EN-P, April 2023 (library 4.10.06), workbook "Regulatory_Procedural Control 4.1", Input Parameters |
| `P_PIDE` | `Inp_PVBad` | PROCES-RM013I-EN-P, April 2023 (library 4.10.06), workbook "Regulatory_Procedural Control 4.1", Input Parameters |
| `P_PIDE` | `Out_CV` | PROCES-RM013I-EN-P, April 2023 (library 4.10.06), workbook "Regulatory_Procedural Control 4.1", Output Parameters |
| `P_PIDE` | `PCmd_Auto` | PROCES-RM013I-EN-P, April 2023 (library 4.10.06), workbook "Regulatory_Procedural Control 4.1", Input Parameters |
| `P_PIDE` | `PCmd_Prog` | PROCES-RM013I-EN-P, April 2023 (library 4.10.06), workbook "Regulatory_Procedural Control 4.1", Input Parameters |
| `P_PIDE` | `PSet_SP` | PROCES-RM013I-EN-P, April 2023 (library 4.10.06), workbook "Regulatory_Procedural Control 4.1", Input Parameters |
| `P_ValveC` | `Cfg_CVEUMax` | PROCES-RM013I-EN-P, April 2023 (library 4.10.06), workbook "Valves 4.1", Input Parameters |
| `P_ValveC` | `Cfg_CVEUMin` | PROCES-RM013I-EN-P, April 2023 (library 4.10.06), workbook "Valves 4.1", Input Parameters |
| `P_ValveC` | `Cfg_CVRawMax` | PROCES-RM013I-EN-P, April 2023 (library 4.10.06), workbook "Valves 4.1", Input Parameters |
| `P_ValveC` | `Cfg_CVRawMin` | PROCES-RM013I-EN-P, April 2023 (library 4.10.06), workbook "Valves 4.1", Input Parameters |
| `P_ValveC` | `Cfg_FdbkRawMax` | PROCES-RM013I-EN-P, April 2023 (library 4.10.06), workbook "Valves 4.1", Input Parameters |
| `P_ValveC` | `Cfg_FdbkRawMin` | PROCES-RM013I-EN-P, April 2023 (library 4.10.06), workbook "Valves 4.1", Input Parameters |
| `P_ValveC` | `Cfg_HasPosFdbk` | PROCES-RM013I-EN-P, April 2023 (library 4.10.06), workbook "Valves 4.1", Input Parameters |
| `P_ValveC` | `Cfg_IntlkCV` | PROCES-RM013I-EN-P, April 2023 (library 4.10.06), workbook "Valves 4.1", Input Parameters |
| `P_ValveC` | `Inp_IntlkOK` | PROCES-RM013I-EN-P, April 2023 (library 4.10.06), workbook "Valves 4.1", Input Parameters |
| `P_ValveC` | `Inp_PosFdbk` | PROCES-RM013I-EN-P, April 2023 (library 4.10.06), workbook "Valves 4.1", Input Parameters |
| `P_ValveC` | `Out_CV` | PROCES-RM013I-EN-P, April 2023 (library 4.10.06), workbook "Valves 4.1", Output Parameters |
| `P_ValveC` | `PCmd_Prog` | PROCES-RM013I-EN-P, April 2023 (library 4.10.06), workbook "Valves 4.1", Input Parameters |
| `P_ValveC` | `PSet_CV` | PROCES-RM013I-EN-P, April 2023 (library 4.10.06), workbook "Valves 4.1", Input Parameters |

