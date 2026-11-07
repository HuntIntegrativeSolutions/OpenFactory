# Changelog

OpenFactory is a training and demo tool, not a safety-rated simulation.

Generated from the commit log by `tools/changelog.py` (PLAN 4.8), grouped by the PLAN
milestone each commit worked on; `docs/PLAN.md` holds the reasoning behind each task. The
project follows semantic versioning from 0.2.0, and the scene format version is separate
from the package version (`docs/versioning.md`).

## 0.2.0 (not yet tagged)

140 commits, `35beca5` (2026-10-25) to `b39bef0` (2026-11-06).

### Milestone 0 — Project foundation

#### Documentation

- ADR-0003 per-scene analog encoding for Modbus (`67134b7`, 2026-10-25)
- THIRD_PARTY.md with every dependency and its license (`d943688`, 2026-10-25)

#### Maintenance (build, CI, chores)

- Chore: project foundation, plan, and research notes (`35beca5`, 2026-10-25)
- Chore: tick Milestone 0 git init task (`a039e22`, 2026-10-25)
- Build: pyproject with uv, ruff, pytest and pinned dependencies (`62409d2`, 2026-10-25)
- Build: web viewer scaffold with three and Rapier (`30ff0d0`, 2026-10-25)

### Milestone 1 — Walking skeleton: one scene, Modbus, browser

#### Features

- Scene schema with pydantic models and JSON Schema export (1.1) (`836dfc0`, 2026-10-25)
- Thread-safe I/O table with write restriction and callbacks (1.2) (`28666c6`, 2026-10-25)
- Component base class, type registry, and scene loader (1.3) (`2cdf126`, 2026-10-25)
- Milestone 1 component library with unit tests (1.4) (`13bda28`, 2026-10-25)
- Headless kinematic part model and scene connections (1.5) (`0bf7ff6`, 2026-10-25)
- Fixed-step scheduler with realtime and fast modes (1.6) (`505b1a5`, 2026-10-25)
- Modbus TCP server bound live to the I/O table (1.7) (`c3f51c9`, 2026-10-25)
- I/O list CSV export with Modbus addresses (1.8) (`bdbafeb`, 2026-10-25)
- Modbus address lockfile and pinned addresses (ADR-0004) (`36a7989`, 2026-10-25)
- Restrict scene plc.bind overrides to loopback (`744f855`, 2026-10-25)
- Friction-limited part acceleration in the headless model (`24de9a0`, 2026-10-25)
- Divert lane and tall-box photo eye for sorting-by-height (`ee75c91`, 2026-10-25)
- Pusher moves parts in the headless kinematic model (`3e8f942`, 2026-10-25)
- Warn when conveyors touch end to end without a connection (`a396f34`, 2026-10-25)
- FastAPI server with scene API and state/event WebSockets (1.9) (`8864203`, 2026-10-25)
- Openfactory CLI with run, validate, and export (1.10) (`9410ff3`, 2026-10-25)
- Screenshot runner for visual verification (1.13) (`49b58ff`, 2026-10-25)
- Viewer meshes per component type with ISA-101 status (1.11) (`ab8910b`, 2026-10-25)
- Rapier viewer physics with kinematic parity within 2 ticks (1.12) (`7880e79`, 2026-10-25)
- One Modbus address lockfile per analog encoding (ADR-0004 amendment) (`52b8b43`, 2026-10-25)
- Initial values for DO/AO bindings; sorter belts start at 100 % speed (`ff4fc0b`, 2026-10-25)
- Live parity check mode in tools/parity_trace.py (informational) (`cf58eba`, 2026-10-25)
- OpenPLC end to end for sorting-by-height (1.14) (`bfb68a3`, 2026-10-25)
- Digital Modbus addresses shared across encodings (ADR-0004 amendment) (`7a56a11`, 2026-10-25)
- Openfactory run prints the addresses a non-loopback bind is reachable at (`8e5dd38`, 2026-10-25)

#### Fixes

- Start the simulation before the viewer server in openfactory run (`79c7e65`, 2026-10-25)
- openplc_configure survives an empty first compile-log poll (`6d4ec7e`, 2026-10-25)

#### Documentation

- License allowlist, as-built behavior, scene options, M3 range risk (`a614c48`, 2026-10-25)
- Headless model is authoritative for PLC tags through Milestone 2 (`a132aa8`, 2026-10-25)
- Competitive landscape survey and harvested ideas (`f1189ff`, 2026-10-25)

#### Tests

- Parity tolerance is max(2 ticks, 25 % of the computed ramp time) (`8d70df3`, 2026-10-25)

#### Maintenance (build, CI, chores)

- Chore: openfactory.toml settings, gitignore, npm test, setup docs (`f17be91`, 2026-10-25)
- Ci: GitHub Actions workflow and tools/ci_local.sh (1.15) (`b242996`, 2026-10-25)
- Ci: sync the logix and opcua extras in CI and tools/ci_local.sh (`8bfd1df`, 2026-10-25)
- Ci: color-safe CLI tests and screenshot ordering (`c4cade1`, 2026-10-25)

### Milestone 2 — Rockwell and Ignition paths, discrete library complete

#### Features

- Tick-locked viewer physics and the README capture (2.0a) (`b171486`, 2026-10-25)
- Headless part queueing and a spawn-clear emitter (2.0) (`485bc90`, 2026-10-25)
- Scripted stand-in PLC, openfactory run --script (2.10) (`df680b8`, 2026-10-25)
- Binding-level signal transforms in the I/O layer (2.17) (`e84ada5`, 2026-10-25)
- Openfactory lint for likely scene mistakes (2.20) (`7210bc0`, 2026-10-25)
- Pusher plate sized from the emitted parts; lint W206/W207 (`adee2fc`, 2026-10-26)
- Viewer spawn-tick joins, reconnect reload, pixel ratio cap (`68ffd09`, 2026-10-26)
- roller_conveyor component with its own friction (2.5) (`28bfdc5`, 2026-10-26)
- Stopper component and the queue-formation parity trace (2.5) (`c0afd81`, 2026-10-26)
- Turntable component, turning conveyor frames, core-placed parts (2.5) (`69799c6`, 2026-10-26)
- diverter_arm component with fence kinematics (2.5) (`72f8b76`, 2026-10-26)
- pick_and_place component with held parts (2.5) (`9acd8f0`, 2026-10-26)
- distance_sensor, per-sensor analog ranges, ranges in the int16 lockfile (2.5) (`20dd30f`, 2026-10-26)
- Fault injection on every component, /api/fault, viewer fault menu (2.6) (`b1d1997`, 2026-10-26)
- Pilot light supports stuck_off, a burned-out bulb (2.6 ruling 7) (`45f36a7`, 2026-10-27)
- The core places parts touching a diverter arm; parity-diverter gates again (2.6 ruling 2) (`6957de6`, 2026-10-27)
- /api/fault needs a token when the viewer is bound beyond loopback (2.6 ruling 3) (`31eb3f6`, 2026-10-27)
- Conveyor-start-stop scene, the hello world (2.7) (`cd82ee3`, 2026-10-27)
- Buffer-and-release scene, counted batches against a stopper (2.7) (`bba26ef`, 2026-10-27)
- Two-lane-merge scene, a turntable merging a side lane (2.7) (`088efd6`, 2026-10-28)
- Palletizer-lite scene, a gantry forming layers of four (2.7) (`22a3a67`, 2026-10-28)
- Part colors and a color_sensor component, for sorting-by-color (2.7) (`a7b0586`, 2026-10-28)
- Sorting-by-color scene, a color sensor and a diverter arm (2.7 done) (`2e77eb0`, 2026-10-28)
- Scene checks and openfactory test (2.11, part 1) (`66291fd`, 2026-10-28)
- Checks on every shipped scene, graded in CI (2.11, part 2) (`93b4258`, 2026-10-28)
- Run statistics block and /api/stats (2.11, part 3) (`caea093`, 2026-10-28)
- --fault and --fault-at for headless runs and graded tests (2.11, part 4) (`fe1d0e3`, 2026-10-28)
- Exam mode, operator switches drive marked sensors; 2.11 done (`8d0e048`, 2026-10-29)
- Record and replay, --record FILE.ofrec and openfactory replay (2.12) (`e06c847`, 2026-10-29)
- Comms-loss injection, drop and latency on the Modbus server (2.13) (`8440809`, 2026-10-29)
- SIM_HEARTBEAT_DI, a 1 Hz simulator heartbeat on every scene (ruling) (`aa3eb8c`, 2026-10-30)
- L5X controller tag stubs, openfactory export --format l5x (2.3) (`0425e55`, 2026-10-30)
- OPC UA server and Ignition 8.3 tag JSON export (2.4) (`b076c16`, 2026-10-30)
- Demo kit builder and the sorter kit (2.14, part 1; not ticked) (`9bad57b`, 2026-10-30)
- Export --format l5x --prefix AREA (2.3 ruling) (`812e6fd`, 2026-10-30)
- Sorter Perspective faceplate, every kit rebuilt; 2.14 done (`3e1acb0`, 2026-10-30)
- GRAFCET generator tools/grafcet_from_st.py; one-line footer; kits list nothing missing (`a85e977`, 2026-10-31)
- Custom mesh per component, a .glb drawn in place of the fixed housing (2.15) (`6e80c11`, 2026-10-31)
- Composite components declared in scenes; buffer-and-release rebuilt on one (2.16) (`f81cff1`, 2026-10-31)
- Scene bundles, one .ofz with the scene, meshes, lockfiles and script (2.18) (`d96f7b9`, 2026-11-01)
- Generated <scene>.CONTEXT.md beside each scene and in each kit (2.19) (`3cf8670`, 2026-11-01)
- counter_display, a seven-segment readout of an AO (2.5b, item 1 of 8) (`5283f55`, 2026-11-01)
- Lift, a deck conveyor raised between two heights (2.5b, item 2 of 8) (`8288f64`, 2026-11-01)
- Reversible belt and roller conveyors (2.5b, item 3 of 8) (`2b78245`, 2026-11-01)
- Composite logic, library composites, the accumulation conveyor (2.5b, item 4 of 8) (`20920b6`, 2026-11-01)
- W207 across a gap asks for the stroke that puts the whole part on the belt beyond (2.5b, item 5 of 8) (`3629554`, 2026-11-01)
- Parts turn with a turntable deck in orientation, not only position (2.5b, item 6 of 8) (`2ff65f9`, 2026-11-02)
- The pick-and-place pad stops on a part's top; a contact DI (2.5b, item 7 of 8) (`bfe05f1`, 2026-11-02)
- debounce_ms on photo_eye (default 20) and color_sensor (default 0); 2.5b done (item 8 of 8) (`b0c1926`, 2026-11-02)
- A demo kit carries the library composites its scene names (`b61b3f5`, 2026-11-04)
- tools/bench.py, a tick-cost gate against a committed baseline (PLAN 2.9) (`1f5b496`, 2026-11-04)
- Frame time in the viewer's stats overlay; instanced belt slats (PLAN 2.9, in progress) (`b3fc447`, 2026-11-04)
- Comms-loss injection on the OPC UA server, and a comms menu in the viewer (PLAN 2.13b) (`d0dcd3b`, 2026-11-04)
- tools/bench.py reports its scores as CI annotations (PLAN 2.9) (`332b3c7`, 2026-11-06)

#### Fixes

- Per-box divert timers in the sorter's ST program and script (`3991e06`, 2026-10-26)
- A gantry's statistics cycle is one trip to the place side and back (2.11) (`ae5841d`, 2026-10-28)
- Palletizer-lite under capacity, a box every 6 s; 300 s runs in Done-when (ruling) (`ac13954`, 2026-10-28)
- Cite 1756-RM014; PLAN 3.4 sourced to PROCES-RM002/RM200; stale GRAFCET fails --check (`3602275`, 2026-10-31)
- A bundle must carry every library composite its scene names (`b226823`, 2026-11-02)
- A viewer joining a run no longer throws the box held at a raised gate (PLAN 2.9) (`85a49eb`, 2026-11-04)
- No seam loss in the viewer at equal-speed belt seams; parity-turntable gates again (PLAN 2.9) (`84d1110`, 2026-11-04)
- A pushed box stays on the divert belt in the viewer, as in the core (PLAN 2.9) (`e793e82`, 2026-11-06)
- Timing-sensitive tests hold under load; OPC UA latency no longer queues requests (`69920d0`, 2026-11-06)
- OPC UA requests keep their order when the comms latency changes (PLAN 2.13b) (`701d21b`, 2026-11-06)

#### Performance

- Cache belt-local part boxes for the queueing check (`de5bb9c`, 2026-10-26)
- State snapshot off the tick path, one serialization per frame (2.9, first item) (`f6b7e55`, 2026-10-30)
- Queueing check walks a per-belt ordered list; remover prefilter (2.9) (`98f5699`, 2026-10-31)
- Recover the 2.5b tick cost (PLAN 2.9) (`d9cb45c`, 2026-11-04)

#### Documentation

- Logix connection by name, setup pointer, Milestone 2 task 2.0 (`966ae6a`, 2026-10-25)
- No binary over 1 MB enters git; large media goes to release assets (`dbd812a`, 2026-10-26)
- PLAN amendments for 2.5, 2.9 and 2.16 (batch 1 suggestions) (`c6d8051`, 2026-10-26)
- Split 2.5b from 2.5, record the 2.5 rulings and the W207 gap (`c049c7c`, 2026-10-26)
- Component reference for every type, with a registry test (2.5) (`5b9d989`, 2026-10-26)
- Rulings on stuck faults and four component behaviours (2.6 rulings 1 and 5) (`b88c3ee`, 2026-10-27)
- ADR-0004 amendment, scaling ranges in int16_scaled lockfiles and E101 (2.6 ruling 6) (`7d2093b`, 2026-10-27)
- PLAN changes accepted with 2.6 (2.9, 2.11, 2.5b, §6.4, parity rule, ADR-0004) (`419c4d1`, 2026-10-27)
- 2.11 progress, test --plc graded against OpenPLC (2.11, part 5) (`7742150`, 2026-10-28)
- Checks reference, openfactory test in CLAUDE.md, color_sensor debounce in 2.5b (rulings) (`9ae2ca1`, 2026-10-28)
- Rulings of 2026-10-30 in PLAN (2.5 trace, 2.9, 2.11, 2.13, 2.14, 4.12) (`0a7c46a`, 2026-10-30)
- CLAUDE.md, kits/ in the layout and no HIS MCP servers in this repo (`d1577c7`, 2026-10-30)
- GRAFCET for conveyor-start-stop, following the /grafcet-chart-creator method by hand (2.8) (`a90cdf5`, 2026-10-30)
- GRAFCET for sorting-by-height, following the /grafcet-chart-creator method by hand (2.8) (`f3b3226`, 2026-10-30)
- GRAFCET for sorting-by-color, following the /grafcet-chart-creator method by hand (2.8) (`636a467`, 2026-10-30)
- GRAFCET for buffer-and-release, following the /grafcet-chart-creator method by hand (2.8) (`dea044c`, 2026-10-30)
- GRAFCET for two-lane-merge, following the /grafcet-chart-creator method by hand (2.8) (`145e3fc`, 2026-10-30)
- GRAFCET for palletizer-lite, following the /grafcet-chart-creator method by hand; 2.8 done (`c427ebf`, 2026-10-30)
- No HIS MCP references left; L5X and tag JSON re-sourced to public documents (`65bbf0e`, 2026-10-31)
- PLAN rulings of 2026-11-01; open the 2.9 note on the viewer's tipped box (`5ca0244`, 2026-11-01)
- README service area (`efde1d0`, 2026-11-01)
- PLAN §5 component rows for the 2.5b ports and settings (`3b25e5e`, 2026-11-02)
- Jolt vs Rapier for the viewer physics; keep Rapier (PLAN 2.9) (`648180b`, 2026-11-04)
- Two 2.9 notes, the hanging pushed box and the plate a tick ahead (`b39bef0`, 2026-11-06)

#### Tests

- Distance-window scene, a window transform end to end (2.5 done) (`1528e65`, 2026-10-26)
- Lint reports a changed distance sensor range as E101 (`b4c4899`, 2026-10-26)
- Tall-box stopper queue as a gated parity trace (2.5 partly-done item) (`af44674`, 2026-10-27)
- Parity traces for the turntable and gantry demos; beams see core-placed parts (ruling) (`dad2f34`, 2026-10-29)
- Validate every scene against the exported JSON Schema with jsonschema (`54a93fe`, 2026-11-01)

#### Maintenance (build, CI, chores)

- Ci: a failed CI run names its failing step in a public annotation (`bd35d97`, 2026-11-06)

### Milestone 4 — Editor, assets, community

#### Documentation

- Release hygiene, part 1: SECURITY.md, code of conduct, templates, CHANGELOG, 0.2.0 (PLAN 4.8) (`81188fa`, 2026-11-06)
