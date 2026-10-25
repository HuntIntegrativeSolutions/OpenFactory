# OpenFactory — Implementation Plan

**Audience:** the implementing AI session (Opus or Fable) and human contributors.
**How to use this document:** work milestones in order. Within a milestone, work tasks top to bottom.
Each task has a *Do*, a *Why*, and a *Done when* so you can verify completion without asking. Tick the
checkbox in the same commit that completes the task. If a task is blocked, write a short note under it with
the blocker and move to the next task that does not depend on it.

Written 2026-10-24 by HIS Controls with Claude Fable 5.1. Research notes with sources are in
`docs/research/`. Decisions that are settled are in `docs/adr/`. Do not relitigate an ADR; write a new one
that supersedes it if you have a strong reason.

---

## 0. Executive Summary

OpenFactory is an open-source (Apache-2.0) 3D factory and process simulator. The product goal is a tool
that a student, an integrator, or a sales engineer can run in under five minutes, connect to a PLC of their
choice, and extend by editing a text file. The engineering goal is a system an AI assistant can author
scenes for, test headlessly, and verify visually, so that HIS Controls can generate a complete demo (3D
plant + PLC tags + Ignition tags + sequence documentation) from a single scene file.

Four decisions shape everything else:

1. **Scenes are declarative YAML**, not editor output. This is what makes AI authoring, git diffs, CI, and
   code generation possible. (ADR-0001)
2. **Python owns the simulation state and all protocols.** The industrial protocol ecosystem in Python is
   mature and permissively licensed. (ADR-0001)
3. **The viewer is a browser app** (Three.js + Rapier). Zero install for users, and the implementing AI can
   drive it with Playwright to verify its own work. (ADR-0001)
4. **Modbus TCP + OpenPLC is the first supported pair**, because it is free end to end and proves the
   architecture. Rockwell (pycomm3) and Ignition (OPC UA) follow immediately in Milestone 2 because they are
   HIS Controls' daily work.

---

## 1. Goals and Non-Goals

### Goals
- G1. A scene file fully describes a plant: components, geometry, I/O bindings, initial state.
- G2. The core runs deterministically at a fixed step and can run headless for tests and CI.
- G3. At least three protocol families: Modbus TCP, EtherNet/IP (Logix), OPC UA. MQTT Sparkplug B later.
- G4. A browser viewer renders any valid scene with no scene-specific code.
- G5. A scene exports an I/O list CSV compatible with HIS Controls' `/io-to-l5x` and `/io-to-ignition-tags`.
- G6. Ten shipped example scenes covering discrete (sorting, palletizing) and process (tank level, PID).
- G7. Documentation good enough that a stranger can add a component type in an afternoon.

### Non-Goals
- Safety-rated or physically exact simulation. Behavior is plausible, not certified.
- Emulating a PLC. We are the plant, not the controller.
- Photorealism. Clean, readable, ISA-101-friendly visuals are the target.
- A full CAD-style scene editor in Milestone 1 through 3. Text first, editor in Milestone 4.

---

## 2. Research Summary (Why These Choices)

Full notes with links in `docs/research/libraries.md`. The short version:

### 2.1 Rendering and physics in the browser
| Option | License | Verdict |
|--------|---------|---------|
| **Three.js** | MIT | **Chosen.** Largest WebGL ecosystem, GLTF loader built in, huge example base. |
| **Rapier3D** (`@dimforge/rapier3d-compat`) | Apache-2.0 | **Chosen** for rigid-body physics (boxes on belts, pusher collisions). WASM, deterministic variant available, actively maintained. |
| Babylon.js | Apache-2.0 | Viable alternative; chose Three for ecosystem size and the team's familiarity. |
| Godot 4 | MIT | Excellent engine but requires an install or a large WASM export, and cannot be driven by Playwright as easily. Rejected for the viewer; may be revisited for a desktop build. |
| Unity | Proprietary | Rejected. License terms are unsuitable for an open-source project. |

**Why physics in the browser rather than Python:** Rapier gives us a 60 Hz physics loop with zero network
latency for visuals. The Python core remains authoritative for *discrete* state (sensor tripped, cylinder
extended, motor running). The browser reports discrete events back; the core does not simulate box motion.
This split keeps the core simple and testable and keeps the viewer smooth. Trade-off: headless runs without
a browser use a simplified kinematic model in Python (`core/kinematics.py`) for sensor timing. The two
models must agree within tolerance; see test `tests/integration/test_kinematic_parity.py`.

### 2.2 Python protocol libraries
| Need | Library | License | Notes |
|------|---------|---------|-------|
| Modbus TCP server | `pymodbus` 3.x | BSD-3 | Async server, datastore we can bind to the I/O table. Universal; every PLC speaks it. |
| EtherNet/IP to ControlLogix / CompactLogix | `pycomm3` | MIT | Client only. We poll/write the PLC's tags from the simulator. Confirmed scope: ControlLogix, CompactLogix, Micro800. |
| Alternative EtherNet/IP | `libplctag` (Python wrapper) | MPL-2.0 | Fallback if pycomm3 hits a limitation. C library, very mature. |
| OPC UA server | `asyncua` (opcua-asyncio) | LGPL-3.0 | Lets Ignition connect to OpenFactory as a device. LGPL is acceptable for a Python import (no static linking). Document this in `THIRD_PARTY.md`. |
| MQTT Sparkplug B | `paho-mqtt` + own Sparkplug encoder | EPL/EDL | Milestone 3+. Ignition 8.3 has Sparkplug built in. |
| Web server + WebSocket | `FastAPI` + `uvicorn` | MIT / BSD | Serves the viewer bundle and a `/ws/state` stream. |
| Scene schema | `pydantic` v2 | MIT | Validation plus JSON Schema export for the web side and editor autocompletion. |

### 2.3 Soft PLC for the free path
**OpenPLC Runtime v3** (GPL-3.0, separate process, not linked) runs a Modbus TCP *server* on port 502 by
default and can also poll external Modbus *slaves* via its "Slave Devices" page. We support both directions:
- **OpenFactory as slave (default):** OpenFactory serves Modbus; OpenPLC polls it. Matches how OpenPLC
  treats remote I/O and keeps OpenFactory passive.
- **OpenFactory as master:** OpenFactory polls a PLC that only acts as a server (most commercial PLCs).
  Needed for Click, Productivity, Micro800 Modbus, and so on.

### 2.4 Rockwell path (the risk item)
pycomm3 talks to a physical ControlLogix or CompactLogix over EtherNet/IP without issue. **Studio 5000
Logix Emulate** is different: it lives on a virtual backplane and is normally reached through RSLinx /
FactoryTalk Linx, not raw Ethernet. Research tasks in Milestone 2 must confirm which of these works:
  a. pycomm3 to Emulate via RSLinx Classic's virtual backplane bridging (reported working in some
     community posts, unconfirmed).
  b. FactoryTalk Linx Gateway OPC UA server, with OpenFactory as an OPC UA *client* (licensed component,
     but HIS Controls has it).
  c. A small .NET or C# bridge using the Logix Emulate SDK on the Windows host.
  d. A physical CompactLogix 5380 on the bench as the reference, with Emulate as best effort.
Decision rule: ship (d) as the documented path, and whichever of (a)–(c) proves reliable as the Emulate
path. Record the result in `docs/research/logix-emulate.md` and an ADR.

### 2.5 Why not fork an existing open project
Surveyed: open-source conveyor sims on GitHub are mostly student projects with no protocol layer; Gazebo
and Webots are robotics-focused, heavy, and not browser-first; none are declarative-scene-first. Starting
fresh with a narrow component model is less work than bending a robotics simulator to a PLC training use.

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  scenes/*.yaml  ──► core.loader ──► ComponentGraph + IOTable         │
│                                          │                           │
│        ┌─────────────────────────────────┼──────────────────────┐    │
│        ▼                                 ▼                      ▼    │
│  protocols.modbus            protocols.logix (pycomm3)   protocols.opcua
│  (server, port 5020*)        (client, polls PLC tags)    (server, 4840)
│        ▲                                 ▲                      ▲    │
│        │  PLC / OpenPLC / Emulate / Ignition  (external)        │    │
│        └─────────────────────────────────┴──────────────────────┘    │
│                                                                      │
│  core.scheduler  (fixed 50 Hz)  ──► component.step(dt) for each      │
│                                          │                           │
│  server.ws  ──► JSON state frames @ 20 Hz ──► web viewer (Three.js)  │
│  server.ws  ◄── discrete events (sensor trip, part removed) ◄────────│
│                                                                      │
│  export.io_list_csv / export.ignition_tags / export.l5x_stub         │
└─────────────────────────────────────────────────────────────────────┘
 * 5020 by default so no root is needed; `--port 502` for PLCs that insist.
```

### 3.1 Core data model
- `Tag`: `name`, `kind` (DI/DO/AI/AO), `dtype` (bool/int16/float32), `value`, `description`, `owner`
  (component id). DI/AI are written by the simulation and read by the PLC. DO/AO are written by the PLC and
  read by the simulation.
- `IOTable`: dict of tags, thread-safe, with change callbacks for protocol servers.
- `Component`: abstract base with `id`, `type`, `inputs: dict[str, Tag]`, `outputs: dict[str, Tag]`,
  `step(dt)`, `state() -> dict` (what the viewer needs), `geometry() -> dict` (static, sent once).
- `Scene`: metadata + list of components + optional `connections` (which conveyor feeds which) + `plc`
  block (protocol selection and settings).
- `Scheduler`: fixed-step loop with accumulator; `--realtime` (default) or `--as-fast-as-possible` for tests.

### 3.2 Determinism rules
- `step(dt)` never reads the clock. Timers accumulate `dt`.
- Random behavior (emitter jitter) uses a scene-level seed.
- Order of component stepping is the order in the scene file. Document this; it matters for same-tick
  sensor interactions.

### 3.3 Viewer contract
- `GET /api/scene` returns the resolved scene with geometry, once.
- `WS /ws/state` streams `{t, components: {id: state}}` at 20 Hz; viewer interpolates.
- `WS /ws/events` upstream: `{t, type: "sensor", id, value}` and `{t, type: "part", ...}`.
- Viewer never computes PLC-visible logic. If a sensor reading can be derived from Rapier contacts in the
  browser, the browser reports it and the core *decides* whether the tag changes (debounce, fault
  injection).

---

## 4. Scene File Specification (v0.1)

```yaml
openfactory: "0.1"
scene:
  name: sorting-by-height
  description: Two-lane box sorter. Tall boxes diverted by pusher, short boxes pass through.
  author: HIS Controls
  seed: 42
  units: meters
plc:
  protocol: modbus_tcp          # modbus_tcp | logix | opcua | none
  role: server                  # server | client
  bind: 127.0.0.1
  port: 5020
components:
  - id: CONV_01
    type: belt_conveyor
    description: Infeed Belt Conveyor — Sorting Line
    position: [0, 0, 0]          # x (along flow), y (up), z
    rotation_deg: 0
    length: 4.0
    width: 0.5
    speed_mps: 0.5               # used when speed AO absent
    io:
      run: CONV_01_RUN_DO
      speed: CONV_01_SPD_AO      # optional; 0-100 %
      running: CONV_01_RUN_DI    # feedback
  - id: PE_01
    type: photo_eye
    description: Infeed Box Present Photo Eye
    position: [1.5, 0.15, 0]
    beam_length: 0.6
    io:
      detect: PE_01_DI
  - id: CYL_01
    type: pusher
    description: Tall Box Divert Pusher
    position: [2.5, 0.1, -0.3]
    stroke: 0.5
    extend_time_s: 0.4
    io:
      extend: CYL_01_EXT_DO
      extended: CYL_01_EXT_DI
      retracted: CYL_01_RET_DI
  - id: EMT_01
    type: box_emitter
    description: Infeed Box Source
    position: [0.2, 0.3, 0]
    interval_s: 3.0
    parts:
      - {name: tall, size: [0.3, 0.4, 0.3], weight: 0.5}
      - {name: short, size: [0.3, 0.2, 0.3], weight: 0.5}
    io:
      enable: EMT_01_EN_DO
  - id: REM_01
    type: part_remover
    position: [4.1, 0, 0]
    size: [0.3, 1.0, 1.0]
```

Rules:
- Tag names are globally unique, match `^[A-Z][A-Z0-9_]{0,39}$`, end in `_DI|_DO|_AI|_AO`.
- Unbound optional `io` keys are allowed; the component uses its default.
- `position` is the component's local origin, documented per type in §5.
- The loader rejects unknown keys (pydantic `extra="forbid"`) so typos surface immediately.

---

## 5. Component Library

Each row is a Python module in `src/openfactory/components/` and a mesh builder in `web/src/components/`.
Milestone column says when it ships. Geometry is primitive-based (boxes, cylinders) until Milestone 4, when
CC-BY GLTF models replace primitives where it helps readability.

| Type | Inputs (DO/AO from PLC) | Outputs (DI/AI to PLC) | Behavior spec | MS |
|------|------------------------|-----------------------|---------------|----|
| `belt_conveyor` | run, speed (opt) | running | Moves parts on its top surface at `speed_mps × speed%` when run. Ramp 0.3 s. | 1 |
| `photo_eye` | — | detect | True while any part intersects the beam segment. 20 ms debounce. | 1 |
| `pusher` | extend | extended, retracted | Linear actuator; moves parts it contacts. Both limits false mid-stroke. | 1 |
| `box_emitter` | enable | — | Spawns a part every `interval_s ± jitter` while enabled. Max parts in scene = 200. | 1 |
| `part_remover` | — | — | Deletes parts entering its volume. Counts by part name for scene stats. | 1 |
| `pushbutton` | — | pressed | Viewer click sets true for 300 ms (momentary) or toggles (maintained). | 1 |
| `pilot_light` | on | — | Viewer visual only. | 1 |
| `selector_switch` | — | pos_a, pos_b | Two or three position. | 1 |
| `roller_conveyor` | run | running | Same as belt, different geometry; parts roll (lower friction). | 2 |
| `stopper` | raise | raised | Blade that blocks parts on a conveyor. | 2 |
| `turntable` | run, dir | at_0, at_90 | Rotates a short conveyor 90°. | 2 |
| `diverter_arm` | extend | extended | Angled arm that routes parts to a side conveyor. | 2 |
| `pick_and_place` | grip, move_x, move_z | at_pick, at_place, holding | Simplified gantry, two positions. | 2 |
| `counter_display` | value (AO) | — | 4-digit readout on a panel. | 2 |
| `tank` | — | level (AI), hi_sw, lo_sw | Mass balance: Σ inflows − Σ outflows. Height from volume and footprint. | 3 |
| `valve_onoff` | open | opened, closed | Travel time; flow coefficient Cv passes to connected pipe. | 3 |
| `valve_control` | position (AO) | position_fb (AI) | First-order lag, stroke time. | 3 |
| `pump` | run, speed (opt) | running, flow (AI) | Flow = f(speed, head) with a simple curve. | 3 |
| `pipe` | — | — | Topology only; connects sources and sinks for mass balance. | 3 |
| `heater` | on, power (AO) | temp (AI) | First-order thermal model on connected tank. | 3 |
| `transmitter` | — | pv (AI) | Generic 4–20 mA style scaling of any process variable with noise and fault injection. | 3 |
| `vfd_motor` | run, speed (AO) | running, speed_fb (AI), fault | Drives a conveyor or pump; trip on overload. | 3 |

Every component implements `fault_inject(kind)` from Milestone 2 so instructors can break sensors
(stuck on, stuck off, noisy) during training.

---

## 6. Protocol Design

### 6.1 Modbus TCP register map (generated, deterministic)
- Sort tags by kind then name. Allocate:
  - DI → discrete inputs (FC02) from 0
  - DO → coils (FC01/05/15) from 0
  - AI → input registers (FC04) from 0, float32 as two registers big-endian word order (configurable)
  - AO → holding registers (FC03/06/16) from 0, same encoding
- Analog encoding is per scene: `plc.analog_encoding: float32 | int16_scaled`. Default `float32`
  (two registers, high word first). `int16_scaled` uses one register, 0–32767 for 0–100 % of the tag's
  `range`, the same raw-count convention as a 1756-IF8 4–20 mA channel. OpenPLC v3 does no float
  decoding on its Slave Devices path, so OpenPLC examples use `int16_scaled` (ADR-0003 to record).
- Write the map into the exported CSV (`modbus_address` column) and print it at startup.
- Address stability: adding a tag never renumbers existing ones *within a scene version*. The scene file
  may pin `address:` on a tag to override allocation.

### 6.2 Logix (pycomm3) mapping
- Scene `plc.protocol: logix` with `plc.path: "192.168.1.10/1/0"` (device alias file, never in the scene
  itself; the scene references `plc.connection: bench_l33er` resolved from `openfactory.toml`).
- Tag name in the PLC equals the OpenFactory tag name by default. Optional `plc_tag:` per binding for
  mapping into a UDT (`Sim.CONV_01.RUN`).
- Poll DO/AO with one `read()` of all tags every 50 ms; write DI/AI with one batched `write()` on change.
- Add a `SIM_HEARTBEAT_AI` tag the PLC can watch to detect simulator loss.

### 6.3 OPC UA server (asyncua)
- Namespace `urn:openfactory:sim`. Folder per component, variable per tag, writable for DO/AO.
- Ignition connects with its OPC UA client, tags land under `[OpenFactory]`. This is also the path for
  FactoryTalk Linx Gateway and Kepware.

### 6.4 Security defaults
- Bind to loopback unless `--bind` given. No authentication on Modbus (protocol has none); OPC UA
  anonymous by default with a documented switch to username/password. Say plainly in docs: this is a lab
  tool, keep it off plant networks.

---

## 7. Milestones

### Milestone 0 — Project foundation (this week)
- [x] Project folder, `CLAUDE.md`, `README.md`, `CONTRIBUTING.md`, `LICENSE`, `.gitignore`
- [x] `docs/PLAN.md` (this file), `docs/research/libraries.md`, `docs/adr/ADR-0001-stack.md`
- [x] `git init`, first commit, `main` branch protected locally by convention
      *Done when:* `git log` shows the foundation commit.
- [x] `pyproject.toml` with `uv`, `ruff`, `pytest`; `src/openfactory/__init__.py` with `__version__`
      *Done when:* `uv run pytest` runs (zero tests collected is fine) and `uv run ruff check .` passes.
- [x] `web/` scaffold: `npm create vite@latest` (vanilla-ts), add `three`, `@dimforge/rapier3d-compat`,
      `@types/three`; `npm run build` succeeds
      *Done when:* `web/dist/index.html` exists after build.
- [x] `THIRD_PARTY.md` listing every dependency and license (regenerate with `pip-licenses` and
      `license-checker`)

### Milestone 1 — Walking skeleton: one scene, Modbus, browser (target 2–3 weeks of sessions)
Goal: `openfactory run scenes/sorting-by-height.yaml` serves Modbus on 5020 and a viewer on 8000; OpenPLC
runs the sorting program against it; a box visibly gets pushed.

- [x] 1.1 `core/schema.py`: pydantic models for §4, `extra="forbid"`, JSON Schema export to
      `web/public/scene.schema.json`
      *Why:* the schema is the contract between every other part. Get it right first.
      *Done when:* `tests/unit/test_schema.py` loads the example scene, rejects an unknown key, rejects a
      bad tag name.
- [x] 1.2 `core/iotable.py`: `Tag`, `IOTable`, change callbacks, thread-safe get/set
      *Done when:* unit tests for set/get/callback and for kind-based write restriction (PLC cannot write
      a DI).
- [x] 1.3 `core/component.py` base class + registry decorator `@register("belt_conveyor")`
      *Done when:* registering a duplicate type name raises; loader resolves type strings to classes.
- [ ] 1.4 Components: `belt_conveyor`, `photo_eye`, `pusher`, `box_emitter`, `part_remover`,
      `pushbutton`, `pilot_light`, `selector_switch`, each with unit tests
      *Why:* this is the minimum set for a classic sorter. Keep behavior simple and documented.
      *Done when:* each `step()` is covered; `pusher` limit switches verified across a full cycle.
- [ ] 1.5 `core/kinematics.py`: headless part motion (position along conveyor axis, transfer between
      connected conveyors, photo-eye intersection, remover). No Rapier.
      *Why:* CI and PLC logic tests must run without a browser.
      *Done when:* `tests/unit/test_kinematics.py` moves a part down two chained conveyors and trips a
      photo eye at the expected time ± 1 tick.
- [ ] 1.6 `core/scheduler.py`: fixed 50 Hz with accumulator; `--as-fast-as-possible`
      *Done when:* 10 simulated seconds complete in < 0.5 s wall time in fast mode; realtime drift < 1%.
- [ ] 1.7 `protocols/modbus.py`: pymodbus 3.16.x async server bound to IOTable, allocation per §6.1.
      Bind through `SimDevice(action=hook)`; `ModbusSlaveContext` no longer exists and the legacy
      datablocks do not give live reads. Always pass `address=("127.0.0.1", port)` explicitly. Hook must
      be idempotent (FC05 can invoke it twice). See `docs/research/libraries.md` "pymodbus 3.x datastore
      binding".
      *Done when:* `tests/integration/test_modbus_roundtrip.py` uses pymodbus client to write a coil and
      sees `CONV_01_RUN_DO` change; reads a DI after the sim trips it.
- [ ] 1.8 `export/io_list_csv.py`: columns `tag,kind,dtype,description,component,modbus_address`
      *Done when:* output validates with HIS Controls `/io-validate` skill.
- [ ] 1.9 `server/app.py`: FastAPI, `/api/scene`, `/ws/state`, `/ws/events`, static `web/dist`
      *Done when:* `curl /api/scene` returns resolved geometry; a websocket client receives ≥ 15 frames/s.
- [ ] 1.10 CLI `openfactory run|validate|export` via `typer`
      *Done when:* `openfactory --help` lists the three; `validate` exits 1 on a bad scene.
- [ ] 1.11 Viewer: load scene JSON, build primitive meshes per component type, orbit camera, dark ISA-101
      background, status colors (running green `#22C55E`, stopped grey `#9CA3AF`, alarm red `#EF4444`)
      *Done when:* Playwright screenshot of the sorter scene shows two conveyors, a pusher, a photo eye.
- [ ] 1.12 Viewer physics: Rapier world, parts as dynamic boxes, conveyor top as
      `kinematicVelocityBased` body with `setLinvel` **and `setTranslation(home)` reset every step**
      (otherwise the belt drifts; contact surface velocity is Rust-only), pusher as
      `kinematicPositionBased`; sensor beam as a sensor collider with collision events; events sent
      upstream. Draw the belt mesh at `home`, not at the body translation. Part ramp is friction-limited
      (μ·g), so `kinematics.py` models the same ramp or parity is tested at scene friction.
      *Done when:* with PLC coil forced on, a box travels the belt and a photo-eye event arrives in the
      core; parity test against `kinematics.py` within 2 ticks.
- [ ] 1.13 `tools/screenshot.py`: launches server + Playwright, waits N sim seconds, saves PNG
      *Why:* this is how the AI verifies visual work. Build it early and use it every time.
- [ ] 1.14 `examples/openplc/sorting-by-height.st` + `docs/guides/openplc-quickstart.md` with the Slave
      Devices settings (IP, port 5020, start addresses, sizes). Use `analog_encoding: int16_scaled` for
      this scene (see §6.1); OpenPLC delivers raw 16-bit words with no float decoding. OpenPLC polls at
      100 ms by default, so the example logic must not rely on photo-eye pulses shorter than ~200 ms;
      size emitter spacing and belt speed accordingly. **Container:** there is no official OpenPLC image
      on Docker Hub (only personal uploads; do not use them). Build from the upstream Dockerfile at the
      pinned commit: `git clone https://github.com/thiagoralves/OpenPLC_v3 && git checkout b5d4135 &&
      docker build -t openfactory/openplc:b5d4135 .` Image was pre-built on the dev host 2026-10-25.
      Run with `docker run -d --name openplc -p 8080:8080 -p 502:502 openfactory/openplc:b5d4135`; the
      web UI is on 8080 (default login openplc/openplc), and Slave Devices must point at the WSL host IP
      (`hostname -I`) on port 5020, not 127.0.0.1, since the container has its own loopback. Run only
      this one container; this host freezes under heavy container fleets.
      *Done when:* a fresh OpenPLC install follows the guide and sorts boxes. Record a 30 s screen
      capture for the README.
- [ ] 1.15 CI: GitHub Actions running `pytest`, `ruff`, `npm run build`, and the headless parity test

### Milestone 2 — Rockwell and Ignition paths, discrete library complete (target 3–4 weeks)
- [ ] 2.1 Research spike: Logix Emulate connectivity, options (a)–(d) in §2.4. Write
      `docs/research/logix-emulate.md` and ADR-0002.
- [ ] 2.2 `protocols/logix.py` with pycomm3, batched read/write, heartbeat, reconnect with backoff
      *Done when:* bench CompactLogix runs the sorter with tags generated by `/io-to-l5x`.
- [ ] 2.3 `export/l5x_stub.py`: controller tags only (BOOL/REAL) for a scene, via existing
      `/io-to-l5x` where possible. Writes to the consuming project's `03-plc/generated/`, never here.
- [ ] 2.4 `protocols/opcua.py` with asyncua; `export/ignition_tags.py` producing 8.3 tag JSON
      *Done when:* Ignition 8.3 gateway shows Good quality on all tags; a Perspective view from
      `/perspective-view` starts the conveyor.
- [ ] 2.5 Components: `roller_conveyor`, `stopper`, `turntable`, `diverter_arm`, `pick_and_place`,
      `counter_display`
- [ ] 2.6 `fault_inject` API on all components + `/api/fault` endpoint + viewer context menu
- [ ] 2.7 Scenes: `conveyor-start-stop` (hello world), `sorting-by-height`, `sorting-by-color`,
      `buffer-and-release`, `palletizer-lite`, `two-lane-merge`
- [ ] 2.8 Scene → GRAFCET: feed the example PLC sequence to `/grafcet-chart-creator` for each scene and
      ship the HTML in `docs/sequences/`
- [ ] 2.9 Performance: 200 parts at 60 fps in the viewer on integrated graphics; profile and fix

### Milestone 3 — Process simulation (target 3–4 weeks)
- [ ] 3.1 Flow network solver (`core/flownet.py`): directed graph of sources, pipes, valves, pumps,
      tanks; per-tick mass balance, simple pressure-free model first (flow = Cv × opening × pump curve)
- [ ] 3.2 Components: `tank`, `valve_onoff`, `valve_control`, `pump`, `pipe`, `heater`, `transmitter`,
      `vfd_motor`
- [ ] 3.3 Scenes: `tank-level-onoff`, `tank-level-pid`, `two-tank-cascade`, `batch-mix-heat`
- [ ] 3.4 PlantPAx demo: scene + L5X with `P_AIn`, `P_PIDE`, `P_ValveC`, `P_Motor` instances (via
      `/plantpax-*` skills, parameters from `ai-memory-vendor-docs`) + Perspective faceplates
- [ ] 3.5 MQTT Sparkplug B publisher (`protocols/sparkplug.py`) tested against Ignition 8.3 MQTT Engine
- [ ] 3.6 Trend endpoint `/api/history?tag=` ring buffer so the viewer can show a 10-minute trend

### Milestone 4 — Editor, assets, community (ongoing)
- [ ] 4.1 In-browser scene editor: drag components, edit properties with schema-driven forms, export YAML
- [ ] 4.2 CC-BY GLTF models for the top ten components; `ATTRIBUTION.md`
- [ ] 4.3 Multi-viewer: one core, many browsers (classroom)
- [ ] 4.4 Docs site (MkDocs Material), component reference generated from docstrings
- [ ] 4.5 Public GitHub release, issue templates, Discussions, first good-first-issues
- [ ] 4.6 Desktop packaging (PyInstaller + bundled viewer) for Windows users without Python

---

## 8. Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Logix Emulate not reachable from Python | Rockwell demo path weaker | §2.4 spike early in M2; physical CompactLogix as reference; OPC UA via Linx Gateway as fallback |
| Browser physics and headless kinematics diverge | Flaky tests, PLC timing differs by mode | Parity test from day one; keep kinematics authoritative for sensors; tolerance documented |
| Scope creep in component library | Milestones slip | Table in §5 is the list. New types need an issue and a PLAN edit first |
| LGPL in asyncua | License questions from adopters | `THIRD_PARTY.md` explains dynamic import; offer `open62541` binding as alternative later |
| Performance with many parts | Demo looks bad | Cap parts at 200; instanced meshes; profile in 2.9 |
| Clean-room doubt | Legal exposure | CONTRIBUTING policy, DCO sign-off, never open `04_Software` from this project |

---

## 9. Working Agreement for the Implementing Session

1. **Start each session** by reading `CLAUDE.md`, then the current milestone here, then `git log -5`.
2. **One task per commit** where practical. Tick the box in the same commit.
3. **Test before claim.** "Done when" is the acceptance test. Run it. Paste the result in the commit
   body or PR description.
4. **Visual work is verified visually.** Use `tools/screenshot.py` and look at the PNG.
5. **Protocol facts come from docs**, retrieved through `ai-memory-vendor-docs` or `deep-research`, not
   from memory. Cite the source in a code comment.
6. **When stuck for more than two attempts**, write the blocker under the task and move on. Do not loop.
7. **New decisions get an ADR.** Short is fine: context, decision, consequences.
8. **Never touch** `E:\04_Software`, `E:\Hyper-V VMs`, or `E:\VirtualBox-VMs`.

---

## 10. Sources

See `docs/research/libraries.md` for the full list with retrieval dates. Key references:
- Three.js (MIT): https://github.com/mrdoob/three.js
- Rapier (Apache-2.0): https://rapier.rs/docs/ and https://www.npmjs.com/package/@dimforge/rapier3d
- pymodbus (BSD-3): https://github.com/pymodbus-dev/pymodbus
- pycomm3 (MIT): https://pycomm3.readthedocs.io/ and https://github.com/ottowayi/pycomm3
- opcua-asyncio (LGPL-3): https://github.com/FreeOpcUa/opcua-asyncio
- OpenPLC Runtime v3 docs, Modbus and Slave Devices: https://autonomylogic.com/
- Eclipse Sparkplug B specification: https://sparkplug.eclipse.org/
- ISA-101 / HIS Controls color conventions: `~/.claude/rules/ignition-conventions.md`
