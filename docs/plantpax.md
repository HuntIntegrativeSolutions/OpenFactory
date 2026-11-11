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
| Support tags | `DEMO_LIC_101_SP` (REAL, the loop's tunable setpoint, 1.0 m), `DEMO_PROG_REQ` and `DEMO_LIC_101_AUTO_ONS` (BOOL: the program-ownership request and the Auto one-shot); in `plantpax-tank-level` also `DEMO_LOAD_TMR` (TIMER), `DEMO_LOAD_DELAY_MS` (DINT), `DEMO_LOAD_ONS` (BOOL): the demo's load step, the transfer pump starting 20 s after power-up |
| Program `PlantPAx_Demo` | `MainRoutine`: `Init` on the first scan only (`S:FS`), then `Control` every scan |
| Routine `Init` | Configuration only, one commented rung per object: ranges, the PI tuning carried over from `tank-level-pid` (gain 8 %/%, 10 repeats a minute, dependent form, reverse acting), the setpoint tag 1.0 m, the interlock CVs; then it raises `DEMO_PROG_REQ`. **No program command and no `PSet_` value is written in the first scan** (see "Program ownership" below) |
| Routine `Control` | The scene's tags wired to the objects in signal order (level → `P_AIn` → `P_PIDE` → `P_ValveC` → valve; pump feedback and the low-level interlock → `P_Motor` → run), each object called once; `PSet_SP` written from `DEMO_LIC_101_SP` every scan; Auto requested once `P_PIDE` reports Program; and, last, the program-ownership request |

**It does not contain:** the Add-On Instruction definitions or the library's data types (Rockwell
distributes them; import the library into the project first), I/O modules (the plant is
OpenFactory, reached through tags), rationalized alarms (none are configured: a scene has no
rationalized setpoints, ISA-18.2), HMI, or anything safety-related.

## Program ownership and the first scan

Review of 2026-11-10: `Init` used to latch `PCmd_Prog` (and `PCmd_Auto` on the loop) in the first
scan. What RM013 (PROCES-RM013I-EN-P, April 2023) says about that:

- **Commands received at power-up are discarded.** The execution table of each object says so
  for its powerup (prescan, first scan) condition: `P_PIDE` p. 82, `P_Motor` p. 130, `P_ValveC`
  p. 287. `Init` runs on the first scan *before* the objects are called, so its commands were
  exposed to that.
- **Whether a command issued in the scan that acquires Program is honoured is not documented.**
  RM013 does not say whether `PCmd_Auto` (or a `PSet_` value) given in the same scan as
  `PCmd_Prog` is accepted or dropped; the file is written so that the question never arises.
- **Program settings track the operator's while in Operator.** `Cfg_SetTrack` (library default 1,
  workbooks "Regulatory_Procedural Control 4.1" and "Valves 4.1") makes the `PSet_` values follow
  the operator settings in the Operator command source, so a `PSet_SP` written once before
  Program is acquired is overwritten.
- **The library has a power-up owner setting:** `Cfg_ProgPwrUp` of the embedded command-source
  instruction `P_CmdSrc` (workbook "Cross Functional 4.1", Input Parameters; text p. 381 and its
  prescan row p. 388): set to 1, the object powers up with Program selected. On `P_PIDE`,
  `P_ValveC` and `P_Motor` it is **not** an input parameter: it is a member of the local tag
  `CmdSrc`, and RM013's own examples (`P_DOut` p. 61, `P_nPos` p. 289) only say that `CmdSrc.Cfg_ProgPwrUp` "is set
  to 1", not how. It is applied at prescan, so a rung in `Init` could not affect the first power-up
  anyway, and the file has no `<Data>` that could carry it. **The file therefore does not use it**
  `[TBD: whether a partial import can carry CmdSrc.Cfg_ProgPwrUp on the three instances; set it in
  the tag editor; import check 9]`.

What the file does instead, so ownership never depends on that setting:

1. `Init` writes configuration only and raises `DEMO_PROG_REQ`.
2. The **last rung of `Control`**, after every object call of the first scan, issues one
   `PCmd_Prog` to each owned object and clears `DEMO_PROG_REQ`; each object reads it on the
   second scan.
3. `P_PIDE`'s `PCmd_Auto` is issued on the rising edge of its own `Sts_Prog`, after its call, so
   the loop reads it on the scan after it reports Program.
4. `PSet_SP` is written from `DEMO_LIC_101_SP` every scan (`PSet_CV` already was).

`tests/unit/test_plantpax.py` (`test_program_commands_are_never_issued_in_the_first_scan`)
holds all four.

## The pump after a low-level trip

The programs for `plantpax-tank-level` do not behave alike once the low level switch (LSL, 0.3 m)
has tripped the transfer pump:

- **OpenPLC and the stand-in** (`examples/openplc/plantpax-tank-level.st`,
  `examples/pyplc/plantpax-tank-level.py`) compute the run command every scan as "load delay done
  AND NOT LSL", so the pump **restarts by itself when LSL clears**.
- **The PlantPAx L5X** issues one `PCmd_Start` (a one-shot when the load timer is done, 20 s after
  power-up). When `Inp_IntlkOK` drops, `P_Motor` stops the running motor and raises its interlock
  trip (RM013 p. 129; `Sts_IntlkTrip`); nothing in the file issues another start, so the pump
  **stays off until a new start** (from the faceplate or a program command). RM013 does not say
  in so many words that `P_Motor` drops its run request on a trip, so this is
  `[TBD: confirm on the first live run]`.

**What live-run check 11 expects:** the scene's own checks (60 s, and 300 s per PLAN §9 rule 10):
the level 0.98 to 1.02 m after 45 s, never overflow, never dry, at most two pump starts. In that run
LSL never trips: under the stand-in the level stays between 0.98 and 1.02 m (0.987 to 1.009 m after
45 s, `openfactory test --script ... --duration 300`, 2026-11-10), far above 0.3 m. So check 11
expects **one start, at 20 s, and no LSL trip**, which both behaviours give; it does not test the
restart. A demo of the trip (draining the tank by hand) shows the difference, and the instructor
should say so.

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
   mode a scan after power-up; the setpoint `PSet_SP` 1.0 m from `DEMO_LIC_101_SP`.
7. **`P_ValveC`**: CV and raw ranges 0 to 100 % both sides (the scene's AO is already in %),
   position feedback from `FV_01_ZT_AI`, `Cfg_IntlkCV` 0 with `Inp_IntlkOK` off while the high
   level switch is on, and what it does with bad feedback.
8. **`P_Motor`**: run feedback from `P_01_RUN_DI` (the scene reports running at once), the
   5 s fail-to-start time, the low-level interlock, and the one program start at 20 s.
9. **`Init` runs once**, on the first scan only, issues no program command, and nothing else
   uses `S:FS`. **Program ownership:** a scan after power-up `P_PIDE`, `P_ValveC` and `P_Motor`
   show Program (`Sts_Prog`) and `P_PIDE` shows Auto ("Program ownership and the first scan"
   above). Optionally set `CmdSrc.Cfg_ProgPwrUp` to 1 on those three instances in the tag editor
   so they also *power up* in Program (RM013 p. 381); the file works without it `[TBD: whether
   the partial import can carry it, and whether ladder may write a member of an object's local
   tag]`.
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
| `P_PIDE` | `Sts_Prog` | PROCES-RM013I-EN-P, April 2023 (library 4.10.06), workbook "Regulatory_Procedural Control 4.1", Output Parameters |
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
