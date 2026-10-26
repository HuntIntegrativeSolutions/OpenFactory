# Competitive Landscape and Idea Sources

Surveyed 2026-10-25 and 2026-10-26. Purpose: know what exists, place OpenFactory honestly, and harvest
feature ideas. **Clean-room reminder:** reading public marketing pages, forum wishlists, and open-source
repositories for *ideas* is fine. Copying names, formats, assets, or code from a non-permissive source is
not. Open Industry Project is MIT-licensed, so its code and format may be studied and, with attribution,
reused; everything else in this file is ideas only.

## The pyramid

| Tier | Products | Price shape | What they answer |
|------|----------|-------------|------------------|
| Enterprise digital twin / virtual commissioning | Rockwell Emulate3D, Siemens SIMIT + NX MCD + Process Simulate, Dassault DELMIA Virtual Factory, NVIDIA Omniverse (+ Isaac Sim) | Six figures, CAD-native, dedicated sim teams | "Will this whole line work before we build it" |
| Planning / discrete-event | FlexSim, Arena, Siemens Plant Simulation | Per-seat planning-tool prices | "How many per hour under statistical variation" |
| Training cell and demo | Factory I/O, Nirtec Machines Simulator Suite, Open Industry Project, plcsimulationsoftware.com, AUTRYX, PLC Sim Lab, MakersDeck, **OpenFactory** | Free to low four figures per seat | "Does my PLC program and HMI work on this cell" |
| PLC emulators (no plant) | Logix Emulate / FactoryTalk Logix Echo, S7-PLCSIM (Advanced), TwinCAT 3, CODESYS Control, OpenPLC | Mostly bundled or free | Runs the controller, needs a plant from the tier above |

OpenFactory lives in the third tier by design (PLAN §1 Non-Goals). The USD exporter (PLAN 4.10) is the
only planned bridge upward.

## Closest neighbours, in order of relevance

### 1. Open Industry Project (OIP) — MIT, browser, TypeScript + three.js + Jolt
Source: https://github.com/Open-Industry-Project/Open-Industry-Project and https://www.openindustryproject.org/
(maintained by Automation Standard LLC / autStand, an integrator). Early release, ~70 stars, ~95 commits.

This is the nearest thing to OpenFactory that exists, and it is ahead on several fronts:
- Browser editor with drag, snap-to-end, inspector, first-person walk mode, VR, live co-editing links.
- **Jolt physics** compiled to multi-threaded WASM, using Jolt's conveyor-belt contact-listener pattern,
  i.e. true surface velocity on contact, which Rapier JS lacks (see `libraries.md`, "Rapier conveyor
  approach"). Sim steps at 60–1000 Hz in a worker; PLC scan interleaved.
- **Built-in PLC**: subset of IEC 61131-3 ST and LD, import/export `.st` and PLCopen XML.
- **Part SDK**: a part is `manifest.json` + `behavior.js` (`definePart({params, step(ctx)})`), deterministic
  (`ctx.rng()` seeded per part, no `Date`), rig geometry from parameters, shareable as a single file.
- **Scene format**: flat JSON, "placement-as-truth" (connectivity computed from geometry), part packages
  embedded so a scene is self-contained, versioned with upgrade-on-load.
- **Project = scene + ST program + part library + JSON control-logic tests**; `npm run test-project`
  grades the logic. A saved project also writes a generated **`AGENTS.md`** describing tags, parts, and
  test format for AI assistants.
- Lint-style tooling: `check-wiring`, `check-st`, `capabilities`, `catalog`.
- Desktop (Electron) app adds external PLCs: Modbus TCP, OPC UA, EtherNet/IP, S7 PUT/GET, ADS, plus an
  **OPC UA server** for SCADA.

Where OpenFactory differs, and why we continue rather than fork:
- OIP's authority is the browser/worker; ours is a Python process. That is the whole reason our protocol
  layer, PlantPAx/Ignition generation pipeline, headless CI, and process simulation are cheap for us and
  hard for them. OIP needs Electron for any real PLC; we need a browser only for pictures.
- OIP scenes are editor output in JSON; ours are hand- or AI-authored YAML with a schema designed for
  reading. Both are text, so the gap is smaller than with Factory I/O.
- OIP has no process equipment, no engineering-deliverable export, no run statistics or exercise checks
  (it has logic tests, which is adjacent), and no Rockwell-specific tooling.
- OIP has an editor, a catalog, VR, collaboration, and a soft PLC that we do not, and will not for a while.

**Decisions taken from this comparison** (recorded as PLAN tasks, see "Harvested ideas"):
Jolt evaluation in 2.9; self-contained scene bundles; generated per-scene AI context file; wiring lint;
OIP scene import as a Milestone 4 stretch. Collaboration with the OIP maintainers is a reasonable
option: our export pipeline and process components could serve their users, and their part catalog
could serve ours. Owner's call; not a plan item.

### 2. Factory I/O (Real Games) — commercial, Windows, Unity-based
Public wishlist thread (https://community.factoryio.com/t/the-big-factory-i-o-wishlist/135, pages 4–5)
is the best list of what users of this class actually want and do not get:
- Editor: pits, ramps, mezzanines, ceiling-hung conveyors, conveyor start/end at different heights,
  custom 3D model and building import, larger floor.
- Parts: variable-width conveyors, bidirectional conveyors with a visible direction arrow, accumulation
  (ZPA / MDR) conveyors, lifts between levels, guards and bumpers, spawn/destroy blocks.
- Sensors: NO/NC selection, invisible-to-physics sensors, time-of-flight distance windows (muting).
- I/O drivers: invert a signal, OR several sensors into one PLC input, reconnect a driver without
  reloading, NO/NC at the driver level.
- Teaching: exam mode where the plant is visible but sensors are replaced by switches.

### 3. Nirtec Machines Simulator Suite — commercial, Windows, bundled EasyPLC soft PLC
3D editor with OBJ/STL import and user-defined components. Validated PLAN 2.15 (custom mesh) and 2.16
(composites). Nothing further to take.

### 4. plcsimulationsoftware.com — commercial SaaS, browser
151-component catalog in lazy-loaded packs; snap ports typed by kind (load, pipe, mount, safety);
0.25 m placement grid; scene validation; guided scenarios with automated grading reading controller
and model state; failure modes **stuck-on, stuck-off, jammed, slow, misaligned**, one at a time; typed
tags (BOOL, DINT, REAL) classed as command vs feedback; built-in PLC + HMI; no external PLC at all.
Admits it is "a training simulator, not a production digital twin."

### 5. Others seen
- AUTRYX, PLC Sim Lab, MakersDeck, rungs.dev: browser ladder trainers with simple plants, some with
  fault injection and vendor addressing modes (Siemens, Mitsubishi, AB, TOYOPUC). Teaching-oriented,
  no external PLC.
- VirtualPlant (Godot, OPC UA via WebSocket bridge), Factory Core Lite (Godot, MIT, game-oriented),
  factory_sim (C++/ImGui, S7 planned), PLC Node Simulator (multi-protocol virtual *device*, no 3D):
  small open projects, useful mainly as confirmation that the Python-authority + browser-viewer split
  is uncommon.
- CIROS, ProtoTwin, CADe SIMU, FluidSIM: mechatronics/electrical trainers, adjacent not competing.

## Harvested ideas → PLAN

| Idea | Source | PLAN task |
|------|--------|-----------|
| Binding-level signal transforms: invert / NO-NC, OR-combine, on/off delay, distance window | Factory I/O wishlist | 2.17 |
| Failure taxonomy: stuck-on, stuck-off, jammed, slow, misaligned, noisy, drift | plcsimulationsoftware, FIO wishlist | 2.6 (extended) |
| Accumulation (ZPA) conveyor as a composite; lift; bidirectional conveyor | FIO wishlist | 2.5 / 2.16 examples |
| Exam mode: sensors replaced by operator switches, plant still visible | FIO wishlist | 2.11 (extended) |
| Self-contained scene bundle (scene + composites + meshes + lockfile + script in one file) | OIP | 2.18 |
| Generated per-scene AI context file (tags, components, checks, how to run) | OIP `AGENTS.md` | 2.19 |
| Wiring lint: unbound required ports, tags written by nobody, tags read by nobody | OIP `check-wiring` | 2.20 (`openfactory lint`) |
| Evaluate Jolt (MIT, multi-threaded WASM, true conveyor contact velocity) vs Rapier | OIP | 2.9 (extended) |
| Typed snap ports between components (load, pipe, mount) for the editor | plcsimulationsoftware, OIP | 4.1 (note) |
| OIP scene import | OIP (MIT) | 4.11 stretch |
| Driver reconnect without restart | FIO wishlist | 2.2 / 2.4 Done-when |

## Sources
- Open Industry Project: https://github.com/Open-Industry-Project/Open-Industry-Project ;
  https://www.openindustryproject.org/ ; docs/technical-overview.md and packages/part-sdk in that repo
- Factory I/O wishlist: https://community.factoryio.com/t/the-big-factory-i-o-wishlist/135?page=4 and ?page=5
- Nirtec: https://www.nirtec.com/products/machines-simulator-suite/
- plcsimulationsoftware.com: https://plcsimulationsoftware.com/3d-plc-simulator
- FlexSim: https://www.flexsim.com/manufacturing-simulation/
- DELMIA: https://www.3ds.com/products/delmia/industrial-engineering/virtual-factory
- NVIDIA DSX Sim (data-center twin, not a factory sim): https://www.nvidia.com/en-us/data-center/products/dsx/sim/
- Survey articles (vendor-published, treat as leads): https://plcsimulationsoftware.com/blog/best-plc-simulator-2026 ;
  https://plcprogramming.io/comparisons/best/simulators ;
  https://industrialmonitordirect.com/blogs/knowledgebase/best-industrial-automation-simulation-software-for-plc-training
- Small open projects: https://dev.to/calumk/virtualplant-a-3d-factory-simulator-54i4 ;
  https://github.com/pousopk/factory_sim ; https://github.com/kingdol666/plc-node-simulator
