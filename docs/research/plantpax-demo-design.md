# PlantPAx demo (PLAN 3.4): design proposal

**Status: proposal for ruling, 2026-11-08. No code.** Asked for (2026-11-08): which `P_` objects
map to which components, how parameters are sourced from PROCES-RM002 publicly, what the L5X will
and will not contain, and what the owner must verify on import in Studio 5000. Training and demo
use only; nothing here is a safety-rated design, and every generated file needs HIS Controls PE
review before a client sees it.

**Recommendation in one paragraph.** Build the demo on `tank-level-pid` with one added outlet
pump, so the four objects PLAN 3.4 names each have a real component: `P_AIn` on the level
transmitter, `P_PIDE` as the level controller, `P_ValveC` on the control valve and `P_Motor` on
the pump. Generate the L5X with a tool in this repository (not the `/plantpax-*` skills, which
call an MCP server this repository may not use): controller tags for the scene's I/O, one
instance tag per object with no `<Data>` block, an `Init` routine that sets each instance's
configuration on the first scan, and a `PlantPAx_Demo` routine that wires the scene tags to the
objects and calls them. No AOI definitions: the owner imports the Rockwell library (4.10.03 or
later) into the project first. Parameter names come from Rockwell's public manual PROCES-RM013
(not RM002, which has no parameter tables), cited by publication and revision, each checked by the
owner against the official copy. A live demo needs PLAN 2.1 and 2.2 (OpenFactory reaching a Logix
controller's tags), which are not done; until then the L5X is an artifact to import and review.

## 1. Sources, and a correction to PLAN 3.4

PLAN 3.4 says parameter names come "only from Rockwell's public PlantPAx library reference
manuals: publication PROCES-RM002 ... and PROCES-RM200". A search of public sources (2026-11-08)
found:

| Publication | What it is | Parameters? |
|---|---|---|
| PROCES-RM002I-EN-P (May 2019), *Rockwell Automation Library of Process Objects: Configuration and Usage, Version 4.0 or Later* | Installation, HMI templates, security | **No.** Its preface points to RM013 and RM014 for the instructions. |
| PROCES-RM013I-EN-P (April 2023), *Logic Instructions*, library 4.10.06 | The `P_` Add-On Instructions | **Yes**, as Excel attachments embedded in the PDF ("Access the Attachments", p. 12), one workbook per family with Input, Output and Local Tags sheets. The workbooks are labelled **4.1**, not 4.10.06. |
| PROCES-RM014 (*HMI Instructions*) | Faceplates for FactoryTalk View | not needed here |
| PROCES-RM200D-EN-P (December 2022), *Process Library 5.10 Add-On Instructions* | 5.x AOIs | seen only on a document-sharing site; unverified |
| PROCES-RM215C-EN-P (September 2025), *PlantPAx Process Control Instructions* | 5.x firmware instructions (PAI, PAO, PPID, PMTR, PVLV, PDI, PDO) | yes; these run only on 5380P, 5580P and 5590P controllers |

Two cautions on how these were read:

- **Rockwell's own sites refused every automated fetch** (HTTP 403 on
  literature.rockwellautomation.com and the help pages). The PDFs read were copies on a third-party
  mirror whose covers match Rockwell's numbering. The demo cites only publication number and
  revision, never the mirror, and **the owner checks every name against the official copy**
  downloaded by hand.
- **Both manuals forbid reproduction** without written permission. So the repository carries the
  parameter *names* the L5X uses, each with its citation, and nothing else: no tables, no
  descriptions copied from them, no extracted workbooks. (The research's extracted lists stayed in
  the session's scratch folder and are not committed.)

**Proposed amendment to PLAN 3.4:** cite PROCES-RM013 (logic, 4.x) instead of RM002, and
PROCES-RM215 instead of RM200 for 5.x; drop "via `/plantpax-*` skills" (they call
`ai-memory-vendor-docs`, which CLAUDE.md bars in this repository).

## 2. Which object goes on which component

The demo scene, `plantpax-tank-level` (new, derived from `tank-level-pid`): supply, control valve
`FV_01`, tank `TK_01` with level transmitter `LT_01` and high switch, and an outlet pump `P_01`
(run DO, running DI) in place of the consumer drain, pumping to an open drain. Instance names
follow HIS Controls' `AREA_EQUIP_NNN` with area `DEMO` [TBD: owner to confirm the area prefix].

| ISA tag | Instance | Object | Component and scene tags | Role |
|---|---|---|---|---|
| LT-101 | `DEMO_LT_101` | `P_AIn` | `LT_01`: `TK_01_LT_AI` (REAL, m) | Level PV, range and alarms; `Val` feeds the controller |
| LIC-101 | `DEMO_LIC_101` | `P_PIDE` | none directly | Level controller: PV `DEMO_LT_101.Val`, CV to the valve |
| LV-101 | `DEMO_LV_101` | `P_ValveC` | `FV_01`: `FV_01_POS_AO` out, `FV_01_ZT_AI` position feedback | Throttling valve |
| P-101 | `DEMO_P_101` | `P_Motor` | `P_01`: `P_01_RUN_DO` out, `P_01_RUN_DI` running feedback | Outlet pump with a low-level interlock |

Signal flow, one rung or ST line each:

- `DEMO_LT_101.Inp_PV := TK_01_LT_AI`. OpenFactory writes the level in engineering units, so the
  raw and EU ranges are the same (0 to 2 m): `Cfg_InpRawMin/Max` = `Cfg_PVEUMin/Max` = 0 and 2.
- `DEMO_LIC_101.Inp_PV := DEMO_LT_101.Val`; `Inp_PVBad := DEMO_LT_101.Sts_PVBad`; setpoint through
  `PSet_SP` (program) with the operator's `OSet_SP` on the faceplate; `Cfg_PVEUMin/Max` 0 to 2,
  `Cfg_CVEUMin/Max` 0 to 100.
- `DEMO_LV_101.PSet_CV := DEMO_LIC_101.Out_CV`; `Inp_PosFdbk := FV_01_ZT_AI`;
  `FV_01_POS_AO := DEMO_LV_101.Out_CV`; `Cfg_CVEUMin/Max` and `Cfg_CVRawMin/Max` 0 to 100 (the
  scene's AO is already in %).
- `DEMO_P_101.Inp_RunFdbk := P_01_RUN_DI`; `P_01_RUN_DO := DEMO_P_101.Out_Run`;
  `Inp_IntlkOK := NOT TK_01_LSL_DI` (no running the tank dry); started and stopped by the
  operator (`OCmd_Start`/`OCmd_Stop`) or by `PCmd_Start`/`PCmd_Stop`.
- The high-level switch closes the valve. Whether through `P_ValveC`'s interlock input or by
  forcing the controller to manual depends on the parameter list [TBD: confirm `P_ValveC`'s
  interlock inputs in RM013's Valves workbook before choosing].

Tuning carries over from the scene's PI with a unit change, because `P_PIDE` wraps the Logix
`PIDE` instruction, whose gains are in % of CV per % of PV and integral per minute: OpenPLC's KP
−400 %/m on a 2 m span is a proportional gain of 8 (%/%), reverse acting, and its 6 s reset is
0.1 min. Which `PIDE` form (dependent or independent) `P_PIDE` uses, and so whether `Cfg_IGain` is
1/Ti or Kc/Ti, is [TBD: verify in RM013 and in Studio 5000 before the first run].

Library version: **4.10.03 or later.** A public advisory reports critical anomalies in
`P_PIDE` 4.10.02, fixed in 4.10.03. The manual's workbooks say 4.1 while the manual says
4.10.06; each name must hold for the version the owner imports.

## 3. What the L5X will and will not contain

Generated by a tool in this repository (proposed `openfactory export SCENE --format plantpax`,
beside `--format l5x`), never written into this repository, partial import
(`ContainsContext="true"`), SoftwareRevision 35.00 by default, validated as well-formed XML in a
test:

**Will contain**

- The scene's I/O as controller tags (BOOL and REAL), as `--format l5x` already writes them, with
  descriptions and ranges.
- Four instance tags, `DataType` the object's name, each with a description in ISA-5.1 form
  ("Level Control Tank Level Transmitter LT-101"), **with no `<Data>` element** (see §4).
- Routine `Init` (ladder), called once on the first scan: one rung per configuration value
  (`MOV` into `Cfg_*`, `PSet_SP`), each commented with its source (scene file, or "tuning carried
  from tank-level-pid"). No setpoint is a literal in the control rungs.
- Routine `PlantPAx_Demo` (ladder): the signal-flow rungs of §2, each commented with the ISA tag
  and what it does, and one call per object in execution order (`P_AIn`, `P_PIDE`, `P_ValveC`,
  `P_Motor`), each with the operand count its definition requires [TBD: the Required parameters
  per object, from RM013; a wrong count fails import].
- A source table in the generator: every parameter name the file uses, with "PROCES-RM013I-EN-P,
  April 2023, <workbook> sheet <name>" beside it, so a reviewer can check each one.

**Will not contain**

- The Add-On Instruction definitions, the library's data types, or anything else from the
  Rockwell library: they are Rockwell's to distribute (Product Compatibility and Download Center,
  under its own license), and an Apache-2.0 repository should not carry them. The owner imports
  the library first.
- I/O modules: the plant is OpenFactory, reached through tags (PLAN 2.1 and 2.2), not through a
  chassis.
- Alarm rationalization (ISA-18.2): `Init` configures no `P_AIn` alarm limits or `Cfg_Has*Alm`
  flags, and a comment says the alarms are not rationalized (a scene has no rationalized
  setpoints). What the library's defaults enable is item 2 of §5.
- HMI: the Perspective faceplates PLAN 3.4 names are not in the L5X (see decision 5).
- Anything safety-related. The low-level and high-level interlocks are process interlocks for a
  demo, not safety functions.

## 4. Why the instances carry no `<Data>`

HIS Controls' rule for instance data is to generate the Decorated `<Data>` block with
`aoi_instance_data.py`, which reads Rockwell's own library exports. That tool and its source
files are outside this repository and outside the clean-room boundary (they are vendor material),
so they cannot be used here. Writing the block by hand would mean copying member order, types and
defaults from the library, which the manuals do not license. Leaving `<Data>` out and setting
configuration in `Init` keeps every value in the file traceable and lets Studio 5000 apply the
library's own defaults; whether it does so on a partial import of an instance tag without
`<Data>` is the first thing to verify (§5).

## 5. What the owner must verify on import (Studio 5000)

In order; the demo is not shown to anyone until each is checked:

1. The Rockwell Process Library is in the project, version 4.10.03 or later, the same version
   whose RM013 the names were checked against.
2. The partial import completes with no errors and no warnings; the four instance tags exist
   with the library's default values (no `<Data>` was sent) [TBD: confirm on the first import].
3. Every parameter the rungs name resolves (no red rungs): each against the official RM013,
   for that library version.
4. Each AOI call has the operand count its definition requires.
5. `P_PIDE`: the gain form and units (§2), the control action (reverse: CV up when PV falls),
   the CV and PV ranges, the mode the demo leaves it in, and who owns the setpoint
   (program or operator).
6. `P_ValveC`: the CV range and raw range, the position feedback, the interlock path, and what it
   does with bad feedback.
7. `P_Motor`: the run feedback, the fail-to-start timer against the scene's pump (it reports
   running at once), the interlock on the low-level switch, and the operator commands.
8. `Init` runs once, on the first scan only, and nothing else uses `S:FS`.
9. SoftwareRevision matches the controller (35.00 by default) and the controller or emulator
   type matches.
10. A run against the scene (once PLAN 2.2 exists): the level holds at 1.0 m across the load step
    as `tank-level-pid` does with OpenPLC, within the same checks.

## Decisions for you (recommendation first)

1. **Sources.** Amend PLAN 3.4 to cite PROCES-RM013 (4.x logic) and RM215 (5.x), not RM002 and
   RM200; names only, each cited, checked by you against the official copy.
   *Alternative:* keep RM002 in the text (it cannot supply the names).
2. **Library generation.** 4.x `P_` AOIs, 4.10.03 or later, not the 5.x firmware instructions:
   the AOIs run on any 5570 or 5580 and in Logix Emulate; PAI, PPID and PMTR need a 5380P,
   5580P or 5590P, and 5.x has no control-valve object (a throttling valve is a `PAO`).
   *Alternative:* both, 5.x later as its own task.
3. **Generator.** A tool in this repository (`export --format plantpax`), not the `/plantpax-*`
   skills, which call `ai-memory-vendor-docs`; drop the skills from PLAN 3.4's text.
   *Alternative:* none that keeps the clean-room rule.
4. **Instance data.** No `<Data>`; configuration in a first-scan `Init` routine.
   *Alternative:* hand-written Decorated `<Data>` (copies the library's member layout; not
   recommended).
5. **Faceplates.** Move the Perspective faceplates to 3.8 (the process demo kit), built from
   Ignition UDTs whose members are the same cited names, after the L5X has imported cleanly.
   *Alternative:* keep them in 3.4.
6. **Order.** Do PLAN 2.1 and 2.2 (Logix connectivity) before 3.4's live run; generate and review
   the L5X now, run it when a controller can reach the scene.
   *Alternative:* a Modbus TCP client in the controller (a Rockwell sample AOI): more moving
   parts, and another vendor file to keep out of the repository.
7. **Scene.** A new `plantpax-tank-level` (the PID plant plus an outlet pump), so `P_Motor` has a
   motor. *Alternative:* `two-tank-cascade` with two `P_PIDE` in cascade and no motor.

## Sources

- Rockwell Automation, PROCES-RM002I-EN-P (May 2019), PROCES-RM013I-EN-P (April 2023),
  PROCES-RM215C-EN-P (September 2025); PROCES-RM200D-EN-P (December 2022, unverified). Cited by
  number and revision; read from third-party copies because Rockwell's site refused automated
  fetches.
- Rockwell Automation, 1756-RM014, Logix 5000 Controllers Import/Export Reference Manual (the L5X
  format, as for PLAN 2.3).
- Border States, "Rockwell Automation Library of Process Objects P_PIDE version 4.10.02 critical
  anomalies" (public advisory notice).
- ISA-5.1 (instrument tags), ISA-18.2 (alarm rationalization).
