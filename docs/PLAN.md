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
- Virtual commissioning of whole lines from CAD (Emulate3D, Siemens SIMIT/NX MCD, Dassault DELMIA, Omniverse). OpenFactory
  is a training cell and demo tool one level below those; the USD exporter (4.10) is the bridge up.
- Discrete-event throughput modeling (FlexSim, Arena, Plant Simulation). Those answer "how many per
  hour under statistical variation" over thousands of runs. OpenFactory runs one plant in real time
  with a controller in the loop and reports what happened; its statistics (2.11) describe a run, they
  do not predict a design.

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
*(Updated 2026-10-26: a survey of the field, including the MIT-licensed Open Industry Project which is
the closest existing work, is in `docs/research/landscape.md`. The conclusion below stands; the
reasons specific to OIP are recorded there.)*

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
- `GET /api/scene` returns the resolved scene with geometry. The viewer fetches it at start and
  again whenever `/ws/state` reconnects (the server may have restarted with another scene); it
  rebuilds the plant if the document changed and always restarts its physics world.
- `WS /ws/state` streams `{t, tick, components: {id: state}, parts, io}` at 20 Hz. Each part
  carries `velocity`, `spawn_tick`, and `spawn_position`, so the viewer adds a new part before
  stepping the tick the core first moved it in (`spawn_tick + 1`), not at the next frame.
- Part authority in the viewer (ruled 2026-10-26, PLAN 2.5). **Rapier-driven:** parts resting on
  or falling onto belt and roller conveyors and a turntable deck that is not turning, parts pushed
  by a pusher plate, and parts standing against a stopper blade; these are what the parity traces
  compare. **Core-driven:** parts held by a `pick_and_place` gripper, parts in contact with a
  `diverter_arm` (ruled 2026-10-27), and parts on a turning turntable deck. A frame marks these `follow: true`; the viewer makes the part's body
  kinematic and moves it to the core's position across the frame's steps, and when `follow` ends
  it becomes dynamic again with the core's `velocity`. A distance sensor's reading is core-only
  (no physics body).
- `WS /ws/events` upstream: `{t, type: "sensor", id, value}` and `{t, type: "part", ...}`.
- Viewer never computes PLC-visible logic. If a sensor reading can be derived from Rapier contacts in the
  browser, the browser reports it and the core *decides* whether the tag changes (debounce, fault
  injection).
- Sensor authority (decided 2026-10-25):
  The headless model is authoritative for PLC tags through Milestone 2; browser sensor events are parity-only.
  The core's kinematic model (`core/kinematics.py`) writes every DI the PLC reads. The core records
  each browser `sensor` event with the tick it arrived (`/api/events`) and never applies it to a
  tag; the events feed parity checks only (PLAN 1.12). Handing a sensor to the browser is
  revisited no earlier than Milestone 3.

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
  # bind / port: optional overrides; defaults come from openfactory.toml
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
      speed: {tag: CONV_01_SPD_AO, initial: 100}  # optional; 0-100 %, 100 until the PLC writes
      running: CONV_01_RUN_DI    # feedback
  - id: PE_01
    type: photo_eye
    description: Infeed Tall Box Photo Eye
    position: [1.5, 0.3, 0]      # above short boxes (0.2 m), below tall ones (0.4 m)
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
  # The shipped scene adds CONV_02, a divert lane alongside CONV_01 that the pusher moves tall
  # boxes onto, and REM_02 at its end.
connections:                     # optional; which conveyor feeds which
  - {from: CONV_01, to: CONV_02} # (illustrative: in this scene CONV_02 is fed by the pusher)
```

Rules:
- Tag names are globally unique, match `^[A-Z][A-Z0-9_]{0,39}$`, end in `_DI|_DO|_AI|_AO`.
- Unbound optional `io` keys are allowed; the component uses its default.
- A binding is a tag name or a mapping `{tag: NAME, address: N, initial: V}`; both keys are
  optional. DI bindings may also carry signal transforms, `invert`, `on_delay_s`, `off_delay_s`,
  and `combine: or|and` with `from: [A.port, B.port]`; DO bindings may carry `invert`; an AI port
  may bind a `_DI` tag with `window: [lo, hi]` (PLAN 2.17). AI and AO bindings may set the tag's
  own `range: [lo, hi]` (in the port's units) and a `units` label for the I/O list CSV and
  CONTEXT.md only (PLAN 3.7; label only, ruled 2026-11-08).
  `address` pins the tag's Modbus address (§6.1, ADR-0004). `initial`, on DO and AO tags
  only, seeds the I/O table at startup before any PLC write: `true`/`false` for a DO, an
  engineering value inside the port's range for an AO (a value outside it fails at load). The
  PLC's first write replaces it, and `openfactory run --force TAG=VALUE` overrides it. Without
  `initial` a DO starts false and an AO starts at 0, so a belt whose speed AO is bound but not yet
  written stands still even with its run coil on; the shipped sorter sets `initial: 100` on both
  belts' speed AOs for that reason.
- `position` is the component's local origin, documented per type in §5.
- The loader rejects unknown keys (pydantic `extra="forbid"`) so typos surface immediately.
- `connections` (optional) lists conveyor handoffs: parts leaving `from`'s discharge end land on
  `to`'s infeed, bridging any gap, height step, or turn like a transfer plate. Both ends must be
  conveyors, a conveyor cannot feed itself, and each conveyor feeds at most one other; several may
  feed the same conveyor (a merge), and loops (recirculation) are allowed. Collinear conveyors that
  touch still need a connection for the headless model to hand parts across; the loader warns (it
  never infers the connection) when one conveyor's discharge end is within 2 cm of another's
  infeed, at any heading, and no connection joins them (W212 from the conveyors' snap ports,
  PLAN 4.1d; until 2026-11-12 W203 at 5 mm), and when a connection's ends do not meet (W213).
- Optional keys not shown in the example:
  - `scene.friction` (default 0.8, μ between belts and parts): the viewer gives belt and part
    colliders this friction. The headless model caps a carried part's acceleration at μ × 9.81 m/s²
    and, on landing, lets friction change its belt-direction speed by up to μ × |fall speed| at once
    (the impact impulse), so the two models agree on when parts reach belt speed (PLAN 1.12 parity).
    A part straddling a connection seam rests on both belts by overlap share, and each pulls with up
    to its share of μ·g (PLAN 2.0), so a part crossing onto a faster belt speeds up as Rapier's does.
  - `box_emitter.jitter_s` (default 0): uniform ± jitter added to each `interval_s`; must be smaller
    than `interval_s`.
  - `box_emitter.io.enable` is optional; when unbound the emitter is always enabled.
  - `selector_switch.positions` (2 or 3, default 2) and `selector_switch.initial` (`a`, `center`, or
    `b`, default `a`; `center` needs 3 positions).
  - `pushbutton.mode` (`momentary` or `maintained`) and `color` on pushbuttons and pilot lights.
  - `pusher.plate_width` (local x, default 0.3 m) and `pusher.plate_height` (default: the plate
    top reaches 3/4 of the tallest emitted part's height above the belt it sweeps, else 0.15 m).
  - `checks:` (PLAN 2.11), the exercise checks `openfactory test` grades a run against:
    ```yaml
    checks:
      duration_s: 60                                 # how long `test` runs (default 60)
      parts_reached: {REM_01: short, REM_02: [tall]} # each name reaches only that remover
      none_lost: true                                # no part falls out of the scene
      max_cycle_s: 12                                # spawn to remover, and oldest part left
    ```
    Reference with both failure rules spelled out: `docs/scene-format.md`.
  - `exam: switch` on a DI binding (PLAN 2.11): with `run --exam` an operator switch in the viewer
    drives that tag instead of the simulation (`docs/scene-format.md`).
    Every key is optional. `parts_reached` names part removers and part names an emitter makes,
    each name under one remover only; a wrong id or name fails at load. A name passes when at
    least one part of it reached that remover and none reached another, so a stopped line fails.
    `max_cycle_s` fails on a removed part that took longer, and on a part still in the scene at
    the end that is older, so a jammed line fails too.
- Connection settings live in `openfactory.toml` (git-ignored overrides in `openfactory.local.toml`),
  never in scenes. The `plc` block selects `protocol` and `role`; `bind` and `port` are optional
  per-scene overrides of the server's listen address, and a PLC's own address is only ever a named
  `plc.connection` resolved from `openfactory.toml`. Precedence: CLI flag > scene override >
  `openfactory.local.toml` > `openfactory.toml` > built-in defaults.
- `plc.bind` accepts loopback addresses only (`127.0.0.0/8` or `::1`); any other value, including a
  hostname, fails at load with a message pointing to the `--bind` CLI flag. A scene file, which may
  come from anyone, can never open a server to the network (§6.4).
- The authoritative, machine-readable form is `web/public/scene.schema.json`, generated from
  `src/openfactory/core/schema.py`.

---

## 5. Component Library

Each row is a Python module in `src/openfactory/components/` and a mesh builder in `web/src/components/`.
Milestone column says when it ships. Geometry is primitive-based (boxes, cylinders) until Milestone 4, when
CC-BY GLTF models replace primitives where it helps readability.

| Type | Inputs (DO/AO from PLC) | Outputs (DI/AI to PLC) | Behavior spec | MS |
|------|------------------------|-----------------------|---------------|----|
| `belt_conveyor` | run, speed (opt), reverse (with `bidirectional`, 2.5b) | running | Moves parts on its top surface at `speed_mps × speed%` when run, toward the infeed with reverse. Ramp 0.3 s. | 1 |
| `photo_eye` | — | detect | True while any part intersects the beam segment. `debounce_ms` debounce (default 20, 2.5b). | 1 |
| `pusher` | extend | extended, retracted | Linear actuator; moves parts it contacts. Both limits false mid-stroke. | 1 |
| `box_emitter` | enable | — | Spawns a part every `interval_s ± jitter` while enabled, once its spawn volume is clear. Max parts in scene = 200. | 1 |
| `part_remover` | — | — | Deletes parts entering its volume. Counts by part name for scene stats. | 1 |
| `pushbutton` | — | pressed | Viewer click sets true for 300 ms (momentary) or toggles (maintained). | 1 |
| `pilot_light` | on | — | Viewer visual only. | 1 |
| `selector_switch` | — | pos_a, pos_b | Two or three position. | 1 |
| `roller_conveyor` | run, speed (opt), reverse (with `bidirectional`, 2.5b) | running | Same as belt, drawn as rollers; its own `friction` (default the scene's). | 2 |
| `stopper` | raise | raised, lowered | Blade that rises out of the belt and holds the queue; a part already over it passes. | 2 |
| `turntable` | run, turn | running, at_0, at_90 | Turns its deck conveyor ±90°; `turn` held drives to `turn_deg`, released returns to 0. | 2 |
| `lift` | run, raise | running, at_top, at_bottom | Raises its deck conveyor `lift_height`; `raise` held drives to the top, released returns to the bottom; hands parts across only while level. | 2 (2.5b) |
| `diverter_arm` | extend | extended, retracted | Paddle swinging across a belt; parts slide along it onto a side conveyor. | 2 |
| `pick_and_place` | traverse, lower, grip | at_pick, at_place, lowered, holding, contact (2.5b) | Two-axis gantry with a gripper pad; the pad stops on a part's top (2.5b). | 2 |
| `distance_sensor` | — | distance (AI), switching (AI) | Range to the nearest part or belt along its beam, metres 0 to `beam_length`. | 2 |
| `color_sensor` | — | match, detect | Diffuse sensor taught one part `color`; `match` while the nearest part in range has it; `debounce_ms` (default 0, 2.5b). | 2 (2.7) |
| `counter_display` | value (AO) | — | Seven-segment readout on a panel, `digits` 1-6 (default 4), `decimals`; dashes when the value does not fit. | 2 (2.5b) |
| `tank` | — | level (AI), hi_sw, lo_sw, temp (AI) | Node of the flow network (ADR-0005): volume from the solved flows, level from volume and footprint; overflows at its height; well mixed. | 3 (3.2) |
| `valve_onoff` | open | opened, closed | Link of the flow network: opens or closes over `travel_s` (held command, reverses mid-travel); `Q = Kv·x·√Δp`, `cv` or `kv`. | 3 (3.2) |
| `valve_control` | position (AO) | position_fb (AI) | Link of the flow network: stem with rate limit and first-order lag, linear or equal-percentage characteristic, fail position (float32 only), a hand valve when unbound. | 3 (3.2) |
| `pump` | run, speed (opt) | running, flow (AI) | Link of the flow network: EPANET's power curve (single point: 133 % shutoff, twice the design flow), affinity with speed, forward only, starves on a dry tank. | 3 (3.2) |
| `pipe` | — | — | Link of the flow network: Darcy-Weisbach resistance at a fixed friction factor plus minor losses, the drawn length by default, optional check valve. | 3 (3.2) |
| `supply` | — | — | Node of known head (ADR-0005): a fixed gauge pressure, never exhausted; delivers at its temperature and ingredient. | 3 (3.2) |
| `drain` | — | — | Open: a node at atmospheric head taking what arrives. With a demand (and an optional schedule): a junction drawing it pressure-dependently (ADR-0005 decision 4). | 3 (3.2) |
| `junction` | — | — | Node whose head is solved; its pressure read through a transmitter. | 3 (3.2) |
| `heater` | on, power (AO) | temp (AI) | Immersion heater in one tank: rated power times the command while covered, less the tank's UA loss; well mixed. | 3 (3.2) |
| `transmitter` | — | pv (AI) | Level, temperature, fraction, flow or pressure over its range, with lag, seeded noise, and faults including NE 43 `signal_fail`. | 3 (3.2) |
| `vfd_motor` | run, speed (AO), reset | running, speed_fb (AI), fault | Drives a pump or a belt/roller conveyor in place of its run and speed; ramps; trips on overload (a pump's shaft power above rating for `trip_s`), reset by edge. | 3 (3.2) |
| `scenery` | — | — | A body only: a box of `size` in `color`, or a mesh; no I/O, no physics, parts pass through. The body of the `driven_item` and `variable_item` library composites. | 5 (5.2c) |

#### Milestone 1 behavior as built

The full behavior spec for each type is in its module docstring (`src/openfactory/components/`), and
[`docs/components.md`](components.md) is the component reference (parameters, ports, behavior) for every type,
Milestone 2 ones included. The choices that go beyond the table above:

- **`belt_conveyor`**: origin is the center of the infeed end at belt top-surface height; flow is local
  +x. `running` follows the `run` command in the same tick (contactor auxiliary contact). Surface
  speed ramps linearly, 0.3 s for a full-scale change, in both directions, so a stopped belt coasts.
  Unbound `speed` means 100 %; the speed command is clamped to 0–100 %.
- **`photo_eye`**: origin is the beam midpoint; the beam spans local z. A part blocks it when its box
  intersects the beam segment. The 20 ms debounce applies to both edges and is measured from the
  first step that sees the change (one tick of delay at 50 Hz).
- **`pusher`**: origin is the center of the retracted push-plate face; extends along local +z.
  Single-solenoid spring return: `extend` off retracts at the same speed, and a command change
  mid-stroke reverses immediately. The plate is `plate_width` × `plate_height` (default width 0.3 m;
  default height sized at load so the plate top is a quarter of the tallest emitted part's height
  above its centre of mass, since a plate meeting a box below its centre of mass tips it).
  In the headless model an advancing plate pushes every part whose footprint overlaps it, so the
  part's back face rides on the plate face (one tick behind the plate, swept, never pulled back); a
  part pushed past a belt edge falls onto whatever is below.
- **`box_emitter`**: origin is the spawn point (part center). Spawns at once on enable, then every
  `interval_s ± jitter_s`; unbound `enable` means always enabled; disabling resets the timer; a due
  part waits (held, counted in `waiting_ticks`) until its spawn volume is clear of other parts, and
  the next interval starts from that spawn (PLAN 2.0); spawns over the 200-part cap are skipped and
  counted.
- **`part_remover`**: origin is the center of its volume. Removes a part when the part's *center* is
  inside the (rotatable) volume, and counts removals by part name.
- **`pushbutton`**, **`selector_switch`**: origin is the center of the button or switch face. Viewer actions are queued and
  applied on the next step, so PLC-visible changes land on step boundaries. Momentary pushbuttons
  pulse 300 ms; a re-press restarts the pulse.
- **`pilot_light`**: origin is the lens center; lit while `on` is true.

Every component implements `fault_inject(kind)` from Milestone 2 so instructors can break sensors
(stuck on, stuck off, noisy) during training.

From 2.16 onward this table lists **primitives** only. Equipment that can be expressed as a composite
of primitives (a gated conveyor, a two-station indexer, a filling head) is declared in a scene's
`composites:` block or in `scenes/_library/*.yaml`, not added here. Add a primitive only when no
composite can produce the behavior.

---

## 6. Protocol Design

### 6.1 Modbus TCP register map (generated, deterministic, locked)
- A new scene sorts tags by kind then name and allocates:
  - DI → discrete inputs (FC02) from 0
  - DO → coils (FC01/05/15) from 0
  - AI → input registers (FC04) from 0, float32 as two registers big-endian word order (configurable)
  - AO → holding registers (FC03/06/16) from 0, same encoding
- Analog encoding is per scene: `plc.analog_encoding: float32 | int16_scaled`. Default `float32`
  (two registers, high word first). `int16_scaled` uses one register, 0–32767 for 0–100 % of the tag's
  `range`, the same raw-count convention as a 1756-IF8 4–20 mA channel. OpenPLC v3 does no float
  decoding on its Slave Devices path, so OpenPLC examples use `int16_scaled` (ADR-0003).
- Write the map into the exported CSV (`modbus_address` column) and print it at startup.
- Listen address and port come from `openfactory.toml` `[modbus]` (default `127.0.0.1:5020`), overridden
  by the scene's optional `plc.bind` / `plc.port`, then by `--bind` / `--port` on the command line.
- Address stability (ADR-0004): the first run in an analog encoding writes
  `scenes/<name>.<encoding>.modbus.yaml`, a lockfile mapping every tag to its address, committed with
  the scene. A float32 and an int16_scaled lockfile coexist; the encoding in use is the scene's
  `plc.analog_encoding` unless `openfactory run` / `export` is given `--analog-encoding` (CLI flag >
  scene), and only that encoding's lockfile is read or written. A new encoding's lockfile copies the
  discrete-input and coil tables of an existing one, so digital addresses match across encodings
  (ADR-0004 decision 8). Later runs keep those addresses and append new
  tags after the highest used address in each table, so adding a tag never renumbers existing ones.
  A removed tag's line stays (marked `# not in scene`) so its address is not reused. A binding may
  pin an address, `run: {tag: CONV_01_RUN_DO, address: 12}`, which overrides the lockfile and
  allocation. The server, the startup printout, and the CSV export share one resolver
  (`scene_register_map`).
- **SIM_HEARTBEAT_DI (ruled 2026-10-30).** Every scene has one more DI than it declares: the
  simulator's heartbeat, a 1 Hz square wave in simulated time (false for 0.5 s, true for 0.5 s,
  so it changes every 25 ticks at 50 Hz), written at the end of every tick from the tick count
  (`core/heartbeat.py`). It is allocated after the scene's own DIs, so in an existing lockfile it
  takes the next free discrete input and never renumbers a tag (ADR-0004 decision 10). It is a
  row in the I/O CSV (blank `component`) and is served by every protocol. A PLC that sees it stop
  changing knows the simulation is down, stalled, or no longer answering; a watchdog of a few
  seconds on its edges is enough. The name is reserved.

### 6.2 Logix (pycomm3) mapping
- Scene `plc.protocol: logix`, `plc.role: client`, and `plc.connection: bench_l33er`. The name is
  resolved from `[connections.bench_l33er]` in `openfactory.toml` (or the git-ignored
  `openfactory.local.toml`), which holds the CIP path; a scene never contains an IP address:
  ```yaml
  # scenes/x.yaml
  plc: {protocol: logix, role: client, connection: bench_l33er}
  ```
  ```toml
  # openfactory.local.toml
  [connections.bench_l33er]
  protocol = "logix"
  path = "192.0.2.10/1/0"   # IP / backplane port / slot (documentation address range)
  ```
- Tag name in the PLC equals the OpenFactory tag name by default. Optional `plc_tag:` per binding for
  mapping into a UDT (`Sim.CONV_01.RUN`).
- Poll DO/AO with one `read()` of all tags every 50 ms; write DI/AI with one batched `write()` on change.
- The PLC watches `SIM_HEARTBEAT_DI` (§6.1) to detect simulator loss; on this path it is written
  to the PLC like any other DI.

### 6.3 OPC UA server (asyncua)
- Namespace `urn:openfactory:sim`. Folder per component, variable per tag, writable for DO/AO.
- Ignition connects with its OPC UA client, tags land under `[OpenFactory]`. This is also the path for
  FactoryTalk Linx Gateway and Kepware.
- Built in 2.4 (`protocols/opcua.py`, `docs/ignition.md`): a variable's NodeId is its tag name as
  a string, so clients and Ignition item paths (`nsu=urn:openfactory:sim;s=TAG`) survive a
  restart; DI/AI are read-only; `run --opcua` or `plc.protocol: opcua`.

### 6.4 Security defaults
- Bind to loopback unless `--bind` given. No authentication on Modbus (protocol has none); OPC UA
  anonymous by default with a documented switch to username/password. Say plainly in docs: this is a lab
  tool, keep it off plant networks.
- Only an operator action opens the network: the `--bind` CLI flag, or a `bind` the operator wrote in
  their own `openfactory.toml` / `openfactory.local.toml`. A scene's `plc.bind` override is restricted
  to loopback (`127.0.0.0/8`, `::1`) and fails at load otherwise, so opening a shared scene file never
  exposes a server.
- **Fault injection is guarded off loopback (ruled 2026-10-27).** `/api/fault` (PLAN 2.6) changes
  what a PLC sees, so with the viewer bound to loopback it is open, and bound beyond loopback every
  request (GET, POST, DELETE) needs the token given by `--fault-token` or `[server] fault_token`
  (in the git-ignored `openfactory.local.toml`), in the `X-OpenFactory-Fault-Token` header:
  401 without it or with a wrong one, compared in constant time, never logged. With no token
  configured the fault API is off there (403) and `run` logs a one-line notice. The viewer's
  fault menu asks for the token once per browser tab. Built in `server/app.py`
  (`tests/unit/test_fault_access.py`).

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
- [x] 1.4 Components: `belt_conveyor`, `photo_eye`, `pusher`, `box_emitter`, `part_remover`,
      `pushbutton`, `pilot_light`, `selector_switch`, each with unit tests
      *Why:* this is the minimum set for a classic sorter. Keep behavior simple and documented.
      *Done when:* each `step()` is covered; `pusher` limit switches verified across a full cycle.
- [x] 1.5 `core/kinematics.py`: headless part motion (position along conveyor axis, transfer between
      connected conveyors, photo-eye intersection, remover). No Rapier.
      *Why:* CI and PLC logic tests must run without a browser.
      *Done when:* `tests/unit/test_kinematics.py` moves a part down two chained conveyors and trips a
      photo eye at the expected time ± 1 tick.
      *Parity note (2026-10-25):* as first built (commit `0bf7ff6`) parts took the belt speed instantly
      and tripped at tick 257 against 257 on paper. Rapier accelerates a part by friction (μ·g), a lag
      of about 1.5 ticks at μ 0.8 and 0.5 m/s and worse at lower μ, which would eat most of 1.12's
      2-tick parity budget. `kinematics.py` now caps part acceleration at `scene.friction` × 9.81 m/s²
      in both directions, so parity is tested at the scene's own friction. Re-run: tick 259 against
      259 on paper (landing + ⌈(2.55 m + v²/2μg) / (v·dt)⌉ + debounce). 1.12's parity replay then
      showed Rapier parts consistently 1–2 ticks *ahead*: a landing impact lets friction change the
      belt-direction speed by μ·|vy| at once. The headless model now does the same on landing (tick
      258 against 258 on paper, v0 = μ·g·dt for a part placed at rest), and parity tightened from
      worst 2 to worst 1 tick at μ 0.8 and from 3 to 2 ticks at μ 0.3.
- [x] 1.6 `core/scheduler.py`: fixed 50 Hz with accumulator; `--as-fast-as-possible`
      *Done when:* 10 simulated seconds complete in < 0.5 s wall time in fast mode; realtime drift < 1%.
- [x] 1.7 `protocols/modbus.py`: pymodbus 3.16.x async server bound to IOTable, allocation per §6.1.
      Bind through `SimDevice(action=hook)`; `ModbusSlaveContext` no longer exists and the legacy
      datablocks do not give live reads. Always pass `address=("127.0.0.1", port)` explicitly. Hook must
      be idempotent (FC05 can invoke it twice). See `docs/research/libraries.md` "pymodbus 3.x datastore
      binding".
      *Done when:* `tests/integration/test_modbus_roundtrip.py` uses pymodbus client to write a coil and
      sees `CONV_01_RUN_DO` change; reads a DI after the sim trips it.
      *Note (2026-10-25):* allocation sorts by kind, then tag name, so addresses are stable across runs
      (CLAUDE.md), but adding a tag that sorts earlier renumbers the later tags in its table. §6.1's
      "adding a tag never renumbers existing ones" and its pinned `address:` override cannot both hold
      today: an `io` binding is a plain tag string, so there is nowhere to pin. *Resolved by ADR-0004:*
      a per-scene address lockfile and a `{tag, address}` binding form.
- [x] 1.8 `export/io_list_csv.py`: columns `tag,kind,dtype,description,component,modbus_address`
      *Done when:* output validates with HIS Controls `/io-validate` skill.
      *Note (2026-10-25):* `/io-validate` requires `io_type` (not `kind`) and an `address` column, so the
      CSV uses `io_type` and adds `address`, left blank because a simulated point has no physical PLC
      I/O address. It also adds `modbus_encoding`, `units`, `range_min`, and `range_max` so the
      register map can be decoded from the CSV alone. Final columns: `tag,io_type,dtype,description,
      component,address,modbus_address,modbus_encoding,units,range_min,range_max`.
- [x] 1.9 `server/app.py`: FastAPI, `/api/scene`, `/ws/state`, `/ws/events`, static `web/dist`
      *Done when:* `curl /api/scene` returns resolved geometry; a websocket client receives ≥ 15 frames/s.
- [x] 1.10 CLI `openfactory run|validate|export` via `typer`
      *Done when:* `openfactory --help` lists the three; `validate` exits 1 on a bad scene.
      *Note (2026-10-25):* as built, `run` prints the Modbus register map at the default INFO log
      level (`-q` hides it), writes or updates the ADR-0004 lockfile, and ends with a run summary.
      `--as-fast-as-possible` needs `--duration` and implies `--headless`. `--bind` applies to every
      server; `--port` is Modbus and `--web-port` the viewer. `--force TAG=VALUE` sets a DO/AO at
      startup as if the PLC wrote it, for demos and the viewer tasks without a PLC.
- [x] 1.11 Viewer: load scene JSON, build primitive meshes per component type, orbit camera, dark ISA-101
      background, status colors (running green `#22C55E`, stopped grey `#9CA3AF`, alarm red `#EF4444`)
      *Done when:* Playwright screenshot of the sorter scene shows two conveyors, a pusher, a photo eye.
      *Note (2026-10-25):* builders live in `web/src/components/` (one per M1 type plus a grey
      fallback for unknown types); id labels via `CSS2DRenderer`; clicking a pushbutton or selector
      sends `press` / `select` on `/ws/events`. Parts are drawn at the core's kinematic positions
      (eased between 20 Hz frames) until 1.12 gives them Rapier bodies. A bound but unwritten speed
      AO reads 0 %; since housekeeping 2 (2026-10-25) the sorter's speed bindings carry
      `initial: 100`, so a demo needs only the run coil.
- [x] 1.12 Viewer physics: Rapier world, parts as dynamic boxes, conveyor top as
      `kinematicVelocityBased` body with `setLinvel` **and `setTranslation(home)` reset every step**
      (otherwise the belt drifts; contact surface velocity is Rust-only), pusher as
      `kinematicPositionBased`; sensor beam as a sensor collider with collision events; events sent
      upstream. Draw the belt mesh at `home`, not at the body translation. Part ramp is friction-limited
      (μ·g); `kinematics.py` models the same ramp from `scene.friction`, and parity is tested at it.
      *Done when:* with PLC coil forced on, a box travels the belt and a photo-eye event arrives in the
      core; parity test against `kinematics.py` within tolerance. Tolerance (amended 2026-10-25):
      `max(2 ticks, 25 % of the computed ramp time)`, where the ramp time is `v / (μ·g)` for the
      fastest belt, in ticks, unrounded; `tools/parity_trace.py` writes `ramp_ticks` and
      `tolerance_ticks` into each trace and both test sides read it. Sorter at μ 0.8: ramp 3.19
      ticks, tolerance 2; at μ 0.3: ramp 8.49, tolerance 2.12; at μ 0.1 it would be 6.37.
      *Note (2026-10-25):* `web/src/physics.ts` is DOM-free, so the viewer and a node replay run the
      same code. Parity (`tests/integration/test_kinematic_parity.py`): `tools/parity_trace.py`
      records the core's per-tick inputs (belt speeds, plate strokes, spawns, removals) and its
      raw photo-eye edges and belt checkpoints for 20 s of the sorter, committed as
      `web/src/fixtures/parity-*.json` with a freshness check; `physics.parity.test.ts` replays them
      through Rapier. Worst |Δ| 1 tick at the scene's μ 0.8, 2 ticks at μ 0.3. Getting there took
      one model change in `kinematics.py`, landing impact friction (see the 1.5 parity note).
      Live check (housekeeping 5, informational, not a gate): `uv run python tools/parity_trace.py
      --live` runs the built viewer against the core and prints browser-vs-core photo-eye ticks
      from `GET /api/parity`. First run under SwiftShader: the browser stepped 432 physics ticks
      while the core advanced 718, so live deltas reached 46 ticks and some browser edges had no
      core match (the viewer steps on wall-clock time, capped at 5 steps per rendered frame).
      Sensor authority is settled in §3.3: The headless model is authoritative for PLC tags through Milestone 2; browser sensor events are parity-only.
- [x] 1.13 `tools/screenshot.py`: launches server + Playwright, waits N sim seconds, saves PNG
      *Why:* this is how the AI verifies visual work. Build it early and use it every time.
      *Done when (added 2026-10-25, the task had none):* `uv run python tools/screenshot.py
      scenes/sorting-by-height.yaml --seconds 3 --force CONV_01_RUN_DO=1` exits 0 with a PNG that
      passes the tool's own non-blank check (≥ 2 % non-background pixels, ≤ 90 % near-black) and,
      looked at, shows every scene component; `tests/integration/test_screenshot.py` runs it in CI.
      The tool waits on the viewer's `window.__openfactory` ready flag and sim time, never a sleep.
      Built before 1.11, so the viewer gained the minimum it needs: scene fetch, one neutral grey
      placeholder box per component, a HUD, and the `/ws/state` stream.
- [x] 1.14 `examples/openplc/sorting-by-height.st` + `docs/guides/openplc-quickstart.md` with the Slave
      Devices settings (IP, port 5020, start addresses, sizes). Use `analog_encoding: int16_scaled` for
      this scene (see §6.1); OpenPLC delivers raw 16-bit words with no float decoding. OpenPLC polls at
      100 ms by default, so the example logic must not rely on photo-eye pulses shorter than ~200 ms;
      size emitter spacing and belt speed accordingly. **Container:** there is no official OpenPLC image
      on Docker Hub (only personal uploads; do not use them). Build from the upstream Dockerfile at the
      pinned commit: `git clone https://github.com/thiagoralves/OpenPLC_v3 && git checkout b5d4135 &&
      docker build -t openfactory/openplc:b5d4135 .` Image was pre-built on the dev host 2026-10-25.
      Run with `docker run -d --name openplc -p 8080:8080 -p 502:502 openfactory/openplc:b5d4135`; the
      web UI is on 8080 (default login openplc/openplc), and Slave Devices must point at the WSL host IP
      (`hostname -I`, a numeric address; tested 2026-10-25) on port 5020, not 127.0.0.1, since the
      container has its own loopback. Run only
      this one container; this host freezes under heavy container fleets.
      *Done when:* a fresh OpenPLC install follows the guide and sorts boxes. Record a 30 s screen
      capture for the README.
      *Note (2026-10-25):* done with a fresh container of the pre-built image, configured through
      its web UI by `tools/openplc_configure.py` (Playwright). The simulator ran with
      `--bind 0.0.0.0 --analog-encoding int16_scaled` against the new
      `scenes/sorting-by-height.int16_scaled.modbus.yaml`. A 60 s headless run with OpenPLC in
      control and no `--force`: spawned 20, REM_01 {short: 9}, REM_02 {tall: 9}, lost {}.
      Addresses tried from inside the container: `host.docker.internal` (resolves to
      192.168.65.254) connects at TCP level, but OpenPLC's Slave Device accepts only a numeric IP
      (runtime log: "Invalid argument"); the WSL address from `hostname -I` works and is what the
      guide uses. The first run diverted one short box: OpenPLC's input image held stale run
      feedback on connect, the emitter enable chattered, and two boxes spawned on top of each
      other. The program now waits for 1 s of steady run feedback before enabling the emitter.
      **Screen capture deferred, then done in 2.0a:** a 30 s Playwright recording was made and its
      stills checked. But under headless SwiftShader the viewer's Rapier steps ran at about 60 % of
      core ticks (housekeeping 5 finding), so the video showed the pusher stroking ahead of a
      lagging box while the core sorted correctly. It was not committed. Task 2.0a locked the
      viewer to the core tick and recorded `docs/media/sorting-by-height-openplc.webm`.
- [x] 1.15 CI: GitHub Actions running `pytest`, `ruff`, `npm run build`, and the headless parity test
      *Note (2026-10-25):* `.github/workflows/ci.yml` sets up uv (Python 3.11) and Node 22, runs
      `uv sync --frozen --all-extras` and `playwright install --with-deps chromium`, then `tools/ci_local.sh`,
      which holds every step so a local run matches: ruff check and format, schema check, `openfactory
      validate scenes`, `npm ci` / `npm run build` / `npm test`, pytest, `tools/screenshot.py` with its
      non-blank check, `tools/parity_trace.py --check`, and `git diff --exit-code`. Under `CI=true`
      the screenshot and parity tests fail instead of skipping when `web/dist`, node, or Chromium is
      missing (`tests/conftest.py`, `prerequisite`). Not yet run on GitHub (no Actions here); the
      same script passed locally in an `env -i` shell. The live parity check stays out of CI.

### Milestone 2 — Rockwell and Ignition paths, discrete library complete (target 3–4 weeks)
- [x] 2.0a Tick-locked viewer physics: the viewer steps Rapier on the simulator's tick, not on
      wall-clock time. Each `/ws/state` frame carries the core tick; on each frame the viewer steps
      physics exactly `tick - last_tick` times, at most 10 per frame (extra ticks are dropped and
      counted), and never steps between frames. Rendering still runs every display frame and eases
      part meshes toward their latest physics pose.
      *Why:* the Milestone 1 viewer stepped on wall-clock time with at most 5 steps per rendered
      frame, so under software WebGL it ran about 60 % of core ticks (housekeeping 5 live check:
      432 browser steps for 718 core ticks, deltas up to 46 ticks). Its parts lagged the core's,
      the pusher appeared to miss boxes the core sorted correctly, and the 1.14 screen capture had
      to be deferred.
      *Done when:* `tools/parity_trace.py --live` shows worst-case browser-vs-core photo-eye drift
      ≤ the replay tolerance (2 ticks at the scene's μ 0.8), with no dropped ticks; then the 30 s
      README capture from 1.14 is recorded with OpenPLC in control, stills at 5 s intervals are
      looked at, the pusher is seen hitting the tall boxes, and the video is committed under
      `docs/media/`.
      *Note (2026-10-26):* `web/src/ticksync.ts` (`planSteps`, tested in `ticksync.test.ts`) and
      `stepToFrame` in `main.ts`. A tick moves parts with the previous frame's inputs, as in the
      replay, and pusher strokes are interpolated across a frame's steps. Tick-locking alone gave
      a 3-tick worst case, because a part first seen in a frame was created at rest while the
      core's was already falling. Frames now carry each part's world `velocity`, and the Rapier
      body starts with it. Live, 30 s: 1405 browser steps for 1405 core ticks, 0 dropped, worst
      |Δ| 2 ticks; 60 s: 2937 for 2937, 0 dropped, worst |Δ| 1, median 0.5.
      Capture: `docs/media/sorting-by-height-openplc.webm` (poster `.png`).
      *Spawn tick (2026-10-26):* frames now carry each part's `spawn_tick` and `spawn_position`;
      `spawnStep` (`ticksync.ts`) adds a new part before the step for `spawn_tick + 1`, as the
      replay does, instead of after the frame's steps at its current pose. Live: 30 s, 1238
      steps for 1238 ticks, worst |Δ| 1, median 1; 60 s, 2778 for 2778, worst |Δ| 1, median 1.
      The renderer's pixel ratio is capped at 2, and a reconnect refetches `/api/scene` (§3.3).
- [x] 2.0 Headless part-to-part queueing along the flow axis in `core/kinematics.py`: a carried part
      stops at the back face of the part ahead of it on the same belt (and across a connection
      handoff) instead of overlapping it, so parts accumulate behind a stopped belt or a raised
      stopper and release in order. Parity with Rapier's box-on-box contacts as for 1.12.
      *Why:* the Milestone 1 model lets parts overlap on a stopped belt (kinematics docstring,
      "Limits"), so a photo eye sees one long block instead of a queue and a buffer cannot count.
      Required before the `buffer-and-release` scene (2.7) and the `stopper` component (2.5).
      *Done when:* five parts sent onto a stopped belt queue nose to tail with gaps ≥ 0 m, a photo
      eye at the queue sees each gap when the belt restarts, and the parity test covers a queue.
      *Note (2026-10-26):* `core/kinematics.py` moves leaders first and stops each carried part at
      the back face of the part ahead (same belt, or across a connection), lateral and height
      overlap required. `box_emitter` now waits for a clear spawn volume, holding the part it
      already drew (the RNG sequence is unchanged), and the 1.14 chattering-enable stack is gone.
      While a part straddles a connection seam it now rests on both belts by overlap share
      (Coulomb friction from each); this was needed for the queue's release onto a faster belt to
      match Rapier (worst 8 ticks before, 2 after). Headless (`tests/unit/test_queueing.py`): five
      boxes from a running 0.5 m/s belt into a stopped 1.0 m/s belt queue with gaps
      [0, 0, 0, 0] m (leader on the stopped belt's infeed); on restart PE_B sees five separate
      blocks and parts leave first in, first out. Parity (`parity-queue`, scene
      `tests/scenes/queue-parity.yaml`): five boxes *placed* nose to tail on a stopped belt,
      both belts started at tick 50, 30 comparisons, worst |Δ| 2 ticks (tolerance 2). The sorter
      traces are byte-identical. **Known difference, accumulation phase:** in Rapier a queue on a
      running belt presses on the part it waits behind and a stopped belt brakes a part as soon as
      its nose overhangs. Measured: with one or two followers the lead follower stops with its
      nose 4 mm onto the stopped belt and never touches the box there; with four followers the
      line pressure shoves that box 0.26 m along the stopped belt. The headless model has neither
      effect, which is why parity covers a placed queue's release, not accumulation against a
      stopped belt. A raised `stopper` (2.5) gives both models the same hard stop.
- [x] 2.1 Research spike: Logix Emulate connectivity, options (a)–(d) in §2.4. Write
      `docs/research/logix-emulate.md` and ADR-0002.
      *Research written (2026-11-10), not ticked (ruled: document only, ADR-0002 as a
      proposal):* `docs/research/logix-emulate.md` covers (a)-(d) and (e) FactoryTalk Logix Echo
      from public sources, each with what pycomm3 can and cannot reach, what the owner installs
      and a first live test. Findings: Emulate (5570 class) ends at Logix Designer v37, Echo
      covers v33 and later; Emulate is reached through FT Linx's EtherNet/IP encapsulation
      listener and Echo needs that listener off (both use 44818), so they need separate hosts;
      Echo accepts incoming CIP Class 3 (release notes), but no public source shows pycomm3
      against it. `docs/adr/ADR-0002-logix-emulate-path.md` proposes: (d) stays the reference,
      (e) Echo the emulated path for v33+, (a) Emulate the legacy path, (b) Linx Gateway OPC UA
      the unbuilt fallback, (c) not pursued; live tests in the order (d), (e), (a). Waits for
      the owner's ruling and the live tests.
      *Ruled 2026-11-10:* ADR-0002 accepted as proposed (decisions 1 to 5; live tests in the
      order (d), Echo, (a)), its status set to Accepted, and 2.1 ticked. The live tests wait on
      the owner (2.2, open questions).
- [ ] 2.2 `protocols/logix.py` with pycomm3, batched read/write, heartbeat, reconnect with backoff
      *Done when:* bench CompactLogix runs the sorter with tags generated by `/io-to-l5x`, and pulling
      the Ethernet cable for 10 s then reconnecting resumes control with no restart of either side.
      *Open for the owner (ADR-0002, 2026-11-10):* (1) is the bench CompactLogix 5380 available
      for this task's Done-when; (2) which host runs FactoryTalk Logix Echo (and which separate
      host or VM runs Logix Emulate, if a v35-or-earlier demo is wanted)?
      *Built (2026-11-10), not ticked (the Done-when is the bench run):*
      `src/openfactory/protocols/logix.py`, `LogixClient`: every 50 ms one batched `write()` of
      the DI/AI tags that changed (all of them after each connect, the heartbeat included), then
      one batched `read()` of every DO/AO into the I/O table as the PLC; pycomm3's blocking
      calls in a worker thread. `bool`/`float32`/`int16` map to `BOOL`/`REAL`/`INT`; on connect
      each scene tag is checked against the controller's tag list, and a missing or mismatched
      tag (or one a read or write rejects) is logged once and left out while the rest run. A
      communication error closes the driver, logs one line per outage and reconnects after
      0.5, 1, 2, 4 then 5 s, with no restart; the I/O table keeps the PLC's last outputs and
      the PLC sees the heartbeat freeze. A run does not wait for an unreachable controller.
      `openfactory run SCENE --logix NAME` (or the scene's `plc: {protocol: logix, role: client,
      connection: NAME}`) takes the CIP path from `[connections.NAME]` in `openfactory.toml` /
      `openfactory.local.toml`, replaces the Modbus server, and refuses `--script` with it.
      `docs/logix.md`: setup, and the path for the bench 5380, Echo and Emulate.
      `tests/unit/test_logix.py` (11) against a fake controller (a stand-in for `LogixDriver`
      with a droppable link): batching (one write of only the changed inputs, one read), BOOL
      and REAL both ways, the heartbeat written on change, a missing and a mismatched tag, a
      tag deleted online, a 10 s drop survived (one log line, last outputs held, inputs
      re-sent, one reconnect, the same task), an unreachable controller not holding up the run,
      and the CLI's connection errors. Writing them found one bug: pycomm3 returns a single
      result for a single tag, and its `Tag` is a NamedTuple, so "is it a tuple" split it into
      fields. *What the fake cannot prove:* that pycomm3 reaches a real 5380, Echo or Emulate
      with these paths; real timing of a batched read against 50 ms; that a pulled cable
      reaches pycomm3 as a socket timeout after 5 s, not at once as in the fake; how a
      controller answers a write to a Read Only tag or one a rung writes; how long it keeps a
      dead connection. `plc_tag:` per binding (§6.2, a UDT member) is not built.
      *Owner's answers (2026-11-10):* (1) the bench 5380 is **not available yet**, so 2.2 stays
      open; (2) Echo runs on **a VM or another PC, separate from this one** (the live-test
      runbook is `docs/logix-echo-test.md`). `run --logix NAME` accepted.
      *Link loss (ruled 2026-11-10, done):* selectable per connection, `on_link_loss = "off"`
      (default) or `"hold"`, with `link_loss_timeout_s` (default 1.0). With "off", once no
      exchange has succeeded for the timeout, every DO/AO goes to its off state (false, 0.0, 0)
      in one atomic write, logged once per outage; the first read after reconnecting restores
      the controller's values. A watchdog task measures from the last good exchange, so a
      cycle stuck in pycomm3's 5 s socket timeout trips it on time. "hold" is the earlier
      behaviour. `docs/logix.md` explains both and why "off" is the default (real output
      modules go to a fault state). Tests: the 10 s drop with each setting (hold keeps 0.6 and
      True; off goes to 0.0 and False after at least 1 s, both tags in the one write, logged
      once, restored after reconnect), the timeout on an injected clock (0.9 s no, 1.1 s yes,
      once per outage), and the connection entry's defaults and a bad value.
      *For ruling:* should the Modbus server get the same setting (PLC outputs off when the
      PLC has not polled or written for a timeout)? Today a PLC that stops polling leaves its
      Modbus outputs where they were; that path is unchanged.
- [x] 2.3 `export/l5x_stub.py`: controller tags only (BOOL/REAL) for a scene, via existing
      `/io-to-l5x` where possible. Writes to the consuming project's `03-plc/generated/`, never here.
      *Done when (ruled 2026-10-30):* controller-scoped BOOL and REAL tags with descriptions from
      the scene, SoftwareRevision 35.00, ContainsContext true, tag names in AREA_EQUIP_NNN form,
      the XML checked well-formed in a test; the sorter exports an L5X whose tag count equals the
      I/O list row count plus SIM_HEARTBEAT.
      *Done (2026-10-30):* `openfactory export SCENE --format l5x [--controller NAME] [-o FILE]`
      (stdout by default; nothing is written into this repo). One `Base` tag per I/O list row,
      in the same order, so the CSV and the L5X always name the same tags. The CSV already
      carries `SIM_HEARTBEAT_DI` as a row (§6.1), so "rows plus SIM_HEARTBEAT" is the CSV row
      count: the sorter's 11 bound tags plus the heartbeat, 12 rows and 12 L5X tags. `bool` is
      BOOL (radix Decimal), `float32` REAL (radix Float), whatever the Modbus encoding; analog
      descriptions end with the range, `[0 to 100 %]`. Tag names are the scene's own (the Logix
      path and the example programs use them), checked against `^[A-Z][A-Z0-9_]{0,39}$` and for
      duplicates; scene tags follow the project's `EQUIP_NN_SUFFIX` form and carry no area
      prefix. The L5X format reference is Rockwell publication 1756-RM014 (Logix 5000
      Controllers Import/Export Reference Manual, March 2022; its predecessor was 1756-RM084).
      No `ExportDate` and no `<Data>`, so the file is byte-identical per scene for 2.14 and the
      tags take Studio 5000's defaults; whether an import accepts both omissions is part of the
      Studio 5000 import test below. The text is parsed back before it
      is returned. `tests/unit/test_l5x_stub.py`: "sorter: 11 bound tags + SIM_HEARTBEAT_DI = 12
      I/O list rows; L5X tags 12", names equal to the CSV's in order, root attributes, types,
      radixes and descriptions checked, every shipped scene exports unique valid names twice
      byte-identical. The `l5x-analysis` MCP parser read the sorter file as controller
      `OpenFactory`, context true, revision 35.00, BOOL/REAL controller-scope tags with their
      descriptions. *Not done:* a Studio 5000 import (no Studio 5000 in this session); stubs are
      for training and demos and need PE review before any client use. (The 2.14 ruling of the
      same day puts one generated L5X per kit under `kits/`, which supersedes "never here" for
      kits only.)
      *Prefix (ruled 2026-10-30):* scene names without a prefix stay the default;
      `--prefix AREA1` prepends `AREA1_` to every tag name and `AREA1 — ` to every description
      and changes nothing else. Done when: the sorter with `--prefix SIM` exports `SIM_PE_01_DI`
      and the file is otherwise unchanged (`test_prefix_changes_names_and_descriptions_and_
      nothing_else`: stripping the prefix gives the unprefixed file byte for byte).
- [x] 2.4 `protocols/opcua.py` with asyncua; `export/ignition_tags.py` producing 8.3 tag JSON
      *Done when (first written):* Ignition 8.3 gateway shows Good quality on all tags; a
      Perspective view from `/perspective-view` starts the conveyor.
      *Done when (ruled 2026-10-30, replaces the above for this task):* an asyncua client reads a
      sensor and writes a belt run bit, with reconnect verified by restarting the server under a
      connected client; the Ignition JSON validates against the structure in docs (folder per
      area, UDT instance per component).
      *Done (2026-10-30):* `OpcUaServer` (§6.3, `docs/ignition.md`): namespace
      `urn:openfactory:sim`, scene folder, a folder per component, a variable per tag with the
      tag name as its string NodeId; DI/AI read-only, DO/AO writable, a client write lands as the
      PLC side; I/O table changes reach the variables through `on_change` and
      `call_soon_threadsafe`, never under the step lock. `run --opcua [--opcua-port]` serves it
      beside the scene's protocol; `plc.protocol: opcua` serves it as the PLC server.
      `export --format ignition [--area] [--opc-server | --device]`: `{"version": "8.3.0"}`,
      `_types_` first (a UdtType `OF_<Type>` per component type, members per bound port with a
      `Tag_<Member>` parameter), then one area folder with a UdtInstance per component that has
      I/O and `SIM_HEARTBEAT`. Results: `tests/integration/test_opcua.py`, the sorter: wrote
      CONV_01_RUN_DO, CONV_02_RUN_DO, EMT_01_EN_DO = True; read CONV_01_RUN_DI True and
      PE_01_DI True after 5.5 s; a write to PE_01_DI refused; server restarted under the
      connected client: reconnected 1.5 s after it went down, same NodeIds, CONV_01_RUN_DI read
      True, CONV_01_RUN_DO written False, the sim ran 98 ticks meanwhile. `run --opcua` from the
      CLI: all 12 item paths of the sorter's Ignition export resolve on the live server to nodes
      of the exported data type, and the heartbeat changes. `tests/unit/test_ignition_tags.py`:
      all six shipped scenes validate against the documented structure, every I/O list tag
      exactly once, deterministic. *Decisions:* a scene has no areas, so one area folder per
      scene (`--area`, default the scene name in capitals); UDT names carry an `OF_` prefix;
      history on AI members only; no alarms (no rationalized setpoints). The root wrapper
      follows the HIS Ignition conventions; the example export in the Ignition 8.3 manual
      ("Exporting and Importing Tags", https://docs.inductiveautomation.com/docs/8.3/platform/tags/exporting-and-importing-tags)
      has a single `tags` root key and no `version`, so check the import on a gateway.
      *Not done:* the first Done-when (an Ignition 8.3 gateway showing Good quality, a
      Perspective view starting the conveyor; no gateway in this session); the comms faults of
      2.13 apply to Modbus only.
      *Ruled 2026-10-30:* the JSON choices stand provisionally. When Docker is back: one
      `inductiveautomation/ignition:8.3` container (one only), import the sorter JSON through
      the gateway, `run --opcua`, confirm Good quality on all 12 tags, report whether the
      version wrapper and the parameter form import cleanly, and fix the exporter to what the
      gateway accepts. That completes the first Done-when except the Perspective view, which
      moves to 2.14's skill outputs.
- [x] 2.5 Components: `roller_conveyor`, `stopper`, `turntable`, `diverter_arm`, `pick_and_place`,
      `distance_sensor` (split 2026-10-26: the rest of the original list moved to 2.5b).
      Each component ships headless kinematics, a viewer device, a Modbus binding, a unit test, and
      an entry in the component reference.
      *Distance sensor (amended 2026-10-26):* range along its beam to the nearest part as an AI, so
      2.17's `window` transform is tested end to end (a scene binds a `_DI` with `window:` to it).
      *Parity test (amended 2026-10-26):* queue formation. Five boxes pile against a raised
      `stopper` on a running belt, then the stopper lowers and they release; the browser physics
      must match the headless queue within the 2.0 tolerance (max(2 ticks, 25 % of the ramp)).
      This replaces the placed-queue workaround from 2.0, because a stopper gives both models the
      same hard stop.
      *Rulings (2026-10-26):* distance in metres, 0 to `beam_length`, the range set per sensor, and
      the int16_scaled lockfile records the range each analog tag is scaled against, so a changed
      `beam_length` fails the lockfile check instead of silently rescaling. A second port,
      `switching`, carries the same value for a `{tag: X_DI, window: [lo, hi]}` binding, and the
      I/O CSV shows the window on that row. Pick-and-place axes are `traverse` and `lower`. The
      turntable turns a fixed ±90°; `turn` held true drives to `turn_deg` and released returns to
      0, like the pusher's `extend`; `at_0` and `at_90` are both false while moving; a connection
      to or from the deck hands parts across only while that end is aligned. The stopper's
      `raise`/`raised`/`lowered`; a part already over a rising blade passes, the next one stops; the
      blade height is sized from the parts when unset and tuned against the parity trace (if the
      trace cannot agree within the 2.0 tolerance, report the numbers; do not widen it). Parts held
      by a gripper or on a turning deck follow the core's position in the viewer (§3.3). `height`
      on `stopper` and `diverter_arm` is the raised blade top / arm top above the belt, so W206
      applies to them. Component reference: `docs/components.md`, with a test that every
      registered type has an entry. Window scene: `tests/scenes/distance-window.yaml`.
      *Diverter parity (2026-10-26, informational):* trace `parity-diverter` (not a gate, as no
      ruling asks for one): the box the arm diverts arrives up to 10 ticks late in Rapier (12
      comparisons; the box that passes agrees within 1). In Rapier the arm's corner contact turns
      the box about 23 degrees about the vertical, and it slides along the arm a little slower than
      the headless fence's v·cos(swing); the headless model keeps boxes square to the world axes.
      Locking part yaw in Rapier only brought it to 8 ticks and hides real behavior, so it was not
      done. Belt colliders now have 5 mm rounded edges: a part driven sideways across a level seam
      caught on the neighbor belt's top edge (stuck for 0.9 s with yaw locked).
      *Done (2026-10-26):* one commit per component, each with headless kinematics, a viewer
      device (screenshots looked at), a Modbus round trip (`tests/integration/
      test_components_modbus.py`), and unit tests; `docs/components.md` covers all 14 types and
      `tests/unit/test_component_reference.py` checks it against the registry. Queue-formation
      parity (`parity-stopper`): 36 comparisons, worst |Δ| 2 ticks (tolerance 2), with the blade
      height sized to the tallest part's centre of mass; no tipping (Rapier tilt at most 0.3
      degrees, 0.4 with 0.4 m boxes). Window end to end (`tests/scenes/distance-window.yaml`,
      int16_scaled over Modbus): the windowed DI rose for each of 5 tall boxes and for nothing
      else, and every AI raw value was the distance scaled over 0 to 0.8 m. Also built for these:
      `core/sizing.py` (load-time defaults), `Component.frame()` and aligned turntable
      connections, the `follow` flag in state frames, `port(range_from=)` per-instance ranges,
      `ranges:` in int16_scaled lockfiles (ADR-0004 decision 9, amended 2026-10-27), `port(alias=)`
      for the `raise` keyword, and
      `tools/screenshot.py --script`.
      *Diverter parity, gating again (2026-10-27 ruling):* parts in contact with a diverter arm
      (within 0.1 mm of its face, `Kinematics.guided`) are core-driven like held and turning
      parts. Traces now carry `follow` (the core's position after the tick) and `release` inputs,
      which the replay applies as the live viewer does, and the viewer sets a followed body square
      to the world axes. `parity-diverter` is a gate again: 12 comparisons, worst |Δ| 1 tick
      (was 10, informational).
      *Turntable and gantry traces (ruled 2026-10-28, built 2026-10-29):* `parity-pick-place` and
      `parity-turntable` replay the two demos under their stand-in scripts (`TraceSpec.script`;
      traces also carry `turntables` angles). Building them found a viewer bug: Rapier's default
      active collision types leave out kinematic-vs-fixed pairs, so a core-placed part (held, on
      a turning deck, against an arm) was invisible to every photo-eye sensor; the pick eye
      cleared 8 ticks early the moment the gripper took the box. Beams now include
      `KINEMATIC_FIXED`. `parity-pick-place`: 23 comparisons, worst |Δ| 1 tick, a gate.
      `parity-turntable` is **not a gate** (§9 rule 9): 20 comparisons, worst |Δ| 3 ticks against
      a tolerance of 2, all on the straight-through boxes' PE_TT edges (+3, +3 for each). The
      turned boxes agree within 2 and every checkpoint within 2. Cause: a box crossing from
      CONV_IN onto the deck at equal speed loses about 0.012 m (1.2 ticks) at the seam in Rapier,
      and the eye adds its usual 1 tick (the sorter shows the same +1). A plain belt in the
      deck's place loses exactly the same, so it is the seam, not the deck. Belt edge radius 2 mm
      left it at 3, 1 mm made it 5; 5 mm stays. Other seam traces stay within 2 because their
      downstream belt is faster and pulls the box across. A second fix (2026-10-29, folded into
      the same commit) makes beams ignore every collider that is not a part's, so belts, decks,
      pusher plates, stopper blades and diverter arms never trip one.
      *Ruled 2026-10-30:* `parity-turntable` stays informational under §9 rule 9; the seam loss
      is a 2.9 item ("Seam crossing"). *Re-gated 2026-11-04* (2.9, seam crossing): worst 1 tick.
- [x] 2.5b Components split from 2.5: `counter_display` (4-digit readout of an AO on a panel),
      `lift` (moves a short conveyor between two heights; at_top/at_bottom DIs), reversible belts
      (belt and roller conveyors gain `bidirectional: true` with a `reverse` DO and a direction
      arrow in the viewer), and the accumulation (zero-pressure) conveyor, the first
      `scenes/_library/` composite built on 2.16 and 2.0, not a primitive (confirmed
      2026-11-01). The W207 gap case is settled here (item 5 below; 2.20's note updated).
      *Added 2026-10-27 (2.6 rulings):*
      - Part rotation on turntables: parts on a turning deck turn with it in orientation, not
        only position (the core keeps every part square to the world axes today, which is right
        for square boxes and wrong for long ones). Needed before long parts or custom meshes
        (2.15). Parts gain a yaw in the core, the state frame, and the parity traces.
      - Pick-and-place pad collision: the lowered pad stops on a part's top (and `lowered`
        reports the true pad position) instead of passing into it.
      - `color_sensor.debounce_ms` (ruled 2026-10-28): an on/off debounce on `match` and
        `detect`, like the photo eye's 20 ms, default 0 so existing scenes keep their timing.
      *Rulings (2026-11-01):* one commit per item, in this order, the box ticked with the last:
      counter_display; lift (`raise` command, `at_top`/`at_bottom`); reversible belts; the
      composite `logic:` block, library composites by name and the 4-zone accumulation
      conveyor; W207 to the near edge of the belt beyond plus half the part depth; turntable
      part yaw (written only when non-zero, footprint rounded to 90 degrees, every existing
      trace and recording byte-identical); pick-and-place pad collision with a `contact` DI;
      `color_sensor.debounce_ms` and `photo_eye.debounce_ms` (default 20).
      *1. counter_display, done (2026-11-01):* `src/openfactory/components/counter_display.py`.
      `value` (AO) on `digits` (1-6, default 4) seven-segment digits with `decimals`, right
      aligned, a minus sign taking a digit, dashes when it does not fit; `range` (default 0 to
      the largest number shown) scales int16_scaled only (every count 0-9999 round-trips and
      shows as itself); `color` red/green/amber; fault `stuck_off` blanks it. A port's
      `range_from` may now name a `[lo, hi]` parameter. Viewer: real segment meshes, lit in the
      segment color (screenshot of `tests/scenes/counter-display.yaml` under its counting
      script reads "12" at t 24 s).
      *2. lift, done (2026-11-01):* `src/openfactory/components/lift.py`. `run`, `raise` (held:
      up; released: down, at `lift_height / lift_time_s`), `running`, `at_top`, `at_bottom`,
      `start: bottom|top`. Kinematics: lifts join turntables as switching conveyors
      (`SWITCHING_TYPES`, may feed several); parts on the deck move with it; a connection with a
      lift is active only while the belt tops are level within 1 mm (`LEVEL_M`); belt tops are
      read from each conveyor's current frame. Parts on a moving deck follow the core in the
      viewer, whose deck body rises with the core's `height_m`. `tests/scenes/demo-lift.yaml`
      (infeed at 0, outfeed at 0.6 m) under `demo-lift.py`: PASS 3 of 3, 6 boxes at 60 s, 36 at
      300 s, longest dwell 16.58 s both; every existing parity trace and kit unchanged.
      *3. Reversible belts, done (2026-11-01):* belt and roller conveyors take
      `bidirectional: true` with a `reverse` DO (each needs the other). With `run` and `reverse`
      the surface speed is negative, ramping through zero. Kinematics: parts move toward the
      infeed end, queue in reverse (`_room_behind`), stop on a raised blade from its far side,
      and hand back through an active connection to the feeder whose discharge end is nearest;
      no feeder: they fall off; feeders out of line or level: they wait at the infeed end.
      Reversing belts move their parts after forward ones, upstream first. The `reverse` state
      and `bidirectional` geometry keys appear only on bidirectional conveyors, so every
      existing trace, recording and kit stays byte-identical (CONTEXT.md files list the new
      `bidirectional` parameter). Viewer: a direction arrow on the near rail; Rapier carries
      parts back at the negative belt speed. `tests/scenes/demo-reversing.yaml`: 3 boxes on to
      REM_FWD and 2 back across the seam to REM_BACK in 60 s, none lost.
      *4. Composite logic and the accumulation conveyor, done (2026-11-01):* `logic:` drives a
      built-in part's DO port from an expression over parts' DI/AI ports and `{command: DO}` io
      names, evaluated each tick before the parts step (`core/logic.py`, one tick of delay); the
      port has no tag, and exposing it in io too is a load error naming both. Composites gain
      `connections:` between their own conveyors. `{library: name}` loads
      `_library/<name>.yaml` beside the scene or above it, and bundles carry it. First entry:
      `scenes/_library/accumulation_conveyor.yaml` (four zones, `release` DO, four zone eyes).
      `scenes/examples/accumulation-demo.yaml` under its script: four boxes rest one per zone,
      no zone empty between them, 0.69-0.71 m apart (none touching); on release zone 4 starts
      at once and each zone behind starts the tick after the eye ahead clears (14.00 -> 14.02,
      14.60 -> 14.62, 15.20 -> 15.22 s); lint 0 errors 0 warnings; bundled, it passes its
      checks from the `.ofz`. Read "zero gaps" as no empty zone between held boxes and "no
      pressure" as no box touching another (confirmed 2026-11-01: zero-pressure accumulation).
      *5. W207 across a gap, done (2026-11-01):* `pusher.beyond_path` finds the belt a pushed
      part lands on (the nearest along the plate's centre line starting at or past the swept
      belt's far edge, no higher). With a gap, the stroke must reach its near edge plus half
      the deepest emitted part's depth along the stroke: the whole part on the belt beyond,
      not balanced on the seam (ruled "plus" deliberately). Message: "CYL_01: stroke 0.6 m
      leaves the part straddling the seam; 0.9 m puts it fully on CONV_02". Touching belts keep
      the far-edge rule; the shipped scenes still lint clean; 2.20's known-gap note is replaced.
      *6. Part yaw on turntables, done (2026-11-01):* `Part.yaw` turns with the deck in
      `_turn`; `Part.footprint` (size with x/z swapped past 45 degrees) is what collision,
      sensing and `aabb()` use. The state frame writes `yaw_deg` only when non-zero, measured
      against the deck carrying the part (the viewer adds the deck's `angle_deg`) and folded by
      the box's symmetry (90 degrees for a square footprint, else 180); so square boxes, the
      only ones any existing scene emits, never write it, and every parity trace, kit and
      recording is byte-identical (37 boxes ride the two-lane-merge deck while it turns).
      Parity traces carry no yaw. Viewer: followed and newly added parts take the drawn yaw;
      replayed meshes turn too. `tests/scenes/turntable-long.yaml` (0.5 x 0.25 m boxes under
      the demo-turntable script): turned boxes leave on CONV_SIDE at yaw 90 with their long
      side along that belt, 5 straight and 5 to the side, none lost; screenshot checked.
      *7. Pick-and-place pad collision, done (2026-11-02):* a lowering pad stops on the top of
      the highest part under it (not held, footprint overlapping, top at or below the pad);
      `lowered` stays the full-stroke limit, so it is false while the pad rests on a tall
      part; the new optional `contact` DI is true while the pad rests on a part or holds one,
      and `grip` picks when lowered or in contact. Only the pad collides. In both existing
      gantry scenes the pad reaches each box's top exactly at full stroke (deepest
      penetration 0.0 m, measured before the change), so parity traces and kit recordings are
      unchanged; `contact` is not added to the component's state, and only the CONTEXT.md
      files list the new port.
      *8. Sensor debounce settings, done (2026-11-02):* `photo_eye.debounce_ms` (default 20,
      the old fixed value, so every trace is unchanged) and `color_sensor.debounce_ms`
      (default 0, as before), 0 to 1000, through the shared `core/debounce.py` (the photo
      eye's algorithm moved there unchanged: a change must hold on consecutive steps for the
      debounce, from the first step that saw it). The color sensor debounces `match` and
      `detect` each on its own. Parity traces, kit recordings and every other kit file are
      byte-identical; CONTEXT.md files list the new parameter. With item 8, 2.5b is done.
- [x] 2.6 `fault_inject` API on all components + `/api/fault` endpoint + viewer context menu.
      Taxonomy (per component where meaningful): `stuck_on`, `stuck_off`, `jammed` (actuator or belt
      stops moving, feedback honest), `slow` (actuator at 50 % speed), `misaligned` (sensor sees late
      or early by a configurable offset), `noisy` (random dropouts), `drift` (analog offset growing
      with time). One fault per component at a time; `/api/fault` lists what is active.
      *Done when (defined 2026-10-26, the task had none):* every kind is unit-tested on a component
      that supports it; `POST`/`GET`/`DELETE /api/fault` round-trip against a running server with
      404 and 400 for unknown components and unsupported kinds; a fault is visible to a PLC over
      Modbus; on the sorter under its script, `CYL_01 jammed` keeps `CYL_01_EXT_DI` off and sends
      the tall boxes to REM_01, and `PE_01 stuck_off` commands no divert; and the viewer's context
      menu is screenshotted and looked at.
      *Note (2026-10-26):* `core/component.py`: `Component.FAULTS` per type, `fault_inject`,
      `clear_fault`, `speed_factor` (jammed 0, slow `factor`) multiplied into every motion rate,
      and DI/AI faults applied in `write()` before the raw value (so they precede 2.17
      transforms: an NC eye stuck on reads false at the PLC). `Component.tick` runs the fault's
      clock in simulated time, then `step`. `noisy` dropouts are drawn from the component's own
      `world.rng("<id>:fault")` (the emitter's sequence is unchanged) and last `duration_s`
      (default 0.2 s, so 100 ms PLC polling sees them). The runtime applies a fault under the step
      lock, between ticks, and logs `fault`/`clear` events with the core tick (for 2.12 replay).
      The viewer marks a faulted component's label in ISA-101 alarm yellow, draws a misaligned
      eye's beam where the core has it, and opens the menu on a right-click that did not drag
      (OrbitControls pans on right-drag) or a 600 ms long-press. Per-type kinds are in
      `docs/components.md`, checked against the code. Results: sorter 60 s under its script,
      healthy REM_01 {short: 9}, REM_02 {tall: 9}; CYL_01 jammed: CYL_01_EXT_DI on for 0 ticks,
      REM_01 {short: 9, tall: 9}, REM_02 {}; PE_01 stuck_off: extend commanded for 0 ticks, same
      result. Not done here, moved to 2.11 (2026-10-27): a `--fault` flag for headless runs, and
      hiding the fault marker from trainees in exam mode.
      *Rulings (2026-10-27):* stuck faults stay at the device, before transforms (where a real
      fault lives; NC wiring is the transform). The pilot light gained `stuck_off` (a burned
      bulb); the part remover stays fault-free. `/api/fault` needs a token off loopback (§6.4).
- [x] 2.7 Scenes: `conveyor-start-stop` (hello world), `sorting-by-height`, `sorting-by-color`,
      `buffer-and-release`, `palletizer-lite`, `two-lane-merge`
      *Done (2026-10-28):* one commit per scene, each with both lockfiles, an OpenPLC program
      (`examples/openplc/`), a stand-in script (`examples/pyplc/`), a guide (`docs/guides/`,
      indexed in `docs/guides/README.md`; the sorter's is the OpenPLC quickstart), and a clean
      `openfactory lint`. Each ran 60 s under OpenPLC (`openfactory/openplc:b5d4135`, configured
      by `tools/openplc_configure.py`) and 60 s headless under its script, with identical
      remover counts:
      | Scene | Remover counts, 60 s (OpenPLC = script) |
      |---|---|
      | `conveyor-start-stop` | REM_01 {box: 17} |
      | `buffer-and-release` | REM_01 {box: 21} (batches of exactly 3) |
      | `two-lane-merge` | REM_OUT {a: 6, b: 6} |
      | `palletizer-lite` | REM_LYR {box: 8} (two layers of 4; see the 6 s note below) |
      | `sorting-by-color` | REM_MAIN {blue_box: 5}, REM_RED {red_box: 8} |
      | `sorting-by-height` | REM_01 {short: 9}, REM_02 {tall: 9} (1.14, 2.10) |
      `tests/integration/test_shipped_scenes.py` is the CI gate until 2.11's `checks:`: every
      scene in `scenes/` must ship those files and a guide index row, its ST program and script
      must use the same tags, and 60 s under the script must give the counts above. Built for
      these: part `color` and the `color_sensor` component (sorting-by-color had nothing to sense
      color with). Scene choices: the hello world starts with its selector in RUN so a headless
      run moves boxes, and binds its stop button NC (`invert: true`); two-lane-merge uses
      spring-raised stoppers (`invert: true`) ahead of the turntable because the viewer has no
      wall at a turned deck's side; buffer-and-release counts on a 3x faster outfeed because a
      queue has no gaps; palletizer-lite is one layer of one row (no stacking in the headless
      model) and counts placements, since watching the row eye alone let a fifth box ride out
      with the layer. OpenPLC rejects `STEP` as a variable name (IEC 61131-3 reserved word).
      `buffer-and-release` was rebuilt on a composite with 2.16 (2026-10-31).
      *Palletizer rate (ruled 2026-10-28):* at a box every 4 s the line was over capacity (about
      4.8 s a box), so `max_cycle_s` 35 passed at 60 s and failed from 120 s (47 s at 300 s). The
      emitter now runs every 6 s: longest dwell 27.4 s at 60, 300 and 600 s, 48 boxes out in
      300 s. Its 60 s count is unchanged (8) under the script; the OpenPLC re-run at 6 s waits
      for Docker (not available in this WSL session on 2026-10-28).
- [x] 2.8 Scene → GRAFCET: feed the example PLC sequence to `/grafcet-chart-creator` for each scene and
      ship the HTML in `docs/sequences/`
      *Ruled 2026-10-30:* the HTML goes in each scene's kit folder, `kits/<scene>/<scene>-grafcet.html`
      (not `docs/sequences/`), with the "generated, pending PE review" marker, one commit per scene.
      *Done (2026-10-30):* six charts, one per shipped scene, from each scene's OpenPLC program
      read directly (the skill's document mode; no MCP in this repo), laid out per the HIS
      GRAFCET standards in a 1-column layout (none of the programs has fault codes or an
      emergency override), concurrent logic as partial GRAFCETs with their own initial steps,
      the merge's and palletizer's step numbers equal to the programs' STATE and PICK_STEP
      values. Each passed the skill's Playwright review at 150 % (no page errors, no text
      outside its block); the review fixes were made directly instead of through the skill's
      Codex loop. Each scene's kit is built with its chart (`build_kit.py --check`: all six
      current). *Not done:* PE review of the charts.
      *Ruled 2026-10-31:* charts are generated, not hand-edited: `tools/grafcet_from_st.py`
      holds each scene's sequence data (written from its ST program) and the layout, and
      `build_kit.py` regenerates every chart; `tests/unit/test_grafcet_from_st.py` rebuilds the
      sorter's byte for byte and checks every tag a chart names is declared in its program. The
      deliverable footer (company, email, scripture) is replaced by one line, "HIS Controls,
      OpenFactory, CC-BY-4.0", in code.
      *Ruled 2026-10-31 (second):* a stale chart fails CI, not drift silently. Each scene's
      sequence data stores `st_sha256`, the SHA-256 of the ST program it was read from (LF line
      endings); `build_kit.py --check` fails when the program's hash differs, naming the scene
      and the command that prints the new hash (`grafcet_from_st.py --hash SCENE`) to store after
      the sequence is re-read. Tested both ways in `test_grafcet_from_st.py`.
      *Process kits (ruled 2026-11-08):* charts for the four process scenes through the
      generator, with their sequences and stored hashes. `tank-level-onoff`: not filling and
      filling (the FILL seal-in), with the return at LSH. `tank-level-pid` and
      `two-tank-cascade` are continuous loops, so their charts are the operating modes the
      program moves through, not an invented sequence: startup in manual (1 s), automatic
      with the PID block's equation as the action, and the high-level interlock (valve shut,
      controller held in manual), returning to automatic when the switch clears.
      `batch-mix-heat`: idle, fill water, fill syrup, heat, hold (no return: the batch is
      held). Ten kits now carry a chart; PE review of all ten is still not done.
- [ ] 2.9 Performance: 200 parts at 60 fps in the viewer on integrated graphics; profile and fix.
      **First, before any other 2.9 work (ruled 2026-10-30): the real-time tick loop falls
      seconds behind when a viewer is connected.** Seen during 2.11 with a software-rendered
      browser: the server's sim stalled and caught up in bursts. It breaks demos and exam
      sessions (`openfactory test`, headless, is unaffected). The tick loop must not be starved
      by state serialization or WebSocket sends. Measure first (tick lateness with and without a
      viewer, same session), then move serialization off the tick path.
      *First item done (2026-10-30), 2.9 itself not:* `tools/tick_drift.py` runs a scene in
      real time behind the web app and samples simulated time against the wall clock every
      100 ms, with WebSocket clients (`--viewers N`) or real viewer tabs in headless Chromium
      with software GL (`--browsers N`); `tests/scenes/load-200.yaml` holds the scene at the
      200-part cap. Measured, at 200 parts: no viewer, 0 dropped, ticks 5.8 ms (CPU = wall);
      three software-rendered viewers, up to 2.55 s behind with 116 ticks dropped in one run.
      The tick's own CPU time rose from 5.8 to about 10 ms and it waited about 5 ms more per
      tick: the viewers' software rendering competes for the same cores. Ten in-process
      WebSocket clients did not slow the tick (6.1 vs 5.9 ms), so the GIL was not the main
      cause. Changes: the snapshot is built on demand by the first reader after a tick, under
      the step lock, instead of every tick on the tick path (still every tick while recording,
      so recordings are unchanged), and `/ws/state` serializes each snapshot once for every
      viewer. Same session, alternating, three rounds each, three viewers at 200 parts: before
      0.29-0.44 s behind, 3-10 ticks dropped, worst tick 227-324 ms late, tick mean 14.3-14.9 ms
      (p99 49.7-51.7); after 0.07-0.10 s behind, 0 dropped, 53-72 ms late, mean 10.8-11.1 ms
      (p99 35.1-38.4). Ten WebSocket clients: CPU on the server's other threads 17.2-19.1 s
      before, 11.4-12.5 s after, over 40 s. What remains is CPU contention from viewers on the
      same machine; the lever left is the tick's own cost (the queueing item below, 7.5 ms of
      the step at 200 parts).
      **Seam crossing (ruled 2026-10-30):** Rapier loses about 1.2 ticks per belt-to-belt seam at
      equal speeds; the headless model loses none. Either model the loss headless or correct it in
      the viewer so seam-heavy scenes gate at 2 ticks (`parity-turntable`, note under 2.5).
      *Done (2026-11-04), in the viewer; the core is unchanged:* the cause is Rapier's contact
      recycling. Rapier reuses a contact pair's contacts while the two bodies barely move
      relative to each other (on by default since Rapier 0.35, per its changelog), and a box
      riding a belt hardly moves relative to the belt it meets at a seam, so its contacts against
      that belt's rounded entry edge go stale. The box sinks about 0.4 mm into the belt it rides
      (Rapier's allowed penetration: a 0.2 m box rests at y 0.09964), its leading bottom edge
      strikes the next belt's end, and it rises 0.13 mm and travels 8.8, 7.4 and 6.2 mm in the
      next three ticks instead of 10: 7.4 mm lost at 0.5 m/s. Rapier never recycles a pair with
      active hooks, so `PlantPhysics` (now given the scene's `connections`) sets the
      contact-pair hook, each step, on every part within reach of a seam between two connected
      belts in line, level and running at the same non-zero speed, and clears it once the part
      is clear; the hook keeps the default. Ruled out first: longer belt colliders filling the
      seam (2.0-3.2 mm left), rounded part colliders (0.7-11.9 mm, erratic), more solver
      iterations (0.6-3.0 mm), a smaller allowed error (no change). The hook flag on whole belts
      also removed the loss but moved the unequal-speed queue and stopper traces from 2 ticks to
      3, so it is per part and only at equal speeds. Results: a box crossing an equal-speed seam
      loses 0.03 mm (7.38 mm undeclared, `physics.components.test.ts`); `parity-turntable` worst
      3 ticks to 1 and a gate again; every row of the other seven traces unchanged.
      Add `tools/bench.py` running a fixed 500-part benchmark scene headless for 60 simulated seconds
      and reporting mean and p99 tick time; CI fails if p99 exceeds 15 ms. The scheduler overrun
      counter from Milestone 1 is the production-side signal for the same limit.
      *Ruled 2026-11-04, replacing the 500-part p99 gate:* a fixed scene, a fixed tick count,
      the mean step time compared with a committed baseline, failing above 25 % regression; the
      baseline is regenerated by a flag and committed with the change that moves it.
      *Done (2026-11-04):* `tools/bench.py` runs `tests/scenes/load-200.yaml` and
      `queue-200.yaml` (every DO forced on, 3000 warm-up ticks to 200 parts, then 40 blocks of
      50 ticks) and scores each as its mean step time over the mean time of a fixed pure-Python
      calibration workload run before every block, so the CPU's speed and the machine's drift
      cancel; raw milliseconds are printed, not compared. It fails when a score is more than
      1.25 times `tools/bench_baseline.json`; `--update` rewrites the baseline. CI step 11
      (`tools/ci_local.sh`). Measured here: four runs at the committed code scored -1.9 % to
      +5.2 % against the baseline (raw step times 2.38-2.76 ms); `efde1d0` +8.8 % and +9.1 %
      (passes); `b61b3f5`, the 2.5b cost before its fix, +23.1 % and +25.4 %, then +30.3 % and
      +22.0 % (fails both runs); the step wrapped in a 1 ms busy wait +50.6 % and +49.2 %
      (fails). Open: the baseline was measured on the development machine; how well the score
      carries to the GitHub runner's CPU is first seen on the next CI run.
      *Bench drift (ruled 2026-11-07: find whether the step or the calibration scales
      differently; fix the calibration if it is the cause; regenerate the baseline from a CI
      run only if it cannot be fixed).* Measured 2026-11-07, step and calibration separately:
      - CI, from the annotations (which already carried both): run 42337918843 (332b3c7) step
        1.355 / 1.334 ms, calibration 52.93 / 52.97 ms, scores -3.0 % / +0.4 %; run
        42375694006 (2007a8e) step 0.856 / 0.834 ms, calibration 40.00 / 39.89 ms, scores
        -18.9 % / -16.7 %. Between the two runners the step took 0.63 times as long, the
        calibration 0.76 times.
      - Not the code: between those commits only load-time code changed (`schema.py`,
        `runtime.py`, `opcua.py`, `cli.py`), and three local runs at each scored the same
        within noise (load-200 0.0253-0.0263 at both; queue-200 0.0270-0.0278 and
        0.0263-0.0269).
      - This machine: today it runs about 30 % slower than when the baseline was taken (step
        3.0-3.6 ms against 2.48) and the scores still land within -1 % to +5 % of the
        baseline, so step and calibration move together on one CPU. Under CPU contention (4
        or 8 busy processes) both move within about +-15 %, so contention cannot tell
        calibrations apart.
      - So the calibration is what scales differently between those two runs: it scaled less
        than the step. Two causes fit, and the runs cannot tell them apart: a different runner
        CPU, or a different Python build, since 2007a8e also moved CI from `setup-uv@v6` to
        `@v7`, which may install another CPython 3.11 build. Either fits the mechanism: the
        step is almost all Python-level calls and attribute access (`_room_ahead`,
        `_convey`, `_record`, `_local`), and the calibration's time goes mostly into C
        builtins (`insort`, `sin`, `hypot`). The annotation's new `python` and `cpu` fields
        separate the two.
      - The candidate fix, not yet gated: `calibrate_tick` in `tools/bench.py`, a frozen
        miniature of a tick's own operations (200 parts on four belts, methods, `getattr`,
        bisect, tuples; it imports nothing from `src/`, so code changes still move the
        score). It is timed in the same blocks, and the annotation carries `candidate_ms`,
        `candidate_score`, `cpu` and `python`. Whether it carries across CPU types can only
        be seen on more than one runner CPU, so the gate and the baseline are unchanged.
        Candidate scores here: 0.0686 (load-200) and 0.0736 (queue-200), against 0.0264 and
        0.0270 for the current calibration in the same run.
      - The Python build alone moves the score. On this CPU, two runs each under uv's CPython
        3.11.15 (Clang) and the system's 3.12.3 (GCC): the current score moved -7 % (load-200,
        0.0255/0.0259 to 0.0242/0.0233) and -13 % (queue-200, 0.0270/0.0276 to
        0.0243/0.0235); the candidate moved -4 % and -11 % (0.0678/0.0711 to 0.0671/0.0658;
        0.0717/0.0728 to 0.0652/0.0639). So an interpreter change could account for about half
        of the CI drift, and the candidate tracks it only a little better: not yet a fix. If
        `python` differs between the next CI runs, pinning the CI interpreter
        (`python-version` to an exact patch) may matter more than the calibration.
        *Switch rule (ruled 2026-11-07):* gate on the candidate calibration once two CI runs on
        different runners score within 10 % of the local values (0.0686, 0.0736); then
        `--update` with the candidate as the gate and commit the new baseline.
      - First CI run with the new fields, 42445724369 (f672500, 2026-11-07): AMD EPYC 7763,
        CPython 3.11.18 (Clang 23.1.3); the current score -4.0 % (load-200, 0.0241) and -8.3 %
        (queue-200, 0.0242); the candidate 0.0689 and 0.0694, +0.4 % and -5.7 % of local. CI's
        Python was not the local 3.11.15.
      - *CPython pinned (ruled 2026-11-07):* `.python-version` and `ci.yml`'s
        `python-version` name 3.11.15, the build the local venv and the baseline use, so the
        score stops following interpreter builds. Runs after the pin count toward the switch
        rule; 42445724369 ran 3.11.18 and does not.
      - First CI run after the pin, 42561175436 (be6a06a, 2026-11-08): AMD EPYC 7763, CPython
        3.11.15 (Clang 22.1.3), the local build; the current score -7.6 % (load-200, 0.0232)
        and -11.7 % (queue-200, 0.0233); the candidate 0.0695 and 0.0697, +1.3 % and -5.3 % of
        local. The first run toward the switch rule, which asks for two runs "on different
        runners" (ruled 2026-11-07). Both runs so far reported the same CPU model, AMD EPYC
        7763; the annotation cannot tell whether they were different machines. Whether the
        second run must show a different CPU model is for the owner to rule.
        *Ruled 2026-11-09:* "different runners" means a different CPU model or a different
        Python build in the annotation. Two runs on the same EPYC 7763 and the same build count
        once; the rule waits for a run on a distinct one.
      - *Six digits (ruled 2026-11-09, done):* scores and candidate scores are rounded to six
        decimals, not four: at 0.0024 one rounding step was about 4 % of the score, the size of
        the +4.2 % the fresh-clone runs showed for `tank-level-pid`; it is now 0.04 %. The
        script's scan inside the PID bench is accepted as part of the score.
      - Second CI run toward the switch rule, 42589758590 (f029e6f, 2026-11-09): **Intel Xeon
        Platinum 8573C**, CPython 3.11.15 (Clang 22.1.3), a different CPU model from
        42561175436's AMD EPYC 7763, so it counts under the ruled definition. Candidate scores
        0.067581 (load-200) and 0.071225 (queue-200), -1.5 % and -3.2 % of local (0.0686,
        0.0736); 42561175436's were +1.3 % and -5.3 %. **The switch rule's condition is met:**
        two runs on different runners within 10 % of local. The current calibration drifted
        again on this CPU (load-200 0.027518, +9.6 %; queue-200 0.028650, +8.5 %; tank-level-pid
        0.002426, +0.2 %). The switch itself (`--update` of every entry with the candidate as the
        gate; the conveyor entries carry no candidate fields yet) waits for the owner's go.
      - *Switch (ruled 2026-11-10: go; done):* `tools/bench.py` gates on `candidate_score`
        (`GATE`); the old calibration's `score` is still measured, printed ("not gated") and
        kept in the baseline. An entry without a candidate score fails as "no baseline".
        `--update` of every entry on this machine (i7-1260P, CPython 3.11.15, Clang 22.1.3), so
        the conveyor entries now carry the candidate fields: candidate scores load-200
        0.064295, queue-200 0.066211, tank-level-pid 0.006356 (old scores 0.023909, 0.026009,
        0.002333). These are 6 % and 10 % below the local values the switch rule used (0.0686,
        0.0736, measured on 2026-11-07), within this machine's day-to-day spread; against them
        the two qualifying CI runs score +8.1 %/+5.3 % (EPYC 7763) and +5.1 %/+7.6 % (Xeon
        8573C), inside the 25 % limit. A gate run right after, same session: -3.8 %, +3.0 %,
        +7.3 %, all ok. `tests/unit/test_bench.py`: the gate is the candidate score and the old
        score never decides; the committed baseline has candidate fields and a CPU for every
        entry.
      - First CI run gated on the candidate, 42955811835 (9290768, 2026-11-11): **AMD EPYC 9V74**
        (a third runner CPU), CPython 3.11.15 (Clang 22.1.3). load-200 0.067548 (+5.1 %),
        queue-200 0.068200 (+3.0 %), tank-level-pid 0.004892 (**-23.0 %**); all pass. The
        conveyor entries carry across all three runner CPUs seen (within -2 % to +8 % of this
        baseline); the process entry does not on this one: its step ran 3.0 times faster than
        here (0.086 against 0.262 ms) while the candidate ran 2.3 times faster (the old
        calibration missed by more, -31 %). A gate 23 % low on a runner lets a regression of up
        to about 60 % pass there.
      - Second CI run gated on the candidate, 42958754035 (36c5b58, 2026-11-11): **AMD EPYC
        9V45** (a fourth runner CPU): load-200 0.065927 (+2.5 %), queue-200 0.068382 (+3.3 %),
        tank-level-pid 0.006375 (**+0.3 %**). So the process entry's -23 % is one runner CPU
        (EPYC 9V74), not every runner. For the owner, on two data points: accept, rebase that
        entry on CI runs, or give the process scene its own calibration; or wait for more runs.
      - *One-sided gate (ruled 2026-11-10, done):* for every entry, a candidate score more than
        25 % slower than its baseline fails; more than 25 % faster passes and is annotated as a
        warning (`::warning`) naming the entry and the runner's CPU, since then the calibration
        does not carry to that runner and the gate has lost headroom there; within 25 % either
        way is a plain notice. Baselines unchanged. The two runs behind the ruling: 42955811835,
        EPYC 9V74, tank-level-pid -23.0 % (would have been plain under this rule, inside 25 %),
        and 42958754035, EPYC 9V45, +0.3 %. `tools/bench.py` (`too_fast`); `tests/unit/
        test_bench.py`: -26 % warns and names the CPU, exactly -25 % and -23 % are plain, +26 %
        fails, and on Actions an all-faster run passes with `::warning` annotations. (As first
        committed, 24b4654, the warning fired below base / 1.25, at -20 %, while the line
        printed the change against the baseline; fixed to warn below -25 % as printed.)
      - Run 42964905710 (07fa743, 2026-11-11), the first under the one-sided rule, EPYC 9V74
        again: load-200 +0.5 %, queue-200 -1.5 %, tank-level-pid -25.6 %, which passed with the
        warning annotation naming the CPU.
      - *Process scene in the gate (ruled 2026-11-08):* `tank-level-pid` under its stand-in
        script for the fixed 5000 ticks replaces `flow-net` (`PROCESS` in `tools/bench.py`):
        0.18 to 0.26 ms a tick here, the script's scan included, score 0.0023 to 0.0024 over
        four runs. Its entry was measured with the new `--update --only tank-level-pid`, so the
        conveyor entries (and the switch rule's local values) are unchanged.
      **60 fps at 200 parts (ruled 2026-11-04):** measure frame time on the software renderer
      and on the owner's machine through a number in the stats overlay; instance part meshes and
      merge static belt geometry if needed; report before and after.
      *Progress (2026-11-04), not done (waits for the owner's reading):* the HUD's fourth line
      shows the mean and p95 time between rendered frames, the main thread's render time per
      frame, its time per 20 Hz state frame (scene updates and viewer physics) and the draw
      calls (`web/src/framestats.ts`; also `window.__openfactory.frameMs`, `frameP95Ms`,
      `renderMs`, `stateMs`, `drawCalls`). Each belt's slats are one instanced mesh, not one
      mesh per slat. Software renderer (headless Chromium, SwiftShader, 8 cores shared with the
      core), `tests/scenes/load-200.yaml` with every DO forced, at 200 parts, 1280x720: before,
      215-230 ms a frame (p95 517-550), 1732 draw calls, render 22.6-23.3 ms; after, 208-224 ms
      (p95 533), 542 draw calls, render 6-8.6 ms, state 6-9.6 ms. The frame time did not move
      because SwiftShader is bound by rasterizing on the CPU, not by draw calls or the main
      thread (busy 17-24 % of the time): hiding all 200 parts (142 draw calls) left it at
      211 ms; `conveyor-start-stop` idle (23 draw calls, no parts) runs 80-90 ms at 1280x720
      and 30 ms at 640x360. 60 fps is out of reach on the software renderer at any part count.
      Parts are not instanced: here they cost about 0.3 ms of render time. The owner's
      reading decides the next step: run `uv run openfactory run tests/scenes/load-200.yaml`
      with `--force` on every DO (`CONV_nn_RUN_DO=1`, `EMT_nn_EN_DO=1`, nn 01-10), open the
      viewer, and read the frame line once parts reach 200. A p95 above 16.7 ms with render plus
      state well below it means the GPU or its driver: part instancing (400 of the 542 draw
      calls) and a static merge come next.
      *Same visit (added 2026-11-11):* once 4.14 is built, read the same frame line at each of
      its quality levels (low, standard, high). One visit settles this reading and 4.14's
      default level. 4.14 is built (merged into main 2026-11-12): open the viewer with
      `?quality=low`, `?quality=standard` and `?quality=high` in turn, or use the control at the
      bottom left. The frame line names the level it was read at.
      **Physics engine check:** Jolt (MIT) compiled to multi-threaded WASM supports true conveyor
      surface velocity through its contact listener, which Rapier JS lacks (`libraries.md`). Build a
      spike branch swapping Rapier for `jolt-physics` on the sorter, compare parity trace, frame time at
      200 parts, bundle size, and API ergonomics. Write ADR-0005 either way. Do not switch on a hunch;
      switch if parity tightens or frame time halves.
      *Ruled 2026-11-04:* a written comparison in `docs/research/`, no dependency change, a
      recommendation with numbers (in place of the spike branch and ADR-0005).
      *Done (2026-11-04):* `docs/research/jolt-vs-rapier.md` recommends keeping Rapier; neither
      switching criterion is met. Same setups in both engines in Node (`docs/research/
      jolt-spike/bench.mjs`, Jolt 1.1.0 single-threaded with conveyor surface velocity from its
      contact listener, Rapier 0.21 built as `physics.ts` without the seam fix). Ramp to belt
      speed: 4 ticks in both, as the core. Equal-speed seam: Rapier 7.38 mm stock (0.03 mm in
      the viewer now), Jolt 14.3-25.4 mm over nine convex radius settings. Queue at a stop,
      worst centre error: Rapier 4.07 mm, Jolt 1.14-1.26 mm. Step at 200 boxes: Rapier mean
      2.00-2.42 ms, Jolt 1.26-1.99 ms (not halved; p99 worse). Bundle gzip: Rapier 1.65 MB,
      Jolt 0.88-0.91 MB.
      **Headless queueing cost (from 2.0, added 2026-10-26):** the part-to-part queueing check in
      `core/kinematics.py` costs 11.7 ms per tick at the 200-part cap after the belt-local box
      cache (56.4 ms before it), and it is still quadratic in the parts on a belt. Target under
      4 ms per tick at 200 parts (a spatial index per belt along the flow axis, or sorting once per
      tick and checking only the neighbour ahead).
      *Done (2026-10-31, ruled the next 2.9 item):* each belt keeps its parts' boxes sorted by
      flow position (`_order`), and the room-ahead search walks forward from the part and
      stops once no box further along could be nearer (an upper bound on box half-lengths,
      with a 1e-6 m margin over float rounding): the same candidates, tests and minimum as the
      full scan. The part remover also tests a bounding circle before its exact box test (same
      result; it was a third of the step at 200 parts, every remover testing every part).
      Same session, alternating, three rounds each, `LoadedScene.step` at a steady 200 parts:
      `tests/scenes/load-200.yaml` (ten lanes, 20 parts a belt) 4.61-6.71 ms mean before,
      2.20-3.34 ms after; `tests/scenes/queue-200.yaml` (two lanes, about 100 queued nose to
      tail on each belt, the quadratic worst case) 7.31-9.35 ms before, 2.03-2.75 ms after (p99
      3.0-4.6 ms). Byte-identical: a 90 s load-200 recording and a 120 s queue-200 recording
      equal their baseline recordings byte for byte; every parity trace (`parity_trace.py
      --check`) and every kit's 60 s recording (`build_kit.py --check`, all six scenes) unchanged.
      **Tick cost from 2.5b (added 2026-11-04):** 2.5b made the step 11-22 % slower at 200 parts
      (same session, alternating, against `efde1d0`: queue-200 mean 2.41-3.22 ms before,
      2.68-3.92 ms after; load-200 3.30-3.39 ms before, 3.67-3.89 ms after). Recover it: back to
      the `efde1d0` numbers or better on both scenes, measured against `efde1d0` in the same
      session, with every trace, recording and kit byte-identical.
      *Done (2026-11-04):* the cost was `Part.footprint` (and its `round`), called through
      `_extent` about four times per part per tick, plus the new sort key. `_convey` now reads
      the part's local position and half-extents from the box `_record` cached this tick (the
      part has not moved since, so they are the same numbers), `_record` reuses the
      half-extents when the part stays on its belt, `footprint` returns `size` for a part with
      no yaw, and the step keeps the `efde1d0` sort key unless a belt runs in reverse. Measured
      by interleaving: a base and a candidate process hold the same scene in memory and take
      turns running 50-tick blocks (80 each, order alternating), so machine drift hits both;
      separate runs varied 30 % for the same code. Candidate over `efde1d0` per block pair,
      median (quartiles), two runs each: queue-200 0.915 (0.870-0.981) and 0.953
      (0.885-1.051); load-200 0.885 (0.839-0.946) and 0.890 (0.798-0.958). The same method
      on the code before the change (`b61b3f5`): queue-200 1.178, load-200 1.235. Medians, ms:
      queue-200 3.244 base, 3.054 candidate; load-200 2.463 base, 2.301 candidate.
      Byte-identical against `b61b3f5`: recordings of queue-200 (120 s), load-200 (90 s),
      demo-turntable, demo-pick-place, demo-reversing, demo-lift, counter-display,
      buffer-and-release and two-lane-merge (120 s each) and the accumulation example (60 s);
      `parity_trace.py --check` passes and `build_kit.py --check` reports all six kits current.
      **Measure against a same-session baseline (added 2026-10-27):** absolute tick times on the
      development machine vary about 30 % from day to day (11.65 ms on 2026-10-26 for the same
      code that measured 15.4 ms and 15.8 ms on a slower day). Every before/after claim runs the
      baseline commit and the candidate in the same session on the same machine and reports both;
      a single absolute number is not evidence of a regression or an improvement.
      **Viewer shows a queued box tipped over (opened 2026-11-01):** if the viewer shows a box
      fallen over while the core holds it upright, a trainee sees a lie. Reproduce:
      `uv run python tools/screenshot.py tests/scenes/gated-conveyor.yaml --seconds 10 --force
      EMT_01_EN_DO=1 --force GC_01_RUN_DO=1 --force GC_02_RUN_DO=1`. At about t 10.5 s (tick
      523 in the 2026-11-01 run, 4 parts; the viewer runs in real time, so the exact tick
      varies) the viewer draws a box behind GC_01_GATE tilted up onto the box held at the gate;
      seen in two runs (2026-10-31 and 2026-11-01). `openfactory replay` cannot reproduce it:
      it draws parts where the recording has them, not through Rapier. The core at tick 525
      (same forces, headless `--record`) holds all four upright on GC_01_BELT at y 0.1 m,
      queued nose to tail at x 1.65, 1.35, 1.05 and 0.75 m, each stopped against the one ahead
      by ticks 180, 290, 400 and 515 (frames every 5 ticks). Likely cause, not yet confirmed:
      Rapier lets a pushed box climb the one stopped ahead of it. Fix it in the viewer (the core
      is authoritative for queued parts) and add a viewer check that every part's drawn up-axis
      stays within a few degrees of the core's.
      *Done (2026-11-04):* the cause was not a climb. A viewer joining a run already going
      (its first frame, `planSteps` reset) set the raised gate with
      `setNextKinematicTranslation` before adding the frame's parts at the core's poses, so on
      the next step the blade rose out of the belt in one tick, through the bottom of the box the
      core holds against it, and threw it (up to 0.49 m high, turned 173-180 degrees); it fell
      back across the queue or over the gate. `screenshot.py` opens the page a variable time
      after the run starts, so the bug appeared only when the first box reached the gate
      (tick 180) before the viewer joined. Evidence: replaying the core's inputs through
      `physics.ts` from tick 1, with jittered spawns or with frames dropping up to 4 ticks in 14,
      never tilted a box past 0.9 degrees (13 runs); replaying a join from the core's state at
      ticks 200, 220, 260, 300, 400, 523 and 700 flipped the gate box every time. Fix, viewer only:
      `PlantPhysics.snapDevices()` puts every plate, blade and arm at its set pose at once, and the
      viewer calls it on a reset frame before adding parts; `addPart` exempts a part placed over a
      raised blade's line, which the core let pass. The same joins after the fix: worst tilt
      0.06-0.19 degrees, no part above y 0.100. Live, the viewer opened when the core reached
      tick 523: before (fix call removed), joined at 520, P000001 flipped 179.98 degrees by tick
      568 and rode GC_02 while the core held it at the gate; after, joined at 542, worst tilt
      0.18 degrees through tick 1180, the queue whole (screenshots at the join +2, +10, +25, +50
      and +100 ticks). The viewer check: the viewer publishes `maxTiltDeg` and `tilt` (part,
      tick) for parts the core carries on a conveyor, `tools/screenshot.py` prints them and
      fails above `--max-tilt-deg N`, and CI runs it with 5 (`tools/ci_local.sh`).
      `web/src/physics.components.test.ts` joins at the gate (0.04 degrees; 173 without the snap)
      and over the blade line (passes; 92 degrees without the exemption).
      **Sorter divert overshoots in the viewer (found 2026-11-04 by the tilt check):** in
      `sorting-by-height` under its script, Rapier sends a pushed 0.4 m box across CONV_02 and
      over its far edge (z 1.3 m) while the core carries it on CONV_02: in two 60 s runs, P000003
      at tick 662 (drawn at y -6.9 m) and at tick 619 (y -0.34 m). The core moves a pushed part
      with the plate and gives it no momentum; Rapier keeps the plate's speed. The other five
      shipped scenes stay under 2 degrees for 40 s. Not fixed: falling parts are outside the
      check (they may tumble), but this one the core still carries.
      *Fixed (2026-11-06, ruled the next 2.9 item):* the cause is earlier than the overshoot.
      Driven across at the plate's 1.5 m/s, the tall box caught its leading bottom edge on
      CONV_02's rounded side edge at tick 8 of the push, tipped over the plate (38 degrees at
      full stroke) and flew off CONV_02 (2.5 m in a Node replay). Fix, viewer only:
      `PlantPhysics.pushParts()` pushes as the core does (kinematics.py, "Pushed"). While a
      plate advances, a part resting on a conveyor in its path is kinematic, placed with its
      back face on the plate face, upright at its yaw, and still carried at its velocity across
      the plate axis. When the plate stops it is dynamic again with no speed along the axis. A
      part not resting on a conveyor is still pushed by Rapier's contact. The position check
      extends the tilt check: the viewer publishes `maxOffDeckM` and `offDeck` (part, carrier,
      tick), how far it drew a part the core carries (not one it places) outside its carrier's
      deck (`offDeck()` in `physics.ts`). `tools/screenshot.py --max-off-deck-m M` fails above
      M, and CI step 7 now runs the sorter under its stand-in script for 20 s with
      `--max-tilt-deg 5 --max-off-deck-m 0.05`. Sorter under its script, 20 s:
      - before: tilt 177.43 and 174.38 degrees, off CONV_02 by 3.58 and 3.31 m, both checks fail;
      - after, three runs: tilt 1.71 degrees, off-deck at most 20 mm.

      The 20 mm is the viewer's one-tick lead. The core pushes with the plate travel of the tick
      before, so at tick 443 it has P000002 at z 0.24 on CONV_01, where the viewer, whose plate
      has always run a tick ahead, has it at z 0.27. `web/src/physics.components.test.ts`: at
      four approach offsets the pushed box sits at z 0.4500 (the core's 0.45) from full stroke
      on, upright to 0.01 degrees. All eight parity traces are unchanged, row for row.
      **A box pushed off a belt end hangs until the plate stops (noted 2026-11-06, open):** the
      viewer moves a pushed part kinematically while the plate advances, so a part pushed past
      the edge of every deck stays at belt height until the plate stops advancing, then falls.
      The core lets it fall as soon as its centre leaves the deck. No shipped scene pushes a part
      off a deck into the air (the sorter pushes onto CONV_02), so nothing shows it yet. A fix:
      in `pushParts()`, release a pushed part once its centre is over no deck.
      **The viewer's plate runs one tick ahead of the core's push (noted 2026-11-06, open):** a
      frame's steps move the plate from the last frame's stroke to this frame's, while the core
      moves parts by the travel the plate made on the tick before. The viewer pushes a part one
      tick early (30 mm at the sorter's 1.5 m/s), inside the parity tolerance; it is the 20 mm
      the position check reports at the sorter's crossing tick. If parity tolerance is ever
      tightened, feed the plate the previous tick's stroke.
      *Amended 2026-11-18 (owner's ruling, PLAN 5.14e):* the snapshot is still built on demand,
      except that with a viewer able to connect, every 5th tick builds the frame the capture
      buffer keeps, on the tick path (a viewer reading that tick reuses it). A run with no viewer
      (`--headless`, `openfactory test`) builds none, as before.
- [x] 2.10 Scripted stand-in PLC: `openfactory run --script path.py` loads a Python file exposing
      `def step(io, t)` that reads DI/AI and writes DO/AO through the same writer-side rules a protocol
      server obeys. Ship `examples/pyplc/sorting-by-height.py`.
      *Why:* every scene can then be demonstrated and tested in CI without any PLC, a sales engineer
      can show a scene on a laptop with nothing installed, and scene authors get an executable
      description of the intended control logic. The reviewer wrote exactly this script by hand on
      2026-10-25 to demo the divert; it should be a feature.
      *Done when:* the sorter scene runs 60 s headless under the script with the same sort result as
      1.14 under OpenPLC.
      *Note (2026-10-26):* `src/openfactory/pyplc.py`: `PlcIO` (read any tag, write DO/AO only,
      `TagWriteError` on a DI/AI write, as over Modbus), IEC-style `TON` and `R_TRIG` timed on
      simulated `t`, and `ScriptPlc`. The runtime scans the script before every tick, under the
      step lock. A script exception stops the run, and `run` exits 1 with the traceback. An
      optional `init(io)` runs once. `--script` still starts the Modbus server, for an HMI to
      read, but warns that a PLC writing over Modbus would fight the script.
      `examples/pyplc/sorting-by-height.py` is a line-for-line port of the OpenPLC ST program.
      `tests/unit/test_pyplc.py` checks it against the ST file: the same tags in the logic, the
      same 1 s permissive, and 100 % = 32767 raw. The ST divert delay equals the script's 2.08 s
      less OpenPLC's 0.10 s polling latency, which a script does not have. 60 s headless, both
      real time and `--as-fast-as-possible`: spawned 20, REM_01 {short: 9}, REM_02 {tall: 9},
      lost {}. That is identical to 1.14 under OpenPLC.
      *Divert ring (2026-10-26):* the ST program and the script held one pending divert in one
      flag and one timer, so a second tall box reaching PE_01 within the divert delay of the first
      restarted the shared timer and the first box was never pushed. Both now give each tall box
      the next of four slots, each with its own timer (`DIVERT_PEND_n`/`DIVERT_TMR_n`, latched
      `DIVERT_OVERRUN` if a fifth box finds its slot busy). Stress scene
      `tests/scenes/sorter-tall-pairs.yaml` (every box tall, 1.5 s apart): OpenPLC, 60 s,
      REM_01 {} and REM_02 {tall: 35}; the old program sent 17 of 35 to REM_01.
- [x] 2.11 Exercise checks: a scene may declare `checks:` (for example `parts_reached: {REM_02: tall}`,
      `none_lost: true`, `max_cycle_s: 12`) evaluated by `openfactory test scene.yaml --script|--plc`
      with a pass/fail report.
      *Why:* this is the auto-grading a training tool needs and the acceptance test a client demo
      needs, from one declaration. No commercial trainer in this class grades the student's PLC
      program automatically. Also the CI gate for every shipped scene from 2.7 onward.
      The same run ends with a **statistics block**, printed by the CLI and served at `/api/stats`:
      throughput per remover (parts/hour, by part name), per-conveyor utilization (% of run time
      with `run` true), per-actuator cycle count and mean cycle time, mean and max part dwell time
      from emitter to remover, and parts lost. Keep it to counters and timers the components already
      have; this is not discrete-event simulation (see Non-Goals), it is the plant manager's view of
      a run so a demo persuades the operations side as well as the controls engineer.
      *Done when (defined 2026-10-28, the task had none):* every shipped scene declares
      `checks:` and passes `openfactory test --script` with its stand-in program in CI; a wrong
      or jammed program fails with a readable reason (the sorter with `--fault CYL_01=jammed`);
      `run` and `test` print the statistics block and `/api/stats` serves it; one scene passes
      `openfactory test --plc` with its OpenPLC program in control; and exam mode lets an
      instructor drive an `exam: switch` DI from the viewer while the fault marker is hidden.
      **Exam mode** (`--exam`): the viewer still shows the plant, but every DI the scene marks
      `exam: switch` is driven by an operator toggle in the viewer instead of the simulation, so an
      instructor can test a program against hand-driven inputs while trainees watch the 3D.
      **Faults in exercises (added 2026-10-27, from 2.6):** `openfactory run|test --fault ID=KIND
      [--fault-at T]` injects a fault headless (default parameters, or `ID=KIND:key=value,...`),
      so a check can grade how a program handles a jammed pusher or a stuck eye. Exam mode hides
      the fault marker (the label's alarm yellow and a misaligned eye's moved beam) from the
      viewer, so trainees find the fault from the PLC's point of view; the instructor's view keeps
      it. *Deferred (ruled 2026-10-30):* there is no separate instructor view yet; the instructor
      uses the fault menu and `/api/fault`. The instructor view is Milestone 4 (4.12).
      *Progress (2026-10-28), not done:* built `checks:` (§4; `duration_s`, `parts_reached`,
      `none_lost`, `max_cycle_s`, references checked at load) and `openfactory test SCENE
      --script F.py | --plc` (`src/openfactory/checks.py`), checks on all six shipped scenes
      graded in CI (`tools/ci_local.sh`), the statistics block (`core/stats.py`, printed by `run`
      and `test` before the summary line, `GET /api/stats`), and `--fault ID=KIND[:k=v,...]`
      with `--fault-at T` on `run` and `test`. `test --plc` against OpenPLC on the sorter, 60 s
      real time: healthy PASS 4 of 4 (CYL_01 9 cycles, mean 1.00 s, against 0.78 s under the
      script: OpenPLC's polling delays the retract); with `--fault CYL_01=jammed --fault-at 10`
      FAIL 3 of 4, "parts_reached REM_02: tall: 2 reached REM_02, but 7 to REM_01", exit 1. Each
      scene's `max_cycle_s` is about 25 to 30 % above its scripted 60 s maximum (the comment in
      each scene gives it).
      *Done (2026-10-29), exam mode:* a DI binding may carry `exam: switch`
      (`docs/scene-format.md`); `openfactory run --exam` makes each such tag follow an operator
      switch (`LoadedScene.enable_exam`, written after the components and transforms each tick,
      and at once when turned), listed at `GET /api/exam` and turned by `POST /api/exam`
      (guarded like `/api/fault`). The viewer shows an exam panel, and in exam mode state frames
      carry no fault markers for anyone; the instructor sees active faults through the fault
      menu and `/api/fault` rather than a separate instructor view. The sorter marks `PE_01_DI`.
      Done when, `tests/integration/test_exam_mode.py` (Playwright): the sorter under its script
      with `--exam`; a real tall box blocked PE_01 with the switch off and CYL_01 made 0 cycles;
      a click on PE_01's switch in the viewer, held for 10+ ticks, then off: CYL_01 made exactly
      1 cycle (mean 0.78 s); a fault injected meanwhile is absent from the frame and present at
      `/api/fault`. Found on the way: under a software-rendered browser the server's sim can
      stall and catch up in bursts, so a 0.4 s wall-clock switch pulse fell between two ticks;
      the test holds the switch for 10 ticks and counts the pusher's cycles with `/api/stats`
      instead of polling `io`.
- [x] 2.12 Record and replay: `--record file.ofrec` logs every I/O table change with its tick;
      `openfactory replay file.ofrec` drives the viewer from the log with no simulation.
      *Why:* reproducible bug reports from users, deterministic screenshots and videos for docs, and the
      raw material for a sequence-of-operations document (feeds 2.8).
      *Done when (defined 2026-10-29, the task had none):* the sorter recorded 60 s under its
      script with a fault at 10 s; two such recordings are byte-identical; with the scene deleted,
      the replay in the viewer draws every part where the recording has it and shows the fault
      from its tick; `replay --io` lists the same PE_01 edges as the live core, tick for tick.
      *Done (2026-10-29):* `src/openfactory/record.py`. Format: gzip (mtime 0) JSON Lines, a
      header with the scene's own YAML and resolved document, `dt` and encoding; I/O changes
      every tick; a full state frame every 5 ticks (10 Hz); events (faults, exam switches, button
      presses); an end record with the summary, statistics and any script traceback. Simulated
      time only, nothing machine-specific. A writer thread does the diffing, JSON and gzip off
      the step lock; the file is closed in a `finally`, and the reader takes a file cut short.
      `replay` serves the frames to the normal viewer (`mode: "replay"`: no Rapier, REPLAY on
      the HUD) and refuses fault and exam requests ("replay: no simulation"). Results: 60 s
      sorter 39,929 bytes, byte-identical twice; Playwright replay from the file alone matched
      every part at ticks 690, 700, 710, 1005 (12 parts compared) and showed "CYL_01 ⚠ jammed"; PE_01_DI edges
      match the core (20 edges). Cost at 200 parts, same session: about 1.4 ms a tick (gzip
      level 1, frames at 10 Hz) against up to 12 ms at level 9 with 25 Hz frames; about 2.2 MB a
      minute at 200 parts.
- [x] 2.13 Comms-loss injection: `/api/fault` gains `comms: {drop: true}` and `comms: {latency_ms: N}`
      that stall or delay the protocol servers without stopping the sim, so a PLC program's watchdog
      and stale-data handling can be exercised. Pairs with the SIM_HEARTBEAT tag in §6.2.
      *Done when (defined 2026-10-29, the task had none):* through `/api/fault`, with a pymodbus
      client as the PLC: `drop` gives no reply (the client times out) while the sim keeps
      ticking and a write in a dropped request is not applied; clearing it brings replies back
      with current values; `latency_ms` delays each reply by that much, once per request, for
      reads and for writes.
      *Done (2026-10-29):* `protocols/modbus.py` `CommsFault` on `ModbusServer.comms`. Drop
      raises pymodbus's `NoSuchIdException` from the action hook, and the server is built with
      `ignore_missing_devices=True`, so pymodbus sends nothing (pymodbus 3.16.1
      `requesthandler.py`; public documentation had nothing on this, so the design rests on the
      library source). Latency awaits in the hook, once per request (FC05,
      FC06 and FC23 call the hook twice). Also `--fault comms=drop` / `comms=latency:ms=N` with
      `--fault-at`, `DELETE /api/fault/comms`, comms events in `/api/events` and recordings.
      `tests/integration/test_comms_fault.py`: healthy read 1 ms; dropped read no reply, the
      client gave up at its 0.5 s timeout; 50 ticks simulated across two dropped requests and the
      dropped write not applied; cleared, 1 ms; latency 300 ms: FC02 302, FC05 304, FC03 306 ms.
      *Not done:* the same checks with OpenPLC in control (Docker is not available in this WSL
      session); the viewer's fault menu has no comms entry (API and CLI only). SIM_HEARTBEAT was
      approved on 2026-10-30 (§6.1) and is built separately. pymodbus answers a delayed request with
      the connection's latest request header, so latency is for clients that wait for each reply
      before the next request.
- [x] 2.13b Comms-loss injection on the OPC UA server (added 2026-10-30, from 2.13 and 2.4):
      `comms: {drop: true}` and `comms: {latency_ms: N}` act on `protocols/opcua.py` as they do on
      the Modbus server, so an OPC UA client (Ignition) sees Bad quality or late values while the
      simulation keeps running.
      *Done when:* an asyncua client sees reads fail under `drop` and recover when it is cleared,
      and reads and writes delayed by `latency_ms`, while the simulation ticks; the faults reach
      `/api/fault`, `--fault comms=...` and recordings as for Modbus.
      *Ruled 2026-11-04:* same semantics as Modbus, with a viewer menu entry for both protocols.
      *Done (2026-11-04):* one `CommsFault` (`protocols/comms.py`) is shared by the Modbus and
      OPC UA servers of a run, so `/api/fault`, `--fault comms=...` and recordings act on both;
      `GET /api/fault` lists them under `protocols`. `protocols/opcua.py` wraps three asyncua
      2.1.0 `UaProcessor` methods: under `drop` every service request is decoded but neither
      processed nor answered and responses already on their way, notifications included, are not
      sent (channel renewals still are, so sequence numbers stay in step); under `latency_ms`
      each request and each subscription notification waits that long. The viewer's comms menu
      opens on a right-click or long-press of empty space: drop, delay 250 or 1000 ms, clear,
      titled with the servers it covers. Evidence (`tests/integration/test_opcua_comms.py`,
      sorter, real time): healthy read 1 ms; under drop the read gave up at 501 ms, a write
      timed out and was not applied, and the sim ran 65 ticks meanwhile; cleared, the client
      reconnected (asyncua's own client closes a connection whose requests timed out) and read
      the current value in 1 ms; at 300 ms latency a read took 302 ms and a write 304 ms and the
      write landed. `run --opcua --fault comms=latency:ms=400 --fault-at 0`: an OPC UA read
      402-403 ms and a Modbus read 402 ms from the one fault, the recording carrying the comms
      event. `tests/integration/test_comms_menu.py` right-clicks empty floor in the viewer,
      picks "Drop every request" (the API then shows the drop) and then "Clear comms fault".
      *Amended (2026-11-06, found by the flaky-test hunt):* latency held a connection's later
      requests behind the one waiting. asyncua handles a connection's messages one at a time,
      and the hook slept inside that handler, so at 1000 ms a read sent while asyncua's 1 s
      watchdog read waited took 2003 ms. A client with requests in flight (Ignition's publish
      requests) saw the latency multiplied. Now each request is handled the latency after it
      arrived, in its own task, as a slow network delivers it; equal delays keep the order. At
      1000 ms: a read 1002 ms, a write 1003-1005 ms, two reads sent together 1002-1004 ms (2003
      ms before the fix, which the test now catches). A client whose watchdog or request timeout
      is under the latency sees a dead server; asyncua's watchdog probe times out at its 1 s
      interval (`docs/components.md`, comms faults).
      *Ordering (ruled 2026-11-06: write the two tests and fix what they show):* the first test
      (ten reads sent back to back on one connection, answered in the order sent, each delayed
      once) passed as it was: 1005-1006 ms each, in order. The second showed a reorder: with
      three reads waiting at 1000 ms, the latency lowered to 0 and three more reads sent, the
      later three were answered in 2-3 ms, before the first three (1004 ms). Now each connection
      keeps one queue: a message is due its arrival time plus the latency in force when it
      arrived, one task per connection handles them in arrival order, and while any wait, new
      messages queue behind them even at zero latency. After: the later three answered at
      703 ms, right behind the first three (1004 ms), in order 0-5; ten in flight 1005 ms each.
      *Fixed (2026-11-07, CI run 42365962279 on main; run 42342061795 probably the same, not
      confirmed: it predates the annotation naming the failing step):* `--fault-at 0` was a
      race. A scheduled fault was applied by the first tick, but `run` starts its protocol
      servers before the sim, so a client that connected and read before the first tick was
      answered at once, unfaulted. On the GitHub runner (and a fresh clone on a Linux file
      system) the OPC UA read in `test_one_comms_fault_covers_modbus_and_opcua_and_reaches_the_
      recording` took 1 ms against the Modbus read's 1002 ms; on `/mnt/e` the slower start hid
      it. Now `run` applies every fault due at t = 0 before the servers listen
      (`Runtime.apply_due_faults`); the first tick would have applied them at the same simulated
      time, so events and recordings are unchanged. `tests/unit/test_runtime.py` checks a fake
      server sees the latency and a component fault already in force when it starts listening.
- [x] 2.14 Demo kit convention: `kits/<scene>/` holding the scene, its PLC program(s), the exported
      I/O CSV, the generated L5X and Ignition tag JSON, the Perspective view, and the GRAFCET HTML, all
      regenerated by `tools/build_kit.py`. First kit: the sorter.
      *Why:* the one-file-to-full-deliverable pipeline is OpenFactory's differentiator (§0). A kit
      makes that claim demonstrable and gives HIS Controls a reusable client demo per scene.
      *Ruled 2026-10-30, amended 2026-10-31:* `build_kit.py` regenerates everything code can
      produce: the scene, its lockfiles, the ST program, the stand-in script, the I/O CSV, the L5X
      tag stubs (2.3), the Ignition tag JSON (2.4), the GRAFCET chart (2.8, from
      `tools/grafcet_from_st.py`) and a recording (2.12). The full L5X is dropped from the kit
      convention: a kit's L5X is the tag stub file, and full controller programs are the user's
      own work outside this repository. Outputs only the HIS skills can make (the full L5X, the Perspective view,
      the GRAFCET chart) are committed with the header line "generated, pending PE review".
      Order: 2.3, then 2.4, then 2.14.
      *Done when (defined 2026-10-30, the task had none):* `tools/build_kit.py` builds
      `kits/sorting-by-height/` from the repository alone; `--check` rebuilds into a temporary
      folder and finds it current, and finds a changed, extra or missing file; the kit's CSV,
      L5X and Ignition JSON name the same tags; its recording replays with no scene file; CI
      runs `--check`. The skill-made files are listed as missing until they are committed.
      *Progress (2026-10-30), not done:* `tools/build_kit.py` (kit README generated, the
      recording made with `test --script --record` so a failing scene cannot build a kit, and
      compared decompressed so another zlib build cannot make it stale); the sorter kit is
      committed (12 files, the recording 42 KB); `tools/ci_local.sh` step 10 runs `--check`;
      `tests/integration/test_demo_kit.py`. `--check` on a kit with a changed CSV, a new
      GRAFCET file and a stray file: "sorting-by-height-io.csv is stale", "README.md is stale",
      "stray.txt is not a kit file", exit 1; restored, "kits current", exit 0.
      *Not done:* the skill-made files (full L5X from `/io-to-l5x`, the Perspective view, the
      GRAFCET chart, which waits on 2.8); none has been generated or reviewed. A JSON file
      cannot start with a header line, so `build_kit.py` accepts the marker text anywhere in the
      first 300 characters (for JSON, a field near the top); this needs a ruling.
      *Ruled 2026-10-30:* the marker within the first 300 characters is accepted; kit
      recordings are committed; the Perspective view moves here from 2.4; Done when
      `build_kit.py --check` passes for all six kits in CI.
      *Done (2026-10-30):* six kits (`kits/<scene>/`, one per shipped scene), each with its
      GRAFCET chart (2.8). The sorter kit has the Perspective faceplate
      `sorting-by-height-perspective.json`, following the `/perspective-view` skill's method by
      hand (its `motor` type on CONV_01) against the kit's Ignition export: indirect tag bindings with a `Root`
      reference to `{view.params.tagPath}` (`{Root}/Run`, `{Root}/Running`, `{Root}/Speed`,
      `{Root}.Documentation`; each checked to be a member of `OF_BeltConveyor`, the default
      tagPath the export's `[default]SORTING_BY_HEIGHT/CONV_01`); START/STOP write `{tagPath}/Run`
      after an Operator/Engineer role check, shown only on Good quality, the write result
      checked. Binding and event formats follow a public exported Ignition project (the
      skill's own template keeps bindings inline in props, which a view.json does not); the
      skill's vendor-docs step was skipped (no HIS MCP servers here). The
      `ignition-security-reviewer` agent: first pass "needs revision" (alarm table bound to a
      literal `{Root}`, an unverified `lastIndexOf`, no quality gating, a Back button to an
      empty path), all fixed; second pass "pass with notes". `tools/build_kit.py --check`: all
      six current (`tools/ci_local.sh`, step 10), and on GitHub Actions run 41179257724.
      *Ruled 2026-10-31:* the Perspective view is accepted as is; its open items (a Designer
      load, component-level role gating, the gateway role names and write permissions, the
      demo defaults on its params, PE review) are listed in the kit README under "Needs
      Designer load before client use". The kit READMEs list nothing as missing.
      *Library composites (ruled 2026-11-04, from 2.5b):* a kit copies every library composite
      its scene names to `_library/` beside the kit's scene, where the loader finds it, and
      `--check` compares those files too. A kit can be built from `scenes/examples/` (its
      script beside the scene, no `.st`, and then no OpenPLC lines in the README).
      `tests/integration/test_demo_kit.py` builds the accumulation example's kit outside the
      repository: it holds `_library/accumulation_conveyor.yaml`, passes 2 of 2 checks from
      the kit folder, and without the library file `validate` fails naming it. The six shipped
      kits are byte-identical.
- [x] 2.15 Custom mesh per component: an optional `mesh: {file: models/press.glb, scale: 1.0,
      offset: [0,0,0], rotation_deg: 0}` on any component replaces the viewer's fixed housing
      with a user-supplied model. The headless model and physics collider keep using the
      component's declared dimensions; the mesh is visual only. Files resolve relative to the
      scene file; the server serves them under `/models/`.
      *Why:* users want their own equipment on screen immediately, and competitors in this class
      already offer model import. A curated asset library (4.2) is polish on top of this, not a
      prerequisite for it.
      *Ruled 2026-10-31:* `.glb` only in v1 (no `.gltf`); `tools/convert_mesh.py` waits until
      someone asks. A mesh is a binary under the 1 MB rule, so shipped example meshes stay small
      and larger ones belong in the user's scene folder. Every mesh in the repo has an entry in
      THIRD_PARTY.md with its license, and lint warns (W209) on a scene mesh with no sidecar
      `<file>.license.txt` or THIRD_PARTY entry.
      *Done when:* a scene with a custom housing on the pusher renders the mesh in a screenshot,
      a missing file falls back to the primitive with one warning line, and a scene with `../`
      or a URL fails validation. Parity traces unchanged.
      *Done 2026-10-31:* `MeshSpec` in `core/schema.py`: `file` (normalized to `/`; absolute,
      drive, UNC, `..`, any `scheme:`, and anything but `.glb` rejected), `scale` (number or
      `[sx, sy, sz]`, > 0), `offset`, `rotation_deg`. The loader resolves each file against the
      scene's folder (`build(..., base_dir=)`; `load` and the CLI pass it) and keeps it only if it
      is inside that folder after symlinks and has a sound GLB header (magic, version 2, length =
      file size); otherwise one warning, "CYL_01: mesh file models/pusher-housing.glb not found;
      drawing the primitive housing", and no `mesh` in the scene document. The document gains
      `mesh {url: /models/<id>.glb, scale, offset, rotation_deg}` only on components whose mesh
      resolved, so every other scene's document, kit recording, and parity fixture is unchanged;
      recordings leave meshes out (a replay draws primitives). `GET /models/<id>.glb` serves only
      those files (`model/gltf-binary`), registered before the static mount. Viewer: each builder
      names its fixed housing (`ComponentView.housing`; `docs/components.md` lists it per type),
      `web/src/meshes.ts` loads with three's `GLTFLoader`, hides the housing, and hangs the model
      offset/turned/scaled in the local frame; every mesh settles (`Promise.allSettled`) before
      the first frame, and `__openfactory.meshes = {loaded, failed}`. `tools/screenshot.py
      --min-meshes N` fails unless N drew and none failed. Example:
      `tests/scenes/sorter-custom-housing.yaml` (the sorter with `mesh` on CYL_01) and
      `tests/scenes/models/pusher-housing.glb`, 6744 bytes, a square-profile cylinder housing
      generated by `tools/make_example_mesh.py` (ours, CC-BY-4.0, sidecar license), rebuilt byte
      for byte by a test and parsed by `GLTFLoader` in vitest. `tools/third_party.py --models`
      writes THIRD_PARTY.md's "3D models" section from the sidecars and fails on a tracked `.glb`
      without one; a test checks the section is current and every repo mesh is under 1 MB.
      `openfactory validate` no longer prints a loader warning twice (log line plus its own echo).
      Evidence: screenshot `--min-meshes 1`: "custom meshes: 1 drawn, 0 failed", the housing
      visibly replaces the round body (square profile, tie rods, dark caps; plate and rod
      unchanged); `run` with the file absent: exactly one warning line (tested); `validate` of
      `../pusher-housing.glb` and `https://example.com/x.glb`: exit 1, "mesh.file ... leaves the
      scene's folder ('..')" / "is a URL" (tested); parity traces and all six kits unchanged.
- [x] 2.16 Composite components, declared in scenes without Python: a `composites:` block defines a
      new type from existing components, primitive shapes, and simple expressions:
      ```yaml
      composites:
        - type: gated_conveyor
          params: {length: 2.0, gate_time_s: 0.5}
          parts:
            - {id: belt, type: belt_conveyor, length: "{length}"}
            - {id: gate, type: stopper, position: [ "{length} - 0.2", 0, 0 ]}
          io:
            run:    {to: belt.run}
            open:   {to: gate.raise, invert: true}
            parts_waiting: {from: "gate.raised and belt.part_present"}
      ```
      Instances then use `type: gated_conveyor` like any built-in. Expressions are a small safe
      subset (arithmetic, comparisons, `and`/`or`/`not`, no function calls), evaluated by a
      purpose-built evaluator, never `eval`. Composite types may nest one level. The I/O list export
      flattens them. A `openfactory lint` warning flags a composite that exposes no I/O.
      **Lint (added 2026-10-26):** this task adds that check to `src/openfactory/lint.py` (2.20
      left it out because composites did not exist yet) as a new warning code with a unit test, and
      the Done-when below includes a composite with an empty `io:` producing it.
      *Why:* today only a Python programmer can add a machine. A scene author, a student, or an AI
      assistant should be able to build new equipment from the parts list, which is the feature users
      of commercial trainers ask for most and the one that makes the Milestone 4 editor worthwhile
      (an editor that can only place built-ins is a scene picker). Composites also keep the §5 table
      honest: a new primitive is needed only when no composite can express the behavior.
      *Done when:* the `gated_conveyor` example above runs headless and in the viewer, its tags
      appear in the exported CSV with the composite instance's id as prefix, the JSON Schema
      validates composite definitions, and a recursive composite is rejected at load with a clear
      message. Ship it in a scene (`buffer-and-release` from 2.7 should be rebuilt on it).
      `openfactory lint` warns on a composite that exposes no I/O.
      *Ruled 2026-10-31:* schema as proposed (params with defaults, numbers only; `{param}`
      expressions in part fields; `to:` / `from:` io, a `from:` expression a computed DI;
      expansion at load into `<INSTANCE>_<PART>` components; nesting one level). An unexposed
      required port is a load error naming the port. *Done when:* buffer-and-release rebuilt as a
      composite instance with its lockfiles byte-identical, the recursive-definition and
      unexposed-port errors covered by tests, the expression grammar fuzzed with at least a
      hundred generated expressions that must either evaluate or raise cleanly, W208 shown, and
      the composites overlap lint in place.
      *Done 2026-10-31:* `core/expr.py` (an `ast` whitelist, `ExprError` for every failure),
      `core/composite.py` (definition checks, expansion), schema `CompositeDef`, `CompositeIo`,
      `CompositeInstance` (a callable discriminator sends any non-built-in `type` to it; the
      expansion validator runs first, so every later check sees built-ins only), a computed DI as
      a 2.17 transform with `expr` (`TransformStage`), the scene document's `composite: {id,
      type}`. `scenes/buffer-and-release.yaml` is one `gated_buffer` instance `BUF` (belt,
      release stopper at `{length} - {gate_setback}`, batch-ready eye at `{length} - {full_at}`)
      expanding to BUF_BELT, BUF_GATE, BUF_FULL with the old positions, descriptions and tags.
      Evidence: both lockfiles byte-identical after `test` and `export` (cmp, and a test that
      rewrites them); its 60 s recording equals the pre-composite kit recording frame for frame
      once the three ids are renamed (705 of 705 frames), and a test steps the composite scene
      beside the old flat one (`tests/scenes/buffer-and-release-flat.yaml`) under the same script
      for 60 s with every state, tag and part equal; checks PASS 3 of 3, longest dwell 19.32 s as
      before. Errors tested: "recursive composite: loop_a -> loop_b -> loop_a" (and `itself ->
      itself`), "composite nesting deeper than one level: triple -> gated_pair ->
      gated_conveyor", "composite gated_conveyor: part belt's required port 'run' is unexposed",
      and 14 more (unknown parameter, wrong port kind, duplicate binding, part io, built-in name,
      unknown type, ...); a computed DI is evaluated once at load with typed readings, so
      `"eye.detect + 1"` or a numeric result (`"dist.distance * 2"`) is a load error, not a DI
      that reads false. Fuzz: boolean io 1227 distinct generated expressions (163 evaluate, 1064
      raise `ExprError`), numeric part fields through `number()` 1250 (89 evaluate, 1161 raise),
      nothing else escapes. W208: "composite drop_chute exposes no I/O". JSON Schema: a
      composite instance's `type` excludes the built-in names, so each component matches one
      `oneOf` branch; checked with `jsonschema` (then not a project dependency): all 20 scenes
      in `scenes/` and `tests/scenes/` valid, a composite without `parts` rejected. Since
      2026-11-01 `jsonschema` is a dev dependency and `tests/unit/test_json_schema_scenes.py`
      checks every scene in `scenes/`, `tests/scenes/` and `kits/` in CI.
      *Overlap lint (ruled 2026-11-01):* W205 runs on expanded conveyors and names the
      instance, "conveyors GC_01_BELT (part of GC_01, a gated_conveyor) and GC_02_BELT (part of
      GC_02, a gated_conveyor) overlap by 1 m x 0.5 m at the same height". `tests/scenes/gated-conveyor.yaml` is the plan's example (two
      instances, a computed `waiting` DI, an inverted `open`), exported with
      `expr(GC_01_GATE.raised and GC_01_EYE.detect)` in the transform column.
- [x] 2.17 Binding-level signal transforms. A binding may be `{tag: X, invert: true}` (NO/NC),
      `{tag: X, on_delay_s: 0.2, off_delay_s: 0}`, `{tag: X, combine: or, from: [PE_01.detect,
      PE_02.detect]}` for a DI fed by several outputs, and for distance sensors `{tag: X, window:
      [0.1, 0.4]}`. Transforms are applied in the I/O table layer so every protocol sees the same
      result, and they are listed in the exported CSV.
      *Why:* the most-requested driver-level features in the Factory I/O community wishlist are
      exactly these, and they are cheap in our architecture because the I/O table already sits
      between components and protocols.
      *Done when:* an inverted photo eye reads true with no part present over Modbus, an OR-combined
      input follows either eye, delays are verified to the tick, and the CSV shows the transform.
      *Note (2026-10-26):* the binding syntax is as above. `from` lists `COMPONENT.port` entries and
      must include the binding's own port; `window` goes on an AI port and binds a `_DI` tag.
      Order: combine (or window), then invert, then delays, each delay counted from the first tick
      that sees the change. Components record raw port values. Transformed DI ports are written by
      `core/transforms.py` `TransformStage`, which runs after all components each tick and is
      primed at load, so an inverted eye reads true before the first tick. `invert` on a DO makes
      the component act on the opposite of the PLC's write. The I/O CSV gained a `transform`
      column, for example `NC (inverted); on-delay 0.2 s` or `or(PE_01.detect, PE_02.detect)`.
      Measured: on-delay 0.2 s with the eye's detect changing at tick 6 gives the tag at tick 16;
      off-delay 0.1 s, detect at 31, tag at 36. `window` is tested at the stage level only: no
      Milestone 2 component has an AI output yet (a distance sensor arrives with 2.5).
- [x] 2.18 Self-contained scene bundle: `openfactory bundle scene.yaml` writes `scene.ofz` (zip)
      holding the scene, its composites, referenced meshes, lockfiles, PLC script, and a manifest with
      the schema version; `openfactory run scene.ofz` works directly.
      *Why:* a scene is a demo only if it survives email. Without this, meshes and lockfiles get lost.
      *Note (2026-11-01):* `src/openfactory/bundle.py`, `openfactory bundle SCENE [--out F.ofz]
      [--script F.py | --no-script]`, written to the current folder by default. It holds the
      scene (composites are inside it), each mesh with its `.license.txt` sidecar (a mesh
      without one is refused, since a `THIRD_PARTY.md` entry would not travel), both lockfiles
      (one on disk must match the scene, else bundling asks for `export` first; a missing one is
      generated as `run` would write it), the script (`--script`, else `<scene>.py` beside the
      scene, else `examples/pyplc/<scene>.py` above it) under `scripts/`, and `manifest.json`
      (format, bundle version 1, the scene's `openfactory:` version, each file's role, size and
      SHA-256). Reproducible: sorted entries, fixed 1980 timestamps and permissions. `run`,
      `test` and `validate` take a `.ofz`: every member must be in the manifest with a matching
      size and SHA-256, with a plain relative path (no `..`, absolute path, backslash, drive or
      link), within 1000 members and 256 MB; it unpacks into a temporary folder removed when the
      command ends, and the `.ofz` is never rewritten. **The bundled script is Python, so it
      runs only with `--bundled-script`** (on `run` and `test`); opening a bundle never executes
      it. `tools/screenshot.py` passes `--bundled-script` through. When 2.5b brings
      `scenes/_library/` composite files, the bundler must carry the ones a scene uses (role
      `composite` in the manifest; no format change). For 2.19: a `.ofz` input gets no
      CONTEXT.md (it would land in the temporary folder and be thrown away).
      Done-when: the sorter and `tests/scenes/sorter-custom-housing.yaml` bundled from copies,
      the copies deleted, run from the `.ofz` with the working folder outside the repository:
      `openfactory test sorting-by-height.ofz --bundled-script` "PASS: 4 of 4 checks passed"
      (same results as the scene file: 9 short, 9 tall, longest 7.70 s);
      `tools/screenshot.py sorter-custom-housing.ofz --bundled-script --min-meshes 1` "custom
      meshes: 1 drawn, 0 failed", the square housing visible in the PNG. Both are tests
      (`tests/unit/test_bundle.py`, `tests/integration/test_screenshot.py`), with zip-slip,
      absolute, backslash and drive paths, a link member, a size lie, a tampered and an
      unlisted member, size and count limits, a newer bundle version, a stale lockfile, a
      missing license, the script never running without the flag, and the temporary folder
      removed.
- [x] 2.19 Generated per-scene assistant context: `openfactory run` and `export` write
      `<scene>.CONTEXT.md` next to the scene listing every tag with kind, address, description and
      transform, every component with its type and parameters, the exercise checks, and the exact
      commands to run, test, and export it. Deterministic, regenerated each time, committed with the
      scene.
      *Why:* OpenFactory's claim is that an AI assistant can author and operate scenes. Give it the
      ground truth in one file instead of making it reverse-engineer the YAML and the lockfile.
      *Note (2026-11-01):* `src/openfactory/export/context_md.py`. Named `<scene>.CONTEXT.md`
      (a folder holds several scenes, so a bare `CONTEXT.md` would collide). Lists every tag
      with kind, component.port, description, transform, units and range, and its address in
      both encodings (each lockfile, or computed as `run` would write it, marked "no lockfile
      yet"), then reserved addresses of removed tags; every component after composite expansion
      with parameters (defaults included), ports (bound tag or unbound), its composite, and the
      fault kinds it supports, with a legend of the kinds and the `--fault` syntax; the
      `checks:` block; and validate, lint, test (script and `--plc`), run, export (both
      encodings, L5X, Ignition) and bundle commands, run from the scene's folder, with the
      script found as `bundle` finds it. No time, machine or `openfactory.toml` values. `run`
      and `export` rewrite it when it changed; `test` and `validate` do not; a `.ofz` input
      gets none. Committed for the six shipped scenes (a test fails on a stale one);
      `tests/scenes/*.CONTEXT.md` is git-ignored. `tools/build_kit.py` now exports from the
      kit's own copy of the scene, so the kit's CONTEXT.md names the kit's files and
      `scenes/` is never written (every other kit file came out byte-identical).
      Done-when: `build_kit.py --check` regenerates `kits/sorting-by-height/
      sorting-by-height.CONTEXT.md` byte-identical ("kits current", all six), and exits 1 with
      "sorting-by-height.CONTEXT.md is stale" when one line is appended;
      `tests/unit/test_context_md.py` asserts every tag in both lockfiles of every shipped
      scene appears in its CONTEXT.md, in `scenes/` and in the kit (23 tests).
- [x] 2.20 `openfactory lint scene.yaml`: unbound required ports, DO/AO tags no PLC-side could ever
      write (not in any lockfile or script), DI/AI tags nothing reads, touching conveyors without a
      connection (from Milestone 1), composites exposing no I/O, components outside the floor bounds,
      overlapping footprints. Exit 1 on errors, 0 with warnings; CI runs it on every shipped scene.
      *Note (2026-10-26):* `src/openfactory/lint.py`, `openfactory lint SCENE... [--script F.py]`.
      Errors: E100 the scene does not load (this covers an unbound required port, reported as
      `Field required` with its path), E101 a lockfile conflict. Warnings: W201 a DO/AO in no
      lockfile and written by no script, W202 a DI/AI in no lockfile and read by no script,
      W203 conveyors touching end to end without a connection, W204 outside the viewer's
      40 m × 40 m floor, W205 conveyor footprints overlapping at the same height (connected
      pairs exempt). Scripts are scanned as text, never run. The composites check arrived with
      2.16 as W208. `tools/ci_local.sh` step 3 runs `openfactory lint scenes`; the shipped sorter has
      0 errors and 0 warnings.
      *Pusher checks (2026-10-26):* W206 a plate top below the centre of mass of any emitted part
      on the belt the plate sweeps (it tips the part), W207 a stroke shorter than the retracted
      face's distance to that belt's far edge (the part is left straddling the seam). Both came
      from a phone demo where the sorter's 0.15 m plate at y 0.1 tipped 0.4 m boxes and its 0.5 m
      stroke left them across the CONV_01/CONV_02 seam. An unset `plate_height` is now sized from
      the emitted parts. The sorter's pusher moved to y 0.175 with stroke 0.6 (plate 0.25 m,
      y 0.05 to 0.30); pushed boxes now end centred at z 0.45 on CONV_02 instead of 0.35.
      *W207 gap case (2.5b, ruled 2026-11-01):* when a gap separates the swept belt from the
      belt beyond, the stroke must reach the beyond belt's near edge plus half the deepest
      emitted part's depth along the stroke, the stroke that puts the whole part on the belt
      beyond rather than balanced on the seam (the phone-demo fault).
- [ ] 2.21 Lockstep co-simulation with FactoryTalk Logix Echo (added 2026-11-11; §2.4 option (c)
      revived for Echo). *Why:* the 2.2 path polls the controller over EtherNet/IP while both
      sides run free, so a PLC test can come out differently run to run. Echo's SDK can switch an
      emulated controller into co-simulation mode and advance its virtual time step by step
      ("Controller API: use to control the execution, scale time, and access tags", Rockwell's
      Echo v2 overview), which lets OpenFactory and the controller advance together.
      *Split (the rule from CLAUDE.md, "Original Work and Vendor Material"):* everything that
      links Rockwell's SDK lives in HIS Controls' Echo-Tools, outside this repository; this
      repository gets only a vendor-neutral way for an outside program to advance the core.
      *Depends on:* 2.2 passing on Echo (`docs/logix-echo-test.md`), and the owner's choice of
      Echo version. Echo 3.00 ships the SDK for C#/.NET only; Echo 4.00 (10/2025) adds Python
      to the API (4.00 release notes) but needs Windows 11 or Windows Server 2022/2025.
      *First deliverable:* an ADR (§9 rule 7) choosing between:
      (a) **Python, Echo 4.00:** a `--script` stand-in (2.10, `step(io, t)` every tick, kept in
          Echo-Tools) that writes the plant's inputs to the controller, advances it one tick
          and reads its outputs. No OpenFactory change.
      (b) **Out of process, Echo 3.00 or later:** a small external-step interface in
          OpenFactory (loopback by default; beyond loopback only with a token, §6.4): the
          outside program sends the PLC's outputs, the core runs one tick and returns the
          plant's inputs. The C# bridge in Echo-Tools drives it.
      *Facts the design must carry* (Echo SDK documentation installed with Echo; release notes):
      the SDK reaches the Echo service on the same PC only, and the owner runs Echo on a separate
      VM or PC, so either OpenFactory runs on the Echo host or the step interface is opened
      beyond loopback (owner decision); a step runs *at least* the asked time and returns the
      time it actually ran, and Rockwell recommends 10 ms steps against our 20 ms tick, so the
      bridge tracks the difference rather than assuming one step per tick; tags are written as
      controller-scope tags, as 2.2 does (an input injected into a module tag only stays while
      the module is inhibited or faulted); no snapshot can be taken in co-simulation mode; the
      licence server can delay starting an emulated controller by minutes (4.00 release notes),
      so the bridge waits for it rather than failing.
      *Done when:* CI: a fake outside stepper drives the core through the chosen interface and
      two runs give byte-identical I/O traces, the same as the `--script` run of the same
      scene. On the Echo host: the sorter (`sorting-by-height`), with its control program
      ported to Logix from `examples/openplc/sorting-by-height.st`, runs in lockstep for 60 s
      twice, its DO changes land on the same ticks both times, its `checks:` pass, and the time per
      step (SDK call overhead plus the tick) is measured and recorded here.
      *Sources:* FactoryTalk Logix Echo release notes 3.00
      (https://compatibility.rockwellautomation.com/GeneratedReleaseNote.aspx?v1=62836) and
      4.00 (…?v1=66077); Rockwell's Echo v2 overview
      (https://www.controltech.si/doc/news/FactoryTalk_LogixEcho_v2.pdf); Siemens SIMIT –
      FactoryTalk Logix Echo coupling V2.1.4.2, entry 109800094 (a shipping SDK-based coupling;
      Echo and SIMIT on the same PC).

### Milestone 3 — Process simulation (target 3–4 weeks)
- [x] 3.1 Flow network solver (`core/flownet.py`): directed graph of sources, pipes, valves, pumps,
      tanks; per-tick mass balance.
      *Amended 2026-11-07 (ADR-0005, decision 1; the "pressure-free model first" is dropped):*
      an incompressible network solved quasi-statically every tick with the Global Gradient
      Algorithm (EPANET 2.2 §12.1): link flows and junction heads, tanks, supplies and open
      drains as nodes of known head, demand drains as junctions with a pressure-dependent
      demand; dense pure-Python solve, warm-started, converged at 1e-6 relative flow change and
      1 mm head error, at most 40 iterations and 5 status re-solves per tick; forward Euler for
      tank volumes with the tick split where a tank empties or fills. The solve runs right after
      the kinematics in `LoadedScene.step`. The pressure-free case is the junction-free special
      case, tested first. Design and equations: `docs/research/flownet-design.md`.
      *Done when (ruled 2026-11-07):* the tests of the proposal's §8 pass: volume balance every
      tick, two valves in series, a pump against rising head, convergence within the cap on
      every shipped scene, and a `flow-` scene in `tools/bench.py`.
      *Done (2026-11-07):* `src/openfactory/core/flownet.py`. Tanks, fixed-head nodes (supplies,
      open drains) and junctions; resistances (`r·q·|q|`, linear below 3.6 L/h), pumps
      (EPANET's power curve, single-point curves at 133 % shutoff and twice the design flow,
      forward only) and a demand junction's virtual link (Wagner's pressure-dependent demand).
      Link ends resolve to a node, a tank nozzle or another link (an implicit junction `A>B`).
      The Global Gradient solve, status checks (pumps and check valves against reverse flow, a
      dry nozzle against outflow, reopening when heads drive the allowed way), junctions cut
      off from every known head idle; forward Euler with the tick split where a level falls to
      a nozzle it drains through; overflow counted; temperature and ingredient fractions by
      exact balances, plus heater power. Water at 20 °C from NIST SRD 69 (998.21 kg/m³, Cp
      4.1841 kJ/(kg·K)). `LoadedScene` gains `flownet`, built from components' `flow_nodes()`
      and `flow_links()` (none yet: every existing scene has none and is unchanged), solved
      right after the kinematics.
      Tests (`tests/unit/test_flownet.py`, 23): a tank draining through a valve follows
      Torricelli's law within 0.5 % to 95 % of its drain time and stops at empty; a pump into
      a tank's bottom meets its curve every tick (gain = lift + pipe loss within 2 mm), its
      flow falls monotonically and stops at the shutoff head; two valves in series pass
      `sqrt(Δh / (r1 + r2))` and the implicit junction sits at the first valve's drop; parallel
      pipes split as `sqrt(r2 / r1)`; a pump running its sump dry splits the tick, stops and
      restarts on refill; a check valve stops reverse flow; a nozzle above the surface passes
      no outflow and an inlet above it falls freely; a full tank overflows and counts it; a
      demand drain draws its demand and slows to nothing, never pulling a head below its
      outlet; the volume balance closes to 1e-9 m³ every tick for 300 s on a six-link plant
      operated by hand, with junction continuity to 1e-12 m³/s and no cap hit; two runs are
      identical float for float; every flowing link meets its head equation within 1 mm;
      mixing and heater power are exact balances; bad networks fail to build with the reason.
      Removing the tick split or breaking the continuity equation fails them. "Convergence
      within the cap on every shipped scene" iterates every scene with a network, none until
      3.2 and 3.3 add them. `tools/bench.py` gains `flow-net` (the solver alone on 8 tanks, 24
      links and 8 junctions, 0.5 to 0.85 ms a tick here; baseline score 0.0072, added without
      regenerating the conveyor entries). *Amended (ruled 2026-11-08):* `flow-net` is replaced
      by `tank-level-pid` run under its stand-in script for a fixed tick count, so the gate
      covers a real process scene (PLAN 2.9).
- [x] 3.2 Components: `tank`, `valve_onoff`, `valve_control`, `pump`, `pipe`, `heater`, `transmitter`,
      `vfd_motor`
      *Amended 2026-11-07 (ADR-0005):* plus `supply`, `drain` and `junction` (decision 2), with
      the schemas of `docs/research/flownet-design.md` as accepted: link ends may name another
      link (decision 3); a drain with a demand draws it pressure-dependently (decision 4); a
      full tank overflows (decision 5); `cv` or `kv`, `valve_control.position` optional with a
      held `opening` (a hand valve), and its `fail` position triggers only under `float32`,
      which `docs/components.md` says (decision 6). One commit per type, each with a unit test
      and a `docs/components.md` section; the decision 9 lint checks land with the first
      component that needs each.
      *Progress (2026-11-07), one commit per type:*
      - `tank`: `src/openfactory/components/tank.py`; a cylinder or box, nozzles (`bottom`,
        `top`, named), `level`/`temp` AIs and LSH/LSL switches with a 0.02 m deadband,
        `overflowing` state; faults stuck, drift, noisy, misaligned. Viewer
        (`web/src/components/process.ts`): an outlined translucent shell with the liquid at
        `level_m` (alarm yellow while overflowing), legs to the floor when raised; tanks stand
        on the floor in a scene without conveyors. `tests/unit/test_tank.py` (12), two web
        tests; screenshot of a cylinder and a raised box tank looked at.
      - `supply`: a fixed-pressure source node, its delivered flow in its state; tanks now start
        full of water (an ingredient for mixing). Viewer: a flanged stub, green while it
        delivers. `tests/unit/test_supply.py` (2).
      - `drain`: open (atmospheric) or a consumer with `demand_m3h` and a `schedule` (steps,
        optionally repeating), pressure-dependent; a consumer with no link fails to build.
        Viewer: a stub under a funnel. `tests/unit/test_drain.py` (8).
      - `junction`: a node whose head is solved, its gauge pressure in its state; one no link
        reaches fails to load. Viewer: a small fitting. `tests/unit/test_junction.py` (2).
      - `pipe`, the first link: `from`/`to` ends checked at load (a node, `TANK.nozzle`, or a
        link naming this one back), the drawn length through `path` by default, check valve;
        open drains never give liquid back (a link that would draw from one closes). Lint, as
        decision 9 rules, with the first link: E102 (a declared nozzle no link uses), E103 (a
        network part with no tank, supply or open drain), W211 (a tank's level time constant
        under 10 ticks). Viewer: segments through the core's points. `tests/unit/test_pipe.py`
        (16).
      - `valve_onoff`: a held open command, `travel_s`, ZSO/ZSC limits, `cv` or `kv` (shared
        with `valve_control`); faults jammed, slow, stuck. Link-to-link ends are now drawn at
        the downstream link's origin from both sides. Viewer: a body with a handle that turns
        along the line as it opens, joined to its ends. `tests/unit/test_valve_onoff.py` (4).
      - `valve_control`: stem with `stroke_s` rate limit and `lag_s` lag, ZT feedback,
        equal-percentage (closes tight) or linear; `fail` closed/open/last below 0 %, which
        acts only under `float32` (int16_scaled clamps at 0), as `docs/components.md` says
        (ruled 2026-11-07); unbound it is a hand valve at `opening`. Faults jammed, slow,
        drift, noisy. `tests/unit/test_valve_control.py` (10).
      - `pump`: single-point or three-point curve, run and speed with `ramp_s`, `running` the
        auxiliary contact, `flow` over 0 to the zero-head flow, `dry`, shaft power for the VFD;
        faults jammed (seized), slow (worn), stuck, drift, noisy. Lint W210 (design head below
        the static lift its ends reach) lands with it. Viewer: volute and motor, green running.
        `tests/unit/test_pump.py` (8).
      - `heater`: rated power times `power` while `on` and covered (`min_level`), the tank's
        UA loss to ambient; several heaters on one tank add (`Tank.heat_sources`); `temp` reads
        the tank. Faults stuck_off, drift, noisy. Viewer: a coiled element, green while heating
        (ISA-101: red is for alarms). `tests/unit/test_heater.py` (6).
      - `transmitter`: `measures` a tank's level, temperature or fraction, a link's flow, or a
        junction's or consumer drain's pressure, checked at load; `pv` over `range` with units
        per quantity (ports gain `units_from`); `lag_s`, seeded `noise`; faults drift, noisy,
        stuck (top or bottom of range) and the new `signal_fail` (NE 43: 3.6 mA, or 21 mA with
        `high=1`; clamped under int16_scaled). Viewer: a field transmitter head.
        `tests/unit/test_transmitter.py` (10).
      - `vfd_motor`: drives a pump or a belt or roller conveyor through `driven` (the target's
        `run` and `speed` stay unbound: the scene gives `run` a placeholder while the target
        validates and removes it, so the pump and belt schemas are unchanged and undriven ones
        still require `run`); ramp, speed feedback, overload trip on a pump's shaft power,
        reset by edge or by run off; faults jammed (stall, trips) and drift. Viewer: a
        cabinet, door green running and red tripped. `tests/unit/test_vfd_motor.py` (9).
      *Done (2026-11-07):* all eleven types, one commit each, each with its unit tests, a
      `docs/components.md` section, a viewer builder and web test where it draws state, and
      a PLAN §5 row; the decision 9 lint (E102, E103, W210, W211) with the first components
      that needed it. Every existing scene, trace, recording, kit and CONTEXT.md unchanged.
- [x] 3.3 Scenes: `tank-level-onoff`, `tank-level-pid`, `two-tank-cascade`, `batch-mix-heat`
      *Amended 2026-11-07 (ADR-0005, decision 10):* `checks:` gains `level_within`,
      `never_overflow`, `never_dry` and `max_starts`. The first three scenes as in the proposal,
      each with a stand-in script, an OpenPLC ST program (the PID scene uses OpenPLC's PID
      function block), both lockfiles, checks passing at 60 s and 300 s, a guide and a kit.
      Tank switch and transmitter tags follow ISA-5.1 letters so the I/O list reads like a real
      one (`TK_02_LSH_DI`, `TK_02_LSL_DI`, `TK_01_LT_AI`). `batch-mix-heat` follows after.
      *Progress (2026-11-08):*
      - The four check kinds (`src/openfactory/checks.py`, `core/process_watch.py`): a run's
        process checks are fed every tick in constant memory (each band's lowest and highest
        level from its `after_s`, pump starts, ticks run dry), built only when a scene names
        one. CONTEXT.md leaves unused checks out, so shipped CONTEXT.md files and kits are
        unchanged. `tests/unit/test_process_checks.py` (9): each kind passing and failing, a
        run ending before `after_s`, a pump that never started, five load errors.
      - `tank-level-onoff`: a pump lifts from a sump to a head tank on a stand, a hand valve
        drains it back (the water circulates); the PLC starts the pump at LSL and stops it at
        LSH. Stand-in script, OpenPLC program, both lockfiles, guide, kit. Checks pass at 60 s
        and 300 s (level 0.243 to 0.759 m in 0.2 to 0.8; 1 and 6 starts, limit 8; never
        overflowed or ran dry); a no-seal-in program fails `max_starts` (9 starts in 60 s) and
        a stuck LSH fails `never_overflow` (54 L). Building it found the solver's status
        deadlock (fixed in `fb02691`). Not run under OpenPLC: Docker is down.
      - `tank-level-pid`: a supply through an equal-percentage control valve into a tank on a
        stand, a consumer stepping 2 to 4.5 m³/h at 20 s, a level transmitter; the PLC's PI
        (OpenPLC's PID block: error PV − SP, so KP is negative; LIMIT, an LSH interlock holding
        it in manual, a 1 s manual startup for a bumpless start) holds 1.0 m. Checks pass at
        60 s and 300 s (0.989 to 1.009 m in 0.98 to 1.02 after 45 s). As proposed, a P-only
        loop fails the band (0.956 to 0.966 m). An on/off program on this rate-limited valve
        passes (0.985 to 1.008 m): the 10 s stroke turns it into integral action, so a level
        band cannot fail it. *Ruled 2026-11-08:* accepted, with a fifth check kind,
        `max_reversals: {VALVE_OR_PUMP: N}`, counting direction changes of a valve's or pump's
        command (0 to 100 %) by more than a fixed 10 % of the span, and requiring the command to
        move; set on `tank-level-pid` at 15 (the PI turns back 12 times in 300 s, 1 in 60 s: 25 %
        margin), which the on/off program fails (1151 in 60 s, 3390 in 300 s). An on/off program
        with ±10 mm hysteresis stays under the limit for 60 s (6) and fails at 300 s (29). The
        repeating demand schedule first proposed could not separate PI from P-only (a
        PI transient is as large as a P offset), so the scene has one load step and a band from
        45 s. `TIME_TO_REAL` units in OpenPLC's runtime are marked to verify on the first
        OpenPLC run.
      - `two-tank-cascade`: a supply and control valve into an upper tank that drains by gravity
        into the top of a lower one (non-interacting), a consumer stepping 2 to 3 m³/h at
        10 s; cascade control with two OpenPLC PID blocks (outer on the lower level sets the
        upper setpoint, limited 0.05 to 1.6 m; inner positions the valve), the same interlock
        and bumpless start, the outer manual value tracking the upper level. Checks pass at
        60 s and 300 s (0.788 to 0.809 m in 0.78 to 0.82 after 40 s; never overflowed). A
        single PI loop fails the band with every tuning tried (KP −50 to −600 %/m, TR 10 to
        60 s); a too-fast outer loop passes 60 s and rings over 300 s (0.59 to 0.88 m). The
        plant was resized to settle within the minute the 60 s checks allow: a 0.35 m upper
        tank, a 25 mm gravity line, a 0.4 m lower tank, a 1 m³/h step at 10 s.
      The first three scenes are done; the box stays open for `batch-mix-heat`, which follows
      (amended 2026-11-07).
      *Scene redesign accepted (2026-11-08):* one load step instead of a repeating demand,
      bands set for the 60 s run that hold at 300 s, and the resized tanks, as each scene's
      bullet says; recorded as departures in the ADR-0005 appendix
      (`docs/research/flownet-design.md`, "Departures").
      - `batch-mix-heat` (ruled 2026-11-08: with its recipe checks `fractions_within` and
        `temperature_within`, which land first in their own commit): water (15 °C) and a warm
        syrup (80 °C) through on/off valves into an empty 0.4 m mixing tank, a 45 kW heater,
        level and temperature transmitters, a batch-ready lamp. The PLC's sequence: idle, fill
        water to 0.10 m, fill syrup to 0.16 m, heat to 50 °C, hold (on below 49.5 °C); LSH
        closes both valves, the heater needs 0.10 m. Sized so a batch fits the 60 s checks
        (20 L heated 14 K in 25 s). Stand-in script, OpenPLC program (a CASE step chain),
        both lockfiles, guide, kit. Checks pass at 60 s and 300 s: level 0.169 m in 0.15 to
        0.19; syrup 35.54 % in 32 to 39; 50.40 to 50.48 °C (300 s: 49.49 to 50.48) in 48 to
        52 after 45 s; never overflowed. Not run under OpenPLC: Docker is down.
      *Done (2026-11-08):* the four scenes, each with a stand-in script, an OpenPLC program,
      both lockfiles, checks passing at 60 s and 300 s, a guide and a kit; seven check kinds
      added (`level_within`, `never_overflow`, `never_dry`, `max_starts`, `max_reversals`,
      `fractions_within`, `temperature_within`). None has run under OpenPLC yet (Docker down).
      *Follow-up, optional (ruled 2026-11-09, not now):* grade `batch-mix-heat`'s hold. At the
      tank's 15 W/K loss a program that heats once and switches off stays inside the
      temperature band for 300 s, so the hold step is not graded. The heater's size (45 kW for
      20 L) is accepted for the 60 s rule, and the guide says so.
      *Easy-pass tests (recorded 2026-11-08, `tests/integration/test_process_scenes.py`):* each
      shipped program passes its checks for 300 s inside the solver's caps; a P-only loop fails
      `tank-level-pid`'s band; an on/off program holds that band at 60 s and 300 s and fails
      `max_reversals`; one with hysteresis passes `max_reversals` at 60 s and fails it at 300 s;
      a single PI loop fails `two-tank-cascade`'s band; a pump with no seal-in fails
      `tank-level-onoff`'s `max_starts` while its level stays in band; in `batch-mix-heat`,
      water alone (0 % syrup) and both valves at once (50 %) fail `fractions_within`, and no
      heat (38 °C) and a heater left on (53 to 61 °C) fail `temperature_within`.
- [x] 3.4 PlantPAx demo: scene + L5X with `P_AIn`, `P_PIDE`, `P_ValveC`, `P_Motor` instances (via
      `/plantpax-*` skills) + Perspective faceplates. AOI parameter names in this repository come
      only from Rockwell's public PlantPAx library reference manuals: publication PROCES-RM002
      (Library of Process Objects, 4.x objects) and PROCES-RM200 (5.x), cited by publication
      number and revision.
      *Proposal for ruling (2026-11-08, asked for: design only, no code):*
      `docs/research/plantpax-demo-design.md`: the object-to-component map (`P_AIn` on the
      level transmitter, `P_PIDE` the level controller, `P_ValveC` the control valve, `P_Motor`
      an added outlet pump), the parameter sources (RM002 has no parameter tables: they are in
      PROCES-RM013I, April 2023, as embedded workbooks labelled 4.1; names only, each cited and
      checked by the owner against the official copy), what the L5X will and will not contain
      (no AOI definitions, no instance `<Data>`, a first-scan `Init`), the owner's import
      checks, and seven decisions. Waits for the ruling.
      *Amended (ruled 2026-11-09, all seven decisions as recommended):*
      - Sources: parameter names come from PROCES-RM013 (Logic Instructions, 4.x objects; rev I,
        April 2023, library 4.10.06) and PROCES-RM215 (PlantPAx Process Control Instructions,
        5.x), cited by publication number and revision, names only (no tables). RM002 has no
        parameter tables and RM200 is not the 5.x instruction manual. The owner verifies every
        name against the official manuals in Studio 5000 on import.
      - 4.x `P_` AOIs first, library 4.10.03 or later; 5.x later, as its own task.
      - No `/plantpax-*` skills (they call an MCP server this repository bars): a generator in
        this repository, `openfactory export SCENE --format plantpax`, with a source table
        citing each name.
      - Instance tags carry no `<Data>`; configuration is set by a first-scan `Init` routine.
      - The Perspective faceplates move to 3.8.
      - The live run waits on 2.1 and 2.2 (Logix connectivity); the L5X is generated and
        reviewed now.
      - A new scene, `plantpax-tank-level`: `tank-level-pid`'s plant plus an outlet pump, so
        `P_Motor` has a motor.
      *Done when (ruled 2026-11-09):* the `plantpax-tank-level` scene with checks, stand-in
      script, OpenPLC program, both lockfiles and a guide; `export --format plantpax` with its
      source table and tests; a docs page listing what the owner verifies on import. Ticked
      with a note that the live run waits on 2.1/2.2.
      *Done (2026-11-09); the live run waits on 2.1 and 2.2 (Logix connectivity), and the owner
      verifies every parameter name against the official manuals in Studio 5000 on import:*
      - `scenes/plantpax-tank-level.yaml`: `tank-level-pid`'s plant plus a transfer pump
        (`P_01`, 1.5 m³/h at 3 m design, about 2.75 m³/h up to a header) started by the program
        at 20 s as the load step, interlocked by a low level switch at 0.3 m; a consumer draws
        2 m³/h. Checks at 60 s and 300 s: level 0.987 to 1.009 m in 0.98 to 1.02 after 45 s;
        never overflowed or ran dry; 1 pump start (limit 2); 1 and 14 valve reversals (limit
        18, +29 %). Stand-in script, OpenPLC program (the PI with OpenPLC's PID block plus the
        pump), both lockfiles, CONTEXT.md, guide `docs/guides/plantpax-tank-level.md` (two fault
        exercises checked by running them), and a kit `kits/plantpax-tank-level` (every shipped
        scene has one) with a GRAFCET of the OpenPLC program: the loop's modes and the pump as
        two partial GRAFCETs. Lint clean. Not run under OpenPLC (Docker).
      - `openfactory export SCENE --format plantpax` (`src/openfactory/export/plantpax.py`): the
        scene's I/O tags as `--format l5x` writes them, the four instance tags (`DEMO_LT_101`
        `P_AIn`, `DEMO_LIC_101` `P_PIDE`, `DEMO_LV_101` `P_ValveC`, `DEMO_P_101` `P_Motor`)
        with no `<Data>`, three support tags for the load step, and program `PlantPAx_Demo`:
        `MainRoutine` (`Init` on `S:FS` only, `Control` every scan), `Init` (ranges, the PI
        tuning carried over as gain 8 %/% and 10 repeats/min, setpoint, interlock CVs,
        program ownership, automatic) and `Control` (signal order, each object called once).
        Maps for `plantpax-tank-level` and `tank-level-pid` (for 3.8). Interlocks use the
        cited `Inp_IntlkOK` and `Cfg_IntlkCV` on `P_PIDE` and `P_ValveC` (resolving the
        proposal's open question) and `Inp_IntlkOK` on `P_Motor`. `CITED`, the source table:
        47 members, each with "PROCES-RM013I-EN-P, April 2023 (library 4.10.06)", its workbook
        and sheet; names only. Marked `[TBD]`: each call's operand count, the sense of
        `Cfg_Depend` and `Cfg_CtrlAction`, the import of instances without `<Data>`.
      - `docs/plantpax.md`: what the file does and does not contain, the eleven import checks,
        and the source table (generated from the code). `tests/unit/test_plantpax.py`: the
        file well-formed with the I/O tags and the instances (no `<Data>`), every rung
        commented and balanced and naming only declared tags, each object called once, the
        source table exactly the members the rungs name, the page carrying the table as the
        code has it, a scene with no map refused, a deterministic export.
      *Review of 2026-11-10, ruled:* the five fixes accepted (the pump interlock rung, no
      program command in the first scan, the pump-restart note, the RM013 data types, the
      broadcaster). `Cfg_ProgPwrUp` deviation accepted: the file does not write an object's
      internal tags, and it stays `[TBD]` in import check 9 as an optional setting for the
      owner. The ownership sequence as built (configuration only in `Init`, `PCmd_Prog` at the
      end of the first scan's `Control`, `PCmd_Auto` on `Sts_Prog`, `PSet_SP` every scan) is
      accepted.
- [ ] 3.5 MQTT Sparkplug B publisher (`protocols/sparkplug.py`) tested against Ignition 8.3 MQTT Engine
      *Ruled 2026-11-08:* the publisher with a unit test against a local broker in CI, and a
      documented Ignition test deferred to Docker.
      *Progress (2026-11-08), not done (the MQTT Engine test is the Done-when):*
      `src/openfactory/protocols/sparkplug.py`, one Sparkplug B 3.0 edge node per run
      (`run --sparkplug`; broker, group and node in `openfactory.toml [sparkplug]`, default
      `127.0.0.1:1883`, group `OpenFactory`, node the scene's name): CONNECT with the NDEATH
      Will (QoS 1, bdSeq Int64, +1 per CONNECT, wrapping at 255), SUBSCRIBE to NCMD before
      NBIRTH (seq 0, every tag by name and alias, Boolean or Float, bdSeq, Node
      Control/Rebirth), NDATA of changed metrics by alias every 0.5 s (seq +1, wrapping 255 to
      0), a rebirth on request, NDEATH at QoS 1 on a clean stop, a retry every 2 s. Read-only:
      any NCMD but Rebirth is logged and ignored. No new dependency: the payload's protobuf
      encoding is written from the field numbers of `sparkplug_b.proto` (EPL-2.0, not copied)
      and the public wire format, and the MQTT 3.1.1 client from the OASIS standard. The first
      plan, paho-mqtt (its BSD-3 option) with amqtt as the test broker, stopped: running amqtt
      through `uvx` was refused by the session's permission classifier, so no third-party
      package was added or run. Tests: `tests/unit/test_sparkplug.py` (10: payload,
      `bdSeq`, CONNECT, SUBSCRIBE and PUBLISH bytes worked out by hand from the specifications,
      seq wrapping, the birth's metrics, bad ids); `tests/integration/test_sparkplug_broker.py`
      (3) against `tests/integration/mqtt_test_broker.py`, a broker written separately from
      the publisher that parses every packet itself: the session order and flags (0x0E), the
      Will, NBIRTH and NDATA, a rebirth, a command ignored, the Will on a dropped link and
      bdSeq 1 on the reconnect, NDEATH at QoS 1 and DISCONNECT on a clean stop, a broker that is
      down, and `run --sparkplug` end to end. Writing them found one client bug (the CONNACK
      type read from the whole first byte). *Not done:* interop with a third-party broker and
      with MQTT Engine; the procedure is in `docs/ignition.md`, "Test with Ignition", with the
      Engine's tag path marked `[TBD]`.
      *Outside check (ruled 2026-11-09):* paho-mqtt 2.1.0 is a dev-group dependency, used under
      its EDL-1.0 (BSD-3-Clause) option and recorded in THIRD_PARTY.md; CI installs Mosquitto
      with `apt-get`. `tests/integration/test_sparkplug_mosquitto.py`: our node publishes
      through Mosquitto and paho receives, each payload decoded by OpenFactory's decoder and by
      the hand-written broker test's independent parser (NBIRTH not retained, NDATA by alias,
      the Will on an aborted link, bdSeq 1 on the reconnect, the clean stop's NDEATH); paho
      publishes a rebirth request the node acts on and a payload a bare OpenFactory client
      decodes. The hand-written broker test stays. Mosquitto is not installed in this
      workspace, so these two tests skip here and first run in CI (both pass here with paho
      against the hand-written broker in place of Mosquitto); a local `ci_local.sh` now needs
      Mosquitto installed.
- [x] 3.6 Trend endpoint `/api/history?tag=` ring buffer so the viewer can show a 10-minute trend
      *Done (2026-11-08; ruled: a ring buffer and a 10-minute trend panel for any AI tag, shown
      on the PID scene):* `src/openfactory/core/history.py`, `TagHistory`: one ring buffer per
      AI tag (1201 samples, ten minutes at 2 Hz of simulated time, so memory is fixed by the
      tag count and the rate does not follow the tick rate or run speed), fed after every tick
      by the runtime. `GET /api/history?tag=TAG[&seconds=S]` returns `{tag, units, range,
      period_s, window_s, t, v}`; 404 with the reason for a DI, DO, AO or unknown tag and in a
      replay, 400 for `seconds` <= 0. Viewer (`web/src/trend.ts`): a **Trend** button in any
      scene with AI tags opens a panel with the scene's AI tags, polling once a second and
      drawing an SVG line in ISA-101 highlight blue, the x axis the history held up to ten
      minutes, the y axis fitted to the samples (a floor of 1 % of the tag's range, so a steady
      reading is flat and a 2 cm dip reads on a 2 m level); `?trend=TAG` opens it on a tag.
      Shown on `tank-level-pid` (screenshot at 65 s: the dip to about 0.98 m after the 20 s load step
      and the recovery); the PID guide and the guides index say how to open it.
      Tests: `tests/unit/test_history.py` (8: the 2 Hz grid at 50 Hz and at 10 Hz ticks, the
      ring dropping the oldest past ten minutes, the endpoint serving the level the PLC reads
      with the load-step dip, five bad requests); `web/src/trend.test.ts` (4);
      `tests/integration/test_trend_panel.py` (the panel opened by `?trend=` on a live run,
      only AI tags offered, the line drawn from the endpoint, the button closing it).
- [x] 3.7 Per-tag `range` and `units` override in the binding form (closes the §8 analog-range risk)
      before any 3.2 component lands.
      *Done (2026-11-07):* an AI or AO binding may carry `range: [lo, hi]`, the tag's
      engineering span in the port's units (`AnalogRange` in `core/schema.py`), and `units`,
      for a port that declares none (a port's own units cannot be converted: a load error says
      so). The tag takes them, so `int16_scaled` scaling, the lockfile's `ranges:`, the I/O
      list CSV, CONTEXT.md and the Ignition export all show the binding's span; the component
      keeps its port's physical range, so `speed: {tag: X_AO, range: [0, 50]}` runs a belt at
      half speed at full scale. Load errors: lo not below hi, `range`/`units` with `window`, an
      `initial` outside the binding's range. `tests/scenes/range-override.yaml` (belt speed 0
      to 50 %, a distance 0 to 0.5 m on a 0.8 m beam) and `tests/unit/test_range_override.py`:
      raw 32767 decodes to 50 % and the belt runs 0.25 m/s; 0.25 m encodes to 16384; the CSV
      and CONTEXT.md rows show `0 to 50` and `0 to 0.5`; the lockfile records both, and a
      changed range fails it. Every shipped scene's CSV, lockfiles and CONTEXT.md unchanged.
      *Amended (ruled 2026-11-08): `units` is a label only.* It overrides the units column of
      the I/O list CSV and CONTEXT.md and nothing else (`TagBinding.units_label`): the tag, the
      Ignition export, the L5X stub and the component keep the port's units, and the load error
      for units that differ from the port's is gone (a label converts nothing, so there is
      nothing to refuse). `docs/scene-format.md` says so. Tests: a `units: mH2O` label on a
      level in m shows in the CSV and CONTEXT.md, while the tag, the Ignition `engUnit` and the
      L5X description keep `m`.
- **Owner's open items** (collected 2026-11-12):
  - Legal review of the original-work statement and contributor terms (§9 rule 12): the wording
    is the owner's draft.
- **Housekeeping** (collected 2026-11-12):
  - A stale worktree of an implementer session of 2026-11-09 (detached at `080f744`, in that
    session's scratch folder) holds an uncommitted PLAN edit already in main (`530f1ce`); the
    owner removes it.
- **Docker items** (waiting for Docker; collected 2026-11-09):
  - Each process scene under OpenPLC for 60 s and 300 s (`tank-level-onoff`, `tank-level-pid`,
    `two-tank-cascade`, `batch-mix-heat`, and `plantpax-tank-level`'s OpenPLC program), with
    `TIME_TO_REAL`'s unit checked on the first run (the PID gains assume seconds).
  - A CI step that compiles every `examples/openplc/*.st` with OpenPLC's compiler (matiec,
    `iec2c`) in its container, so a program that does not compile fails CI (ruled 2026-11-09;
    it would have caught `batch-mix-heat`'s `STEP`, an IEC 61131-3 keyword).
  - The Ignition 8.3 MQTT Engine test of 3.5 (`docs/ignition.md`, "Test with Ignition").
  - The OPC UA and Ignition checks of 2.4 against a running gateway.
  - Recapture the README image `docs/media/sorting-by-height-openplc.png` and the video it links
    from an OpenPLC run at viewer quality standard (PLAN 4.14, ruled 2026-11-12).
- [x] 3.8 Process demo kit (`kits/tank-level-pid/`) built with 2.14, including PlantPAx L5X and
      Perspective faceplates from 3.4.
      *Amended (ruled 2026-11-09):* the Perspective faceplates move here from 3.4. *Done when:*
      the kit for `tank-level-pid` with Ignition UDTs and Perspective faceplates whose members
      use the cited names, GRAFCET, CONTEXT.md, and `build_kit --check` passing.
      *Done (2026-11-09):* `src/openfactory/export/plantpax_hmi.py`, `export --format
      plantpax-udts` (Ignition 8.3: `<AOI>_UDT` per object, members exactly the cited names,
      read-only, `ns=1;s=[{Device}]{InstanceName}.<Member>` with the device alias a parameter,
      history on `P_AIn.Val` only, instances in `DEMO`) and `--format plantpax-faceplates`
      (one Perspective view per object, bound only through `{Root}`, ISA-101 colors, display
      only: the operator `OCmd_`/`OSet_` names are not cited yet). `tools/build_kit.py` adds
      the PlantPAx L5X, the UDTs and the faceplates to every kit whose scene has a PlantPAx map:
      `kits/tank-level-pid/` (three objects; with its GRAFCET and CONTEXT.md from 2.8 and 2.19)
      and `kits/plantpax-tank-level/` (four). `build_kit --check` passes.
      `tests/unit/test_plantpax_hmi.py` (6): each UDT's members are exactly its object's cited
      names, paths through the device alias with no address, history on the PV only, instances
      as mapped; every faceplate binding is `{Root}` plus a cited member (or the tag's
      documentation), each view says it is display only and has no button. Not loaded into a
      gateway or Designer yet (Docker items); PE review pending.
      *Follow-up (ruled 2026-11-10, not now):* the faceplates stay display only until the
      status names are cited. Then cite the `Sts_`, `Val_` and `Alm_` members each object needs
      from RM013's workbooks (as for `CITED`, names and types only) so every faceplate shows its
      object's mode, running state and alarms; operator commands (`OCmd_`/`OSet_`) remain a
      separate ruling.
      *Done (2026-11-10, ruled the same day: mode, owner, running or position state, interlock
      state and alarms on the four faceplates, still display only through `{Root}`, alarms in
      the UDTs unconfigured):* `plantpax.DISPLAY` cites 45 output members (`Sts_`, `Val_`,
      `Alm_`), names and types only, from the same RM013 workbooks (each checked against its
      workbook row and sheet); the source table in `docs/plantpax.md` lists them with a "Used
      by" column. `P_AIn` has no command source (no `Sts_Prog` or `Val_Owner` in its lists), so
      its faceplate says so. The UDTs gain the members (`Val_Owner` DINT as `Int4`) and no
      Ignition alarm. Faceplates: the mode from `Sts_OoS`/`Sts_Prog`/`Sts_Oper` (and the loop
      mode `Sts_Casc`/`Sts_Auto`/`Sts_Man`), the owner, the motor's run state, the valve's
      `Val_Pos`, `Sts_IntlkTrip`, and an alarm line naming the `Alm_` bits that are on, red
      while any is. `tests/unit/test_plantpax_hmi.py`: every binding is `{Root}` plus a cited
      member and every displayed member is bound; every faceplate citation is shown; each UDT
      member's type; no UDT alarms; the four faceplates show mode, owner, state, interlock and
      alarms, with no input component. Both kits rebuilt. Expressions not yet checked in a
      Designer.

### Milestone 4 — Editor, assets, community (ongoing)
- [ ] 4.1 In-browser scene editor: drag components, edit properties with schema-driven forms, export YAML.
      Must support placing and editing composite types (2.16) and assigning meshes (2.15) from day one.
      Components declare typed snap ports (`load` for conveyor ends, `pipe`, `mount`) so the editor
      can snap end to end and the lint (2.20) can check them.
      *Design proposal (2026-11-10, for ruling; no code):* `docs/research/scene-editor-design.md`.
      First version: `openfactory edit SCENE` (simulation paused, loopback only), select, a
      form built from the JSON Schema, move and turn on the floor, add from a palette of
      primitives and composite types, delete, Save. Edits are operations the server applies
      to the YAML text through the 4.7 `Document` (extended with set, add and delete splices),
      then loads and lints; Save is refused if the file changed on disk. Typed snap ports
      (`load`, `pipe`, `mount`) declared in Python and published in `/api/scene`, with new
      lint checks on them. Proposed tasks 4.1a-d with a Done-when each, and nine decisions
      for the owner.
      *Ruled 2026-11-10, all nine decisions as recommended:* (1) edits applied on the server
      through the 4.7 `Document`; (2) a separate `openfactory edit` command, simulation paused,
      loopback only, never with a PLC; (3) single-field checks in the browser, the server the
      authority, no `ajv`; (4) the comment lines directly above a deleted component go with
      it; (5) new ids from the type's prefix and the next free number, a description required
      before Save, I/O tags proposed for the user to accept or rename; (6) the editor never
      writes lockfiles; (7) capture within 0.15 m, aligned within 1 mm and 0.1 degrees in the
      editor and 1 to 2 cm in the lint, the snap-port check replacing W203; (8) composite
      instances only in the first version; (9) the order 4.1a, b, c, d. Split as below.
  - [x] 4.1a Edit operations and the edit server: the `Document` operations, the value
        emitter, `openfactory edit` with preview and save (hash check, loopback only).
        *Done when:* pytest applies set, move, add, delete and their inverses to every shipped
        scene and gets the original bytes back with every comment kept; preview returns the
        lint's findings; a save against a changed file is refused.
        *Done (2026-11-10):* `Document` (4.7) gains `splice`, `set_node`, `add_key`,
        `delete_key`, `append_item`, `delete_item`, each a splice at composer positions (a
        block node's end taken from its last leaf, so trailing comment lines are never
        swallowed; a deleted item takes the comment lines directly above it).
        `core/sceneedit.py`: the operations (`set`, `set_raw`, `unset`, `move`, `add`,
        `delete`; paths name components by id), the value emitter in the shipped style, and
        `apply_ops`, which returns each operation's exact inverse. `server/editor.py`:
        `EditSession` with `GET /api/edit/source`, `POST /api/edit/preview` (the text, the
        lint's findings on it as if it were the file, the scene document or null) and `POST
        /api/edit/save` (409 when the base is not the file on disk now, 422 on a lint error,
        400 for a bad op; lockfiles never written); `/api/scene` serves the edited document
        with `mode: "edit"`. `openfactory edit SCENE`: loopback only, the runtime never
        started, no protocol server. `lint_scene(path, text=...)` lints text as if it were the
        file. Tests: `test_sceneedit.py` (17), on all 12 shipped scenes set, move, add and
        delete each load with the change, keep every comment outside the edit (a delete loses
        only its own leading comments), leave the other components equal, and undo to the
        original bytes, singly and all four together; two scenes (`tank-level-onoff`, a closed
        flow loop, and `accumulation-demo`) have no component that deletes without a dangling
        reference, so their delete is checked as text and its load error asserted to name the
        component (removing the references is 4.1c's). `test_edit_server.py` (6): preview
        with W204 and E100 findings and nothing written; save changing one line with its
        inline comment in place and the lockfiles untouched; 409 after a hand edit on disk,
        which survives; 422 and 400 writing nothing; no edit routes in a normal run.
  - [x] 4.1b Selection and the property form: edit mode in the viewer, click to select, the
        schema-driven form, live preview.
        *Done when:* vitest builds a form for every component type in the shipped schema with
        no unexpected fallback, and a Playwright test changes a conveyor's speed and saves: the
        file's diff is that one line.
        *Done (2026-11-10):* `web/src/editor/forms.ts` builds a component's form from
        `scene.schema.json`: text (pattern, length), number (integer, limits), boolean, enum,
        3-vector, optional (a "not set" state), I/O tag binding, nested group, and a YAML text
        field for anything else (sent as `set_raw`); `checkField` checks one value from the
        schema's keywords, `opFor` makes the operation. `web/src/editor/panel.ts`: the panel in
        edit mode (`mode: "edit"` in `/api/scene`): click a component (a composite's part
        selects its instance) and it is outlined in the highlight blue; its form shows the
        file's values (`data` from `/api/edit/source`); an accepted change is previewed (all
        operations against the base), the findings listed and the plant redrawn from the
        preview's document; Save and Revert. The id and type are read only (renames are
        4.1c's), and a binding written as a mapping shows its tag read only. No viewer
        physics in edit mode. `forms.test.ts` (5): all 29 component types build a form with
        only the pinned fallbacks (`mesh.scale` everywhere; `BoxEmitter.parts`,
        `Tank.nozzles`, `Drain.schedule`, `Pipe.path`, `Pump.curve`, a composite instance's
        `io`); every type has id, type, description and position controls; the conveyor's
        limits; the operations. `tests/integration/test_editor.py`: `openfactory edit` on a
        copy of the sorter, a real click on CONV_01, `-1` refused in the browser and never sent,
        `0.6` previewed with nothing written, Save: the file's diff is that one line (its inline
        comment in place) and the lockfiles are untouched. Edit-mode screenshot looked at.
        *Noted for 4.1c:* the panel covers the right edge of the plant (the camera framing does
        not allow for it); bindings written as mappings are read only.
  - [x] 4.1c Placement: palette (primitives and composite types, the scene's and the
        library's), drag and turn on the floor, delete, the `mesh` field, Download YAML.
        *Done when:* a Playwright test adds a photo eye and a library composite instance, moves
        and turns them, sets a mesh, saves; the file validates, the lint shows no new error, and
        the screenshot is looked at.
        *Done (2026-11-11):* `src/openfactory/core/templates.py`: the palette (the 29 built-in
        types, the scene's composite types, and every `_library/` composite beside the scene or
        above it, the loader's own search) and new components as ruled (decision 5): the id from
        the type's prefix and the next free number (`PE_02`), a tag proposed for every required
        port (`PE_02_DI`, `ACC_01_RELEASE_DO`; optional ports stay unbound), starter values for
        required parameters, a reference proposed where the scene has an obvious one (a
        heater's tank, a conveyor with no `run` binding for a VFD, a tank's level for a
        transmitter) else left empty for the user, and a placeholder description. A new
        operation, `use_library`, adds `{library: name}` to `composites:` (after the last entry,
        in a flow list, or as a new block just above `components:`). Server: `POST
        /api/edit/palette` (with the `.glb` files beside the scene), `POST /api/edit/new`, and
        `/models/<id>.glb` serves the mesh of the scene as last previewed (the URL carries the
        file name so a changed file is not taken from the cache). Viewer: the panel's palette
        and Add (placed on the floor at the centre of the uncovered view), Move and Turn with
        three's `TransformControls` on the handle at the file's position (floor axes only; the
        vertical axis only; a 0.05 m and 15 degree grid, Shift for free; a composite instance's
        parts follow its handle), Delete (button or key; a component not saved yet takes back
        its add and changes, `web/src/editor/ops.ts`), the mesh picker, Download YAML. Save and
        Download stay disabled until each new component has its own description. Previews no
        longer reframe the camera. *4.1b's notes, closed:* the view's centre is shifted to the
        middle of the part of the window the panel does not cover (a camera view offset), and a
        binding written as a mapping is edited as one line of YAML. `docs/editor.md` (new, in the
        docs site). Tests: `tests/unit/test_templates.py` (35: every type's new component loads
        in a scene, except the four that name pipe ends and a VFD with nothing free to drive,
        whose load error names the empty field, and a junction, which needs a pipe; ids, tags,
        `use_library` on block and flow lists with its inverse), `tests/unit/test_edit_server.py`
        (+2: palette and new against pending operations; a mesh set in a preview is served),
        `web/src/editor/ops.test.ts` (4), and `tests/integration/test_editor_place.py`: on a copy
        of the sorter with the library and a mesh beside it, a pushbutton added and deleted
        leaves nothing; a photo eye and an accumulation conveyor added from the palette, Save
        disabled until each is described, each moved and turned by real mouse drags on the
        gizmo (on the grids); the pusher's mesh set and drawn; CONV_01's speed binding edited as
        YAML (its inline comment kept); saved: the file validates, the lint has no errors before
        or after, the download equals the saved file, the lockfiles are untouched. Screenshots
        `tools/screenshots/editor-4.1c.png` and `editor-4.1b.png` looked at.
        *Not done:* renaming a component, and deleting the references to a deleted one with it
        (a referenced component's delete shows the load error); a new component is placed at
        the centre of the view even if that overlaps the plant (the lint's W205 says so).
        *Review of 2026-11-12, ruled:* 4.1a to 4.1c stand as ticked (the reviewer read the edit
        server: it writes only the one scene file, refuses a stale save, serves only declared
        meshes), with seven fixes, one commit each: (1) the library conveyor's turn checked in
        the saved file; (2) gizmo colors outside the ISA-101 table, more than 40 levels from
        each (`web/src/editor/gizmo.ts`, `docs/editor.md`); (3) the status lines wrap clear of
        the panel at 640 px; (4) tests write screenshots to their own folder; (5) library
        composites looked up no higher than the project root (`composite.library_folders`);
        (6) writes from another site's page refused: Origin checked (403), JSON only (415), no
        CORS headers; (7) a mesh outside the scene's folder refused at preview (E104 for a
        symlink out; `..` and absolute paths were already E100). *Ruled the same day:* 4.1c was
        ticked by its implementing session before the reviewer saw it; from now on a task that
        another session ticks is reported to the reviewer before anything is built on it.
        Its two open items became 4.1e and 4.1f below.
  - [x] 4.1d Snap ports: ports declared in Python and published, snapping in the editor, the
        new lint checks, the ports in `docs/components.md`.
        *Done when:* a Playwright test drags a conveyor near another's discharge and it snaps
        (ends within 1 mm, headings equal), the connection is added, and the lint is clean; each
        new lint code has a test that fails without it.
        *Scope (ruled 2026-11-12):* `load` ports now; `pipe` and `mount` ports move to 4.1g.
        *Done (2026-11-12):* `src/openfactory/core/snaps.py`: each conveyor's two ends as `load`
        ports (`infeed`, `discharge`; a lift's also at the top, a turntable's also turned), at
        belt-top height, facing out, with a role (`in`, `out`, `both` on a reversible belt).
        `/api/scene` publishes them as `snaps` on each conveyor; recordings leave them out, so
        recordings and kits are unchanged. Editor (`web/src/editor/snap.ts`, pure maths): while
        a conveyor is moved, an end within 0.15 m of an end it can join, at the same height, is
        marked (`web/src/editor/markers.ts`: a ring and a dot, edit overlays, the gizmo's light
        pink); on release the conveyor is placed so the ends meet and face exactly, written to
        0.1 mm and 0.0001 degrees, and a new edit operation, `connect`, adds `{from, to}` to
        `connections:` (a new block at the end of the file if there is none). Lint: W212 (two
        ends within 2 cm with no connection) replaces W203, and W213 (a connection whose ends
        are more than 1 cm apart, out of level, or more than 1 degree from facing) is new. *One
        departure from the 2026-11-10 wording:* W212 compares distance and height only, not
        headings, as W203 did, because a handoff through a turn (an end on a perpendicular
        conveyor's infeed) needs a connection too and W203 had a test for it; facing still
        decides W213 and the editor's snap. No shipped or test scene (32) has a W212 or W213,
        before or after. Tests: `tests/integration/test_editor_snap.py` (two belts 0.3 m
        apart, CONV_02 dragged back by real mouse moves on the gizmo: the marker shows, it
        snaps, the ends meet within 1 mm and face within 0.1 degrees, `{from: CONV_01, to:
        CONV_02}` is added, Save, the lint is empty before and after; screenshots looked at);
        `tests/unit/test_snaps.py` (22: the ports of each type, `/api/scene` publishes them and
        a recording does not, W212 and W213 cases; without W212 three fail, without W213
        five); `web/src/editor/snap.test.ts` (5), `markers.test.ts` (no shadows, no status
        color); `tests/unit/test_sceneedit.py` (+2, `connect` and its inverse);
        `tests/unit/test_component_reference.py` (each conveyor's section lists its snap ports).
        Documented in `docs/editor.md` and `docs/components.md`.
  - [ ] 4.1g Pipe and mount snap ports (added 2026-11-12, split from 4.1d): `pipe` ports (a
        tank's nozzles, a pump's suction and discharge, a valve's and a pipe's ends; snapping
        fills a link's `from`/`to`) and `mount` ports (a conveyor's side rails: a sensor, pusher,
        stopper or diverter takes the rail's height and faces across the belt), with a lint check
        that a mounted device's beam or plate reaches across its belt. Nozzles have only a height
        in the scene today, so `pipe` ports first need nozzle positions (a scene format change,
        with `migrate`). *Done when:* to be set with the owner before it starts.
        *Design proposal (2026-11-14, for ruling, no code):* `docs/research/scene-editor-design.md`,
        "Pipe and mount snap ports" (§9 to §12). In brief: a nozzle may be given a bearing
        (`{height, bearing_deg}`), an optional form, so the format stays 0.1 and `migrate`
        changes nothing (the 0.2 alternative and its migration of each shipped scene are set
        out); pipe ports at nozzles and link origins, snapping by dragging a link's end handles;
        side rails on belt and roller conveyors with a drop rule per device type; W214 (warning,
        1 mm) for an eye's beam, a stopper's blade or a diverter's tip short of the far rail, which
        no shipped scene raises (two-lane-merge's deck eyes would, were decks checked). Proposed
        4.1g1 (mount and W214), 4.1g2 (nozzle bearings and pipe ports), 4.1g3 (pipe snapping),
        each with a Done-when, and nine decisions for the owner.
        *Ruled 2026-11-14:* (1) a value in `nozzles:` is a height or `{height, bearing_deg}`;
        `bottom` may be listed to fix its bearing; (2) additive: the format stays 0.1, no
        migration, every shipped file byte for byte; (3) a link's ports sit at its origin, told
        apart by role; (4) a re-snap that strands a link partner is listed under "Left for you to
        fix"; (5) rails on belt and roller conveyors only, not turntable or lift decks; (6) the
        drop rules of §10.2 as written, including the diverter's swing sign, *amended:* they are
        implemented in Python beside the snap ports in `src/openfactory/core`, as a pure function
        (device type, its parameters, the conveyor, the rail, the distance along it) giving
        `position`, `rotation_deg` and, for a diverter, the swing sign, unit tested there; the
        editor chooses only the rail and the distance and gets the placement from the server,
        because Milestone 5 places devices from a drawing takeoff with the same rules and there
        must be one copy; (7) the mount is not stored; a belt's devices do not follow a moved
        belt in 4.1g; a stored `mounted_on` is looked at again in Milestone 5, not now; (8) W214:
        a warning, 1 mm, photo eyes, stoppers and diverters, pushers staying with W207; (9)
        4.1g1, then 4.1g2; 4.1g3 parked.
  - [x] 4.1g1 Mount ports and W214 (§11 of the proposal, rulings above). *Done when:* a Playwright
        test drops a photo eye, a pusher, a stopper and a diverter beside a belt and each lands by
        §10.2's rule (positions within 1 mm, headings within 0.1 degrees, the diverter's swing
        over the belt); the preview has no W214, W206 or W207; a unit test places both shipped
        pushers at their committed positions by the Python function of ruling 6, and a test shows
        the editor's placement and that function agree for a photo eye, a pusher, a stopper and a
        diverter; each W214 case (beam short by 2 mm, stopper narrower than the belt, diverter
        swinging off the belt, a turntable eye not flagged) has a test that fails without the
        check; the 33 scenes have no W214; a screenshot of a marked rail is looked at.
        *Done (2026-11-15):* `src/openfactory/core/mounts.py`: `rails(conveyor)` gives a belt's
        or roller conveyor's `rail_left` and `rail_right` as `mount` `SnapPort`s (which gain a
        `length`, the rail's run, 0 for a point port), published in `/api/scene` beside the
        `load` ports; `place_on_rail(type, params, conveyor, rail, along, tallest_part)` is the
        one copy of the drop rules (ruling 6), returning `position`, `rotation_deg` and a
        diverter's swing sign; `short_of_belt` is W214. The server answers `POST
        /api/edit/mount {base, ops, id, conveyor, rail, along}` with that function's placement
        (and `swing_deg` when its sign must change); the editor (`web/src/editor/mount.ts`, pure
        maths) finds the rail while a mountable device is dragged (over the belt or within 0.15 m
        beside it, the nearer rail, along it on the 0.05 m grid, Shift for free), marks it, and
        on release asks the server and sends the answer as `set` operations. The existing end
        snapping now reads only `load` ports (`loadPorts`). *Decisions:* the pusher's unset
        `plate_height` rule is written so the loader's sizing gives a plate from 0.05 m to 3/4 of
        the tallest part, or its 0.15 m minimum from 0.05 m when the parts are short or none
        (`pusher_height_above`, tested against the loader); a device's origin within the belt
        widened by 0.15 m captures, so an eye or stopper over the belt, whose origin is on the
        centre line, drops too; W214 finds its belt by footprint widened 0.1 m with the belt top
        0 to 1 m below the origin, not `sizing.belt_under`, which wants the point within 1 cm of
        the belt top. Tests: `tests/unit/test_mounts.py` (11: the rails and no deck rails; both
        shipped pushers placed at their committed positions by `place_on_rail`; the loader sizing
        a placed pusher's plate as the rule expects for 0.4, 0.6, 0.2 m and no parts; every type
        on a belt turned 90 degrees; W214 for a beam 2 mm short, 1 mm and boundary passing, an
        off-centre eye, a 0.4 m stopper, a diverter swinging off or not far enough, a turntable
        eye not flagged where the same eye on a belt is; none in the 33 scenes; the endpoint
        answering with the function's placement and the swing sign), each W214 case failing
        without the check; `tests/integration/test_editor_mount.py` (four devices dragged by real
        mouse moves on the gizmo from beside a belt turned 90 degrees onto its left rail: each
        saved position and heading equal `place_on_rail`'s within 1 mm and 0.1 degrees and the
        rules written out by hand, the diverter's -45 saved as +45, no W214, W206 or W207 on the
        preview); `web/src/editor/mount.test.ts` (4); `test_snaps.py` (rails published, none on a
        deck); `test_component_reference.py` (+1: each railed conveyor lists its mount ports).
        Documented in `docs/components.md` and `docs/editor.md` ("Dropping a device on a rail").
        Screenshots `editor-4.1g1-marked.png` and `editor-4.1g1-dropped.png` looked at.
  - [x] 4.1g2 Nozzle bearings and pipe ports (§9.2, §9.3; rulings 1 to 3). *Done when:* every
        shipped scene loads, draws and runs exactly as before (its state frames and `checks:`
        results equal before and after over a 60 s run; `migrate --check` clean); a tank with
        `bottom: {height: 0, bearing_deg: 90}` draws its pipe end on the +90 degree wall within 1
        mm and the pipe's default length follows; the ports of each process type are in
        `/api/scene` and not in a recording; the scene format and the component reference
        describe both; the editor's `nozzles` control takes a number or a `{height,
        bearing_deg}` group per entry and never falls back to a line of YAML.
        *Done (2026-11-15):* `Tank.nozzles` takes a height or a `NozzleSpec` `{height,
        bearing_deg}` (bearing about +y in the tank's frame from local +x, as `rotation_deg`);
        `bottom` may be listed to fix its bearing (its height must stay 0) and `top` takes none
        (load errors naming each); `Tank.bearings()` beside `all_nozzles()`. The wall point is
        `snaps.nozzle_wall` (a cylinder at its radius, a box where the bearing's ray meets the
        wall), the one copy the tank's drawing (`Tank.nozzle_point`) and the ports use; a nozzle
        without a bearing still faces its link. `snaps.pipe_ports` (ruling 3): a tank's nozzles
        (`top` at the top's centre, a fixed one on the wall facing out, a free one a ring with
        `heading_deg` null and a `radius`), a supply's `outlet`, a drain's `inlet`, a junction's
        `node`, a pipe's and valve's `inlet`/`outlet`, a pump's `suction`/`discharge`, all at the
        origin and told apart by role; published in `/api/scene` with the other ports, not in a
        recording. Format 0.1, no migration (ruling 2); `web/public/scene.schema.json`
        regenerated. Editor: a new `either` field kind for a value of one of a few kinds, each
        with a control (`anyOf` with distinct kinds), offered as a choice above its control;
        switching keeps what it can (a nozzle's height stays when a bearing is added). It also
        gives a mesh's `scale` (one number or three) a control, so the only YAML fallback left is
        a composite instance's `io`. *Checked before and after:* each of the 12 shipped scenes
        run 60 s under its stand-in script with `--record`, at eb8916d and with this change: the
        12 recordings (every state frame and the drawn geometry, link points included) are byte
        for byte the same, and so are the check results. Tests: `tests/unit/test_nozzles.py` (6:
        every shipped file migrates byte for byte at format 0.1; `bottom: {height: 0,
        bearing_deg: 90}` draws PIPE_02's end at (0, 1.55, -0.3) within 1 mm and its default
        length follows the drawn length, against (0.3, 1.55, 0) without; a box tank's ray; the
        load errors; each process type's ports, the ring, the origin; `/api/scene` publishes them
        and a recording does not); `tests/integration/test_editor_nozzles.py` (TK_01's bottom
        nozzle given bearing 90 in the form by its choice and two numbers, no YAML text box
        touched, saved as `{bottom: {height: 0, bearing_deg: 90}}`, PIPE_02 drawn on the -z
        wall); `forms.test.ts` (+3); `test_component_reference.py` (+1: each process type lists
        its pipe ports). Documented in `docs/components.md`, `docs/scene-format.md` and
        `docs/editor.md`. Screenshot `editor-4.1g2-nozzle.png` looked at.
  - [ ] 4.1g3 Pipe snapping in the editor (§9.4; ruling 4). *Parked 2026-11-14, after Milestone
        5's Phase B.* Not to be built before then.
  - [x] 4.1e Free placement on Add (added 2026-11-12, from 4.1c's open items): a new component
        is placed at the nearest free spot to the centre of the view, so it does not overlap the
        plant. *Done when:* adding each palette type to a sorter whose view centre is covered by
        a conveyor gives no W205 (or other overlap finding) on the preview, and a Playwright test
        adds one there and finds it clear of the plant. *Added 2026-11-13 (owner):* a new
        conveyor dropped near an open end snaps to it with the 4.1d ports.
        *Done (2026-11-13):* `web/src/editor/place.ts` (pure maths): once the new component is
        drawn, its footprint (the x-z box of what the viewer drew) with 5 cm around it is
        tested against every other component's; if it overlaps, it moves to the nearest clear
        spot (0.1 m steps, up to 15 m, never past the floor's 20 m edge). A conveyor with an end
        within 1 m of an *open* end (no other end within 2 cm) snaps to it at that end's height
        and the connection is added, unless anything but the conveyor it joins is in the way.
        A component the preview cannot draw yet (it needs a reference) is placed on the first
        preview that draws it; a pipe is never moved. *Decisions:* footprints come from the
        drawn objects, not a Python table, so housings, meshes and composites count as drawn;
        a sensor's beam counts as its footprint, so a new photo eye lands beside a belt, not
        across it; the drop-snap reach is 1 m (a drag's is 0.15 m) because Add has no drag.
        Tests: `tests/integration/test_editor_add.py` (the sorter with the view centred on
        CONV_01's belt: all 29 palette types added in turn, no W205/W212/W213 on any preview,
        each of the 21 drawn clear of every other drawn footprint, the 8 process types that
        need a tank or pipe wait for a reference; a belt dropped 0.67 m past an open discharge
        snaps on at [4, 0, 0] with no W212); `web/src/editor/place.test.ts` (9). Screenshots
        `editor-4.1e-added.png`, `editor-4.1e-snapped.png` looked at.
  - [x] 4.1f Delete with its references (added 2026-11-12, from 4.1c's open items): Delete
        removes or flags every reference to the deleted component (connections, links, composite
        parts, checks), and the preview lists what it will remove before Save. *Done when:* for
        each kind of reference a test deletes a referenced component and the scene still loads
        with that reference removed (or the preview names the reference left to fix), and a
        Playwright test shows the preview's list before Save.
        *Done (2026-11-13):* `src/openfactory/core/references.py` lists every reference to a
        component (and, for a composite instance, to its parts); `POST /api/edit/references`
        serves the list; a new edit operation, `unreference`, removes the `connections:` and
        `checks:` entries (an emptied check, `checks:` or `connections:` goes too), splice by
        splice, so the rest of the file stays byte for byte and the inverse restores it. The
        panel shows "It also removes" and "Left for you to fix" before anything changes; Delete
        ID sends `delete` + `unreference`, Cancel nothing. *Decision:* references a component
        cannot work without (link ends, a heater's tank, a transmitter's `measures`, a VFD's
        `drives`, a combined DI's `from` entry) are listed, not removed or cascaded: deleting a
        tank does not silently delete the pipes, valves and pumps chained to it, and the load
        error names each until it is changed. Tests: `tests/unit/test_references.py` (10: a
        connection, the last connection, a composite's parts, `parts_reached`, an emptied check,
        a tank's four checks and its pipes, heater and transmitter left to fix, a flow-list
        item, a pump's `never_dry`, a VFD and a combined DI; each delete undone byte for byte);
        `tests/integration/test_editor_delete.py` (the sorter: REM_02's list shown, Cancel
        changes nothing, Delete and Save, the saved scene loads with `parts_reached` on REM_01
        only; batch-mix-heat: TK_01's fix list shown, the preview's E100 names TK_01, Save
        refused); `web/src/editor/ops.test.ts` (+1). Screenshot `editor-4.1f-confirm.png`
        looked at.
  - [x] 4.1h A shipped scene built only with the editor (owner, 2026-11-13): start from New and
        build a scene without hand-editing YAML: at least two conveyors joined by snapping, a
        sensor, a pusher and a bin, with a stand-in script and checks that pass; write down what was
        awkward, fix the worst three, ship the scene and add it to the docs gallery with a
        screenshot. *Done when:* the scene is shipped like any other (lockfiles, OpenPLC program,
        stand-in script, guide, checks passing at 60 s and 300 s) and a test rebuilds it in the
        browser from `openfactory new` and saves the same values. *Done (2026-11-13):*
        `scenes/inline-reject.yaml`: two 3 m belts (CONV_02 snapped onto CONV_01's discharge on Add,
        connection added), a box source (good 0.2 m, oversize 0.4 m), a photo eye above the good
        boxes, a pusher that pushes oversize boxes off the side into a reject bin, and a bin at the
        end. Its checks pass at 60 s and 300 s under `examples/pyplc/inline-reject.py` (longest 11.8
        s, limit 15 s); `examples/openplc/inline-reject.st` is its OpenPLC port;
        `docs/guides/inline-reject.md` with `docs/media/inline-reject.png` (looked at).
        `tests/integration/test_editor_build.py` runs the whole session in Chromium (`openfactory
        new`, then palette, Add, form fields, Use buttons, scene settings, Save) and compares the
        saved file's values with the shipped file's; the shipped file is that session's output. The
        file was never opened, but two values were typed as one line of YAML in the form (the
        emitter's `parts`, the `parts_reached` check): the form has no list or mapping control.
        `openfactory new` did not exist when the task began; it had to be built first. The lockfiles
        and CONTEXT.md came from `openfactory export`, and `kits/inline-reject/` from
        `tools/build_kit.py`. The 60 s run under OpenPLC was not recorded (no OpenPLC container on
        this machine). *Awkward, in the order met:* (1) there was no way to start a scene: `edit`
        opens only a scene that loads, and a scene needs a component; (2) the scene's own settings
        and its `checks:` could not be edited at all, and a list or mapping value (the emitter's
        `parts`, `parts_reached`) can only be typed as one line of YAML; (3) optional ports (a
        belt's `running`, a pusher's `extended`, an emitter's `enable`) had to be typed by hand, and
        the emitter, with no `io:` yet, showed "not set" and no way to bind one; (4) a sensor,
        pusher or bin lands clear of the plant (4.1e) and then needs world coordinates typed: there
        is no "on that belt" (mount ports, 4.1g); (5) the view stays framed on the starter belt as
        the plant grows, with no fit-view button; (6) list values (the emitter's `parts`) are one
        line of YAML, shown as JSON; (7) the editor writes no comments (the shipped scenes'
        "measured 11.8 s" note) and no lockfiles, so lint warns W201/W202 until `openfactory export`
        runs once per encoding; (8) typing the value a field already has gives no sign it was taken.
        *Fixed, the worst three:* (1) `openfactory new` (one belt, placeholders that hold Save); (2)
        the scene settings form (`scene:` and `checks:` from the schema; a first check creates the
        block above `components:`); (3) every port listed, a **Use TAG** button with the
        conventional tag, and a first binding creating `io:`. Also (6): list values are shown in
        YAML flow style. Tests: `tests/unit/test_templates.py` (+2), `test_sceneedit.py` (+2),
        `ops.test.ts` (+1), `forms.test.ts` (+1), `tests/integration/test_shipped_scenes.py` (the
        new scene).
  - [x] 4.1i Live checks in the viewer (owner, 2026-11-13): while a scene runs, a panel shows
        each of the scene's checks as pending, passed or failed, updating live, in the ISA-101
        status colors already in the palette; read-only for a trainee, visible in the
        instructor view too. *Done when:* a Playwright test sees a check go from pending to
        passed when the run is graded and another fail live before it is, in the trainee's view
        and the instructor's.
        *Done (2026-11-14):* `openfactory.checks.evaluate` now marks a failure `settled` when
        running longer cannot undo it; `checks.live` gives each check `failed` as soon as it
        is settled, `pending` otherwise, and the graded result from `duration_s` on (the
        runtime grades once, on the tick the run reaches it, exactly as `openfactory test`
        does). `GET /api/checks` serves it; `web/src/checks.ts` polls it once a second and
        draws the panel at the top right (below the exam panel when that is shown). *Decisions:*
        "passed" is shown only when graded, never mid-run, so the panel never shows a pass that
        later turns to a fail; failed is alarm red, pending the stopped grey, passed the normal
        text color (not green: the HIS ISA-101 rule keeps green and red from meaning good and
        bad side by side), and each row says PASS, FAIL or a dash so color is not the only cue;
        a replay and a scene with no checks show no panel. Tests: `tests/unit/test_live_checks.py`
        (3: pending, then graded and frozen, agreeing with `evaluate`; a tall box at the wrong
        remover fails live before 30 s; `/api/checks` is GET only and empty with no checks),
        `web/src/checks.test.ts` (2), `tests/integration/test_checks_panel.py` (2, real time: the
        sorter with a 20 s `duration_s` goes pending to PASS in the viewer and the instructor
        view; a program that never pushes shows FAIL in red at about 11 s, the rest pending).
        Screenshots `checks-4.1i-passed.png`, `checks-4.1i-failed.png` looked at. The instructor page starts it folded to one line below 900 px, as the exam panel (fresh-clone CI caught it covering a fault label at 640 px; `test_instructor_view.py` now checks it).
  - [x] 4.1j Editor leftovers from 4.1h's awkward list (owner, 2026-11-14): (5) a fit-view button
        that frames the whole plant, and the view refits when Add puts a component outside it;
        (2)/(6) a form control for list and mapping values (the emitter's `parts`, a check's
        `parts_reached`) built from the schema, so neither is typed as a line of YAML; (8) typing
        the value a field already has shows that it was taken. *Done when:*
        tests/integration/test_editor_build.py rebuilds inline-reject with no value typed as YAML
        and saves the same values as the shipped file; a Playwright test adds a component outside
        the view, presses fit view and finds every component on screen; a test types an unchanged
        value and sees the acknowledgement; screenshots of each looked at.
        *Done (2026-11-14):* `web/src/editor/forms.ts` gains two field kinds from the schema: a
        `list` (an array with `items`) and a `map` (an object with one `patternProperties`
        pattern), each built only when its items are themselves controls; `anyOf [T, a list of
        T]` is a list that writes one item bare (`parts_reached: {REM_01: oversize}`).
        `checkField` checks them item by item (and groups key by key); `blank` gives a new item
        its schema default or an empty value that `checkField` refuses. `web/src/editor/compound.ts`
        draws them: numbered items with their own fields, **×** and **Add item**; mapping entries
        with their key, **×** and a new-key box with **Add**. The panel writes the whole value as
        one `set` when it checks, and says "Not sent yet" with the unfinished item otherwise. Fit
        view: `framing.fitPose` frames the plant across as well as up and down, in the part of
        the window the panel does not cover (the view offset of 4.1c); `boxInView` tells whether
        a component is wholly in it. The **Fit view** button sits beside Add; after Add places a
        component (4.1e) the view refits only if that component is not wholly in view, so the
        4.1c rule stands that a preview never moves the camera. Acknowledgement: a field whose
        value is typed again fires no `change`, so the panel watches for typing followed by
        leaving the field, and a commit equal to the current value (`0.50` for 0.5) is also
        caught; either shows "✓ Kept: that is already its value" on the field and sends nothing.
        *Decisions:* the edits stay operations applied on the server (one `set` per list or
        mapping, emitted in the scenes' flow style); single-field checks in the browser, the
        server the authority, no `ajv`; the editor writes no lockfile. A half-made item is never
        sent, and is dropped if another change redraws the form first. Still YAML, pinned in
        `forms.test.ts`: `mesh.scale` (a number or three) and a composite instance's `io` (free
        keys of undescribed values); a binding written with options stays one line of YAML.
        The tank's `nozzles`, a drain's `schedule`, a pipe's `path` and a pump's `curve` lost
        their YAML fallback with the same controls, and the scene's `checks:` form has none
        left. Tests: `test_editor_build.py` builds the parts and `parts_reached` with the new
        controls, fails if any text box of YAML receives a keystroke, saves the same values as
        `scenes/inline-reject.yaml` (unchanged), and ends with Fit view and every component in
        view; `tests/integration/test_editor_fit.py` (2: on the sorter zoomed in, a belt added
        outside the view refits by itself, a component typed 12 m away is off screen until Fit
        view brings every component back; a speed typed again key by key shows Kept with no
        operation and no preview, `0.50` for 0.5 too, and a real change still goes through);
        `forms.test.ts` (+4), `framing.test.ts` (+2). Screenshots `editor-4.1j-list.png`,
        `editor-4.1j-mapping.png`, `editor-build-inline-reject.png`, `editor-4.1j-add-refit.png`,
        `editor-4.1j-fit.png`, `editor-4.1j-kept.png` looked at.
        *Fixed (2026-11-14), three faults that lost what the user typed:* (a) renaming a mapping
        key to one another entry has, or to an empty key, was taken, and the mapping was written
        with an entry fewer: now refused as Add refuses a duplicate, the box back to its name with
        the reason, nothing sent; (b) a list item not yet sent was dropped when another change
        redrew the form: the panel now keeps it (`Drafts` in `web/src/editor/compound.ts`, keyed
        by the field's path) until it is sent, removed, its component deleted, or Revert; (c)
        found while testing (b): a list or mapping set whole over one written in block style (the
        sorter's `parts:`) was refused by the server ("a block collection; set its keys", 400),
        so the 4.1j control worked only on flow lists, which inline-reject happens to use. `set`
        now rewrites a block collection as a block, one item per line in flow style
        (`Document.set_block`, an exact inverse); an emptied one is written `[]` or `{}` on its
        key's line. The list and mapping edits are now pure functions (`addItem`, `removeItem`,
        `renameEntry`, `addEntry`, `removeEntry`) that the controls call. Tests:
        `web/src/editor/compound.test.ts` (new, 9: add, remove and rename on a list and a mapping,
        both refusals, drafts kept, copied and cleared); `tests/integration/test_editor_lists.py`
        (2, on the sorter: a rename to REM_02 or to nothing refused with both entries kept and no
        operation, a free name taken; a third part half filled survives a description change and
        its redraw, then finished, sent and saved); both fail with the editor of b6bf8d6 (run:
        the rename's refusal never shows; the item is gone after the redraw);
        `test_sceneedit.py` (+1: the sorter's parts set whole keeps the block, comments and an
        exact undo; a block mapping; an emptied list). Screenshots `editor-fix-rename-refused.png`,
        `editor-fix-draft-kept.png` looked at.
  - [ ] 4.1k The form edits the keys that are YAML-only today (added 2026-11-16, review of
        996e1c8): a binding's `plc_tag` (5.3a) and `lag_s` (5.2b), `scene.area` and
        `plc.heartbeat_plc_tag` (5.3a), the composite io additions in a scene's own `composites:`
        definitions (`command: AO`, `kind: AI`, `range`, `units`, command and io names in a
        `from` expression; 5.2a), `scenery`'s `size` and `color` (5.2c), and a composite
        instance's `mesh` (5.2c). Today each is a YAML line the form keeps as written and does
        not show.
        *Done when:* a Playwright test opens a scene in the editor and, through the form only,
        sets a `plc_tag` of each form (controller, program, member, bit) and sees W215 appear
        and clear as one binding is left without and then given one; sets `lag_s` on an AI
        binding and `scene.area`; adds an AO command and a `kind: AI` io to a scene composite,
        with its range, and an instance binding them; sets a `scenery`'s size and colour and
        sees the box redrawn; picks a mesh for a `variable_item` instance and sees it drawn;
        saves, and the file reloads with each value and every comment it had; a malformed
        `plc_tag`, a `kind: AI` with no range and a mesh on a composite with no scenery part
        are refused in the form with the load error's text, and nothing is written. Screenshots
        looked at.
- [x] 4.2 CC-BY GLTF models for the top ten components; `ATTRIBUTION.md`
      *Defined (ruled 2026-11-07):* models authored by our own scripts or taken from CC0/CC-BY
      sources with attribution, each under the 1 MB rule with a license sidecar, drawn through
      the 2.15 mesh path as the default housing when present; `ATTRIBUTION.md` generated by the
      tool that writes THIRD_PARTY.md's models section; one commit per model. *Done when:* the
      sorter screenshot shows the new conveyor, eye and pusher models, lint is clean, and parity
      traces are unchanged.
      *Design (2026-11-07):* every model is ours, generated by `tools/make_models.py` from boxes
      and cylinders (deterministic; `tests/unit/test_models.py` rebuilds each byte for byte), in
      `web/public/housings/<model>.glb` with its sidecar. The housings are parametric (a
      conveyor's length, width and leg height, a pusher's stroke, a photo eye's beam length),
      so a model is a kit of named parts and each builder lists where each copy goes and how
      far it stretches (`ComponentView.defaultHousing`); `web/src/housings.ts` fetches each file
      once and clones its parts. The defaults live in the viewer, not the scene document, so
      kits, recordings and parity fixtures do not change; a scene `mesh` still wins, and a model
      that fails to load leaves the primitive. `window.__openfactory.housings` reports what drew;
      `tools/screenshot.py --housings TYPE,...` fails unless each listed type drew its model,
      and CI step 7 lists the sorter's types. `tools/third_party.py --models` (and its full run)
      also writes `ATTRIBUTION.md` from the sidecars; `tests/unit/test_mesh.py` keeps it current.
      Top ten by shipped-scene use, leaving out `box_emitter` and `part_remover` (zone outlines,
      no housing): `belt_conveyor`, `photo_eye`, `stopper`, `pusher`, `turntable`, `pushbutton`,
      `pilot_light`, `selector_switch`, `diverter_arm`, and `roller_conveyor` (which shares the
      conveyor frame).
      *Ruled 2026-11-07:* parametric builders, not one static `.glb` per type, accepted: each
      type's housing depends on the scene's dimensions, so a model is a kit of named parts
      that the type's builder places and stretches, and several types may share one model
      (`conveyor_frame`). Nobody should expect `web/public/housings/<type>.glb` for every
      type. `ATTRIBUTION.md` covers the models as our own CC-BY-4.0 work. The top-ten list
      above is accepted. The draw calls the models add (sorter 38 to 87 with three types)
      come back down before 4.2 ships: each component's copies are merged by material,
      *done when* the sorter's draw calls are under 45 with its four housings drawn. No
      reorder: 4.2 and 4.4 wait together until the owner sets the Pages source.
      *Progress:*
      - `conveyor_frame` (14 368 bytes) for `belt_conveyor` and `roller_conveyor`: channel
        rails, flanges outward, over the primitive rails' span; square-tube legs on
        rubber-padded feet with a low brace per pair; for the belt, end drums with bearing
        blocks outside the rails.
      - `photo_eye` (8 956 bytes): a head the primitive's size (0.05 x 0.09 x 0.04 m) with a
        dark face plate, black lens and cable gland, and an angle bracket behind it; both
        heads face the beam. The model hangs in the group a `misaligned` fault moves, which
        the 2.15 mesh path does not (the photo eye has no fixed housing there), so the builder
        names the heads as the parts it replaces.
      - `pusher` (20 488 bytes): a round aluminium barrel (0.04 m radius) and four tie rods
        stretched over the primitive body's span (stroke + 0.1 m), 0.12 m square end caps,
        the front one with the rod gland, both with an air port, and a mounting foot under
        each cap running along the barrel. The plate and rod stay primitives.
      - `stopper` (9 644 bytes): the actuator where the primitive box hangs under the belt,
        0.11 m below its top at the blade's x: a dark flange plate, a square aluminium body, a
        round barrel end and two air ports. Fixed size; the blade stays a primitive. Hidden under
        the belt from the default camera, so its fit is checked by a bounds test (it fills the
        primitive box), not by eye.
      - `turntable` (22 560 bytes): the pedestal from under the platform to the floor: a
        slewing-bearing housing (0.12 m radius), a round column (0.07 m radius) stretched
        between it and the base, and a 0.32 m base plate on a pad with a collar. The
        platform, which shows turning, and the deck stay primitives. No model under 0.1 m of
        pedestal (the primitive draws one from 0.01 m).
      - `diverter_arm` (11 060 bytes): the drive on the pivot axis from the paddle's top up,
        where the primitive pivot post stands: an aluminium hub, a dark right-angle gearbox
        and the motor along -x behind the pivot, away from the arm at rest. Fixed size; the
        arm and paddle, which swing and show status, stay primitives.
      - `pushbutton` (7 428 bytes): the 0.08 m enclosure behind the primitive plate's face, a
        dark legend plate under the operator, and an aluminium guard collar around the cap
        (`_panel` in `tools/make_models.py`, shared with the other operator devices). The cap,
        which shows the button's color, moves when pressed and is what a click presses, stays
        a primitive.
      - `pilot_light` (5 024 bytes): the shared enclosure and legend plate, and a 12-flat
        aluminium bezel ring around the lens. The lens, which lights, stays a primitive.
      - `selector_switch` (9 416 bytes): the shared enclosure and legend plate, a dark bezel
        the primitive bezel's size, and three light index marks on the plate at the a, center
        and b handle positions (45 degrees either side of up). The handle, which an operator
        turns, stays a primitive.
      - Merge by material (ruled 2026-11-07): `assemble` in `web/src/housings.ts` bakes each
        part copy's placement into its geometry and merges a component's copies into one
        mesh per material (a mirrored copy's triangles reversed so it faces outward), the
        materials shared with every component using the model. Sorter at 20 s under its
        script: 33 draw calls with its four housings drawn (38 with primitives, 87 before the
        merge); CI step 7 now fails above 44 (`tools/screenshot.py --max-draw-calls`).
      *Done (2026-11-07):* ten types, nine models (`conveyor_frame` serves both conveyors),
      5-23 KB each, 109 KB in all, every one ours and generated; the sorter screenshot shows
      the conveyor, eye and pusher models (CI step 7 requires them), lint clean, parity
      traces unchanged. Not shipped yet: it waits with 4.4 for the Pages source.
- [x] 4.3 Multi-viewer: one core, many browsers (classroom)
      *Ruled 2026-11-09:* one core, many browsers, each with its own camera, shared faults and
      exam switches, a viewer count in the HUD, tested with ten Playwright pages on one server.
      *Done when:* ten viewers, zero dropped ticks, state frames serialized once.
      *Done (2026-11-09):* `/ws/state` is served by one broadcaster (`server/app.py`): each
      20 Hz frame is serialized once, with `viewers` (the connected count), and the same text is
      sent to every viewer, each send its own task, so a slow viewer skips frames (counted)
      instead of delaying the others (before, each connection serialized whenever its own loop
      found a new snapshot, so ten viewers serialized up to ten times per tick). `GET
      /api/status` adds `viewers`, `frames_serialized`, `frames_sent`, `frames_skipped` and the
      core's live `dropped_ticks` (the scheduler keeps its run's stats as it goes). Viewer: the
      HUD's second line ends `viewers N`; the exam panel follows each frame's I/O, so a switch
      one viewer turns shows on every panel; each browser keeps its own camera (it always did;
      `window.__openfactoryCamera()` reads it). Faults were already the core's.
      `tests/integration/test_multi_viewer.py`: ten Playwright pages on one server running the
      sorter in exam mode under its script: every HUD says `viewers 10`; over 5 s 100 frames
      serialized and 1000 sent, 0 skipped; orbiting one page's camera leaves another's where it
      was; a fault injected through the API is the active fault in all ten browsers' fault menus
      (exam mode hides the 3D fault labels from trainees, by design); a switch turned on one
      page shows ON on all ten; the core dropped 0 ticks and each page's physics dropped 0;
      the count returns to 0 when the browser closes. `test_snapshot_off_tick` and
      `test_server` updated for the broadcaster and the `viewers` key.
      *Review of 2026-11-10 (fix 5):* a failed broadcaster no longer waits for the next
      connection: it logs each failure once and starts again after 1 s (`BROADCAST_RETRY_S`)
      while any viewer is connected. A viewer whose send has been pending more than 5 s
      (`STALLED_SEND_S`) is dropped (its send cancelled, its socket closed with 1008) and
      counted in `/api/status` as `viewers_dropped`. `test_a_failed_broadcaster_restarts_while_
      viewers_stay` (two failures, the same viewer then gets frames, two log lines) and
      `test_a_viewer_stuck_on_a_send_is_dropped_and_the_others_keep_their_frames` (real uvicorn,
      one event loop: the stuck viewer is dropped and closed, the other keeps receiving; fails
      on the old code).
      *Exam panel sync (ruled 2026-11-10: accepted; hold the click if a stale frame can flip
      it):* not seen. A Playwright probe clicked `PE_01_DI` 40 times at 0.4 s intervals on the
      sorter in exam mode, with one viewer and again with ten, recording every change of the
      button's text: every click changed it exactly once (the reply and the next frame wrote
      the same value), no flip back. It can happen by construction, though: the POST reply and
      the state frames use different connections, so a frame serialized just before the switch
      was applied can arrive after the reply. `SwitchHold` (`web/src/exampanel.ts`) now shows
      the clicked value until a frame agrees, or for 2 s at most (a lost click then shows the
      core's value); a failed or contrary reply releases it. `exampanel.test.ts` (3); the probe
      after the change: 20 clicks, no flip.
- [ ] 4.4 Docs site (MkDocs Material), component reference generated from docstrings
      *Defined (ruled 2026-11-06):* MkDocs Material; the component reference generated from
      `docs/components.md` and the registry; guides and ADRs included; built in CI and deployed
      to GitHub Pages on push to main. *Done when:* the site builds with no warnings, every
      shipped scene's guide is a page, and the Pages URL is in the README.
      *Progress (2026-11-06), built, not yet deployed:*
      - `mkdocs.yml` (Material 9.8.0, `mkdocs-material~=9.8.0` in the dev group, its packages
        added to THIRD_PARTY.md, all within ADR-0001): home page `docs/index.md`; the six scene
        guides; the scene format, the component reference, Ignition, the versioning policy and
        "what OpenFactory is not"; the three ADRs, the research notes, and this plan.
        `strict: true`, with link, anchor and omitted-file validation as warnings.
      - `tools/mkdocs_hooks.py` generates one page per registered type at build time (never
        committed), listed in the nav without editing `mkdocs.yml`. Each page joins the type's
        section of `docs/components.md`, the types, defaults and limits from the scene schema,
        the ports and fault kinds from the registry, and the module docstring. A link from a
        page to a repository file outside `docs/` is rewritten to that file on GitHub.
      - CI step 13 runs `mkdocs build --strict`. `.github/workflows/docs.yml` builds the site
        and deploys it to Pages on every push to main. README names
        <https://huntintegrativesolutions.github.io/OpenFactory/>.
      - Evidence: `mkdocs build --strict` exits 0 with no warnings, 17 component type pages;
        `tests/unit/test_docs_site.py` checks every shipped scene's guide is a nav page, every
        `docs/*.md` is in the nav, every registered type gets a page, the link rewriting, and
        the README URL. Looked at the home page and the `pusher` page in Chromium.

      Not ticked: the deployment has not run. It needs a push and the repository's Pages source
      set to "GitHub Actions"; the README URL answers only after the first deploy. Material for
      MkDocs prints at every build that it reaches end of life on 2027-05-05, with Zensical,
      by the same team, as its successor (a printed notice, not a build warning).
      *Ruled 2026-11-07:* the end-of-life banner is not a build warning. The generated type
      pages are the site's component reference: `docs/components.md` is left out of the site
      (`exclude_docs`) and stays in the repository as their source. Its introduction and
      "Conventions" become `reference/components/index.md`, the "Component types" section's
      index, with each category's introduction and links to its types; a link to
      `components.md` from any page lands there, or on the type's page when the anchor names a
      type (`reference_link` in the hooks; the Markdown sources keep their GitHub-readable
      links).
      *Follow-up:* evaluate Zensical (the same team's successor, which reads `mkdocs.yml`)
      before Material's end of life on 2027-05-05: build this site with it, compare the
      warnings and the generated pages, and switch or record why not.
      *Node 24 actions (ruled 2026-11-07, CI only, held with this task):* `docs.yml` moves to
      `checkout@v5`, `setup-uv@v7`, `upload-pages-artifact@v5` and `deploy-pages@v5`, as
      `ci.yml` did, so its first run raises no Node 20 deprecation warning. Each tag's
      `action.yml` read 2026-11-07: checkout v5, setup-uv v7 and deploy-pages v5 declare
      `node24` (deploy-pages v4 declared `node20`); upload-pages-artifact v5 is a composite
      wrapping `upload-artifact` v7.0.0, which declares `node24` (v3 and v4 wrapped
      upload-artifact v4). v5 leaves hidden files out of the artifact; the built site has none.
      The workflow cannot run until the Pages source is set, so this is checked by reading the
      action manifests only.
      *Trigger (ruled 2026-11-09, unblocking the hold):* `docs.yml` runs on `workflow_dispatch`
      only, so the push of the held stack does not fail at a deploy the Pages source cannot
      take yet. Its header says to add `push: branches: [main]` once Settings → Pages → Source
      is "GitHub Actions". 4.4 stays open until the site has deployed.
- [ ] 4.5 Public GitHub release, issue templates, Discussions, first good-first-issues.
      *Release step (ruled 2026-11-06):* every version bump rebuilds all six demo kits
      (`tools/build_kit.py`), because each kit's recording carries the package version in its
      header, and regenerates the JSON Schema, whose `$comment` names the package version
      (`docs/versioning.md`, "Release steps").
- [ ] 4.6 Desktop packaging (PyInstaller + bundled viewer) for Windows users without Python
- [x] 4.7 Scene schema versioning: `openfactory: "0.2"` bumps require `openfactory migrate` to rewrite
      older scenes, with a test corpus of every shipped scene at every past version. Must exist before
      4.5, because a public release freezes the format people depend on.
      *Ruled 2026-11-06:* the package version and the scene format version are independent.
      Milestone 2 added optional keys and component types only, so scenes stay at
      `openfactory: "0.1"` while the package is 0.2.0, and `migrate` waits for the first
      breaking format change (`docs/versioning.md`). The exported schema keeps
      `$id: urn:openfactory:scene:0.1` and names the package release in its `$comment`.
      *Ruled 2026-11-10 (build it now):* the framework, a no-op 0.1 path, and one synthetic
      migration in the tests only that proves a rewrite keeps comments and key order. *Done
      when:* `migrate --check` passes on every shipped scene in CI and the synthetic test
      round-trips.
      *Done (2026-11-10):* `src/openfactory/core/migrate.py`. `MIGRATIONS` is keyed by the
      version a migration starts from and is empty (0.1 has never broken); `migrate_text`
      chains them to `SCHEMA_VERSION`, rewriting `openfactory:` after each, and returns a
      current file byte for byte. Rewrites edit a `Document`: PyYAML's composer gives each
      node's start and end in the text and an edit (`rename_key`, `set_scalar`) splices only
      that span, so comments, key order, quoting and layout stay (no load-and-dump, no new
      dependency). Errors: no version line, a newer version, no migration from a version.
      `openfactory migrate SCENE... [--check]` rewrites in place, or with `--check` writes
      nothing and exits 1 when a file needs it; a bundle is refused with how to do it. CI step 3
      runs `migrate --check scenes scenes/examples scenes/_library` (14 files, all current).
      The corpus lives in `tests/migrate/<version>/` (README there), one folder per version a
      migration starts from, every shipped scene as it was; none yet.
      `tests/unit/test_migrate.py` (6): every shipped scene unchanged byte for byte; the corpus
      folders are exactly the migrations' source versions, each scene migrating to the shipped
      file; `migrate --check` passes on all shipped files; the errors; a two-step chain keeps a
      comment on the version line; and a synthetic 0.1 -> 0.2 migration (scene `description`
      to `summary`, every component's `position` to `at`, one speed rewritten) on
      `conveyor-start-stop.yaml`: every comment kept in order, the same line count, only the
      version, renamed and rewritten lines changed and nothing else on them, key order
      identical under the new names, and its inverse gives the original bytes.
      *Ruled 2026-11-10:* accepted, including the position-based edits with PyYAML (no new
      dependency).
- [x] 4.8 Release hygiene before 4.5: SECURITY.md with a disclosure contact, CODE_OF_CONDUCT.md,
      semantic versioning policy, CHANGELOG.md, SBOM generated from `THIRD_PARTY.md`, and a one-page
      "what OpenFactory is not" statement (not safety-rated, not virtual commissioning, not a PLC).
      *Part 1 (2026-11-06, started as ruled with Milestone 2 otherwise complete):*
      - `SECURITY.md`: private reporting to the README's contact address, or GitHub's private
        advisory form if the repository enables it; supported versions; the security model of
        §6.4 (loopback by default, Modbus and anonymous OPC UA by design, the fault token,
        scenes and bundles as data, scripts as code); what is in and out of scope.
      - `CODE_OF_CONDUCT.md`: the Contributor Covenant 2.1, its official text, contact filled in.
      - Issue forms (bug report, feature request with a clean-room checkbox, blank issues off,
        security reports pointed at SECURITY.md) and a pull-request template with the CI,
        scene, screenshot, PLAN-tick, DCO, 1 MB and clean-room checks. *Renamed 2026-11-12:*
        both checks are now "Original work" (§9 rule 12).
      - `CHANGELOG.md` from `tools/changelog.py`: every commit since the first under 0.2.0,
        grouped by the milestone of the PLAN task it names; a commit that names none follows
        the one before; a milestone with no ticked task, such as 3, is never assigned.
        Regenerated when cutting the release, so not checked in CI.
      - Version 0.2.0 in `src/openfactory/__init__.py` (which `pyproject.toml` reads) and
        `web/package.json`. The six kit recordings are rebuilt: only their header's
        `openfactory` version changed. No tag: the owner tags after the Docker items and the
        video re-recording.

      *Done (2026-11-06, the rest as ruled):*
      - `docs/versioning.md`: semantic versioning from 0.2.0, approved; what bumps major, minor
        and patch (before 1.0.0 a breaking change bumps the minor version and the changelog
        says so); the package version and the scene format version are independent; and the
        release steps, including the kit rebuild (each kit's recording carries the package
        version) and the schema regeneration (its `$comment` names the package version).
      - The scene schema, ruled metadata only: scenes stay at `openfactory: "0.1"`,
        `$id` stays `urn:openfactory:scene:0.1`, and the exported schema's `$comment` names the
        format and the package release (`scene_json_schema()`, `tests/unit/test_schema.py`).
        `docs/scene-format.md` says the two versions are independent.
      - The SBOM: `tools/sbom.py` merges `uv export --format cyclonedx1.5` (runtime closure,
        extras included, no dev tools) and `npm sbom --sbom-format cyclonedx --omit dev` (what
        the viewer bundles) into one CycloneDX 1.5 document in `build/sbom/` (git-ignored,
        never committed). Python components take their license from THIRD_PARTY.md, since
        uv's export has none. `--check` fails on a component missing from THIRD_PARTY.md's
        runtime tables or listed at another version, a missing license or package URL, a
        duplicate `bom-ref`, or a license outside the ADR-0001 allowlist. CI step 12 runs
        it, and the workflow keeps the SBOM as an artifact. 36 Python and 2 npm components,
        check passing; `tests/unit/test_sbom.py` pins what the check catches.
      - `docs/what-openfactory-is-not.md`: not safety-rated, not virtual commissioning, not a
        PLC, not a throughput model, not a plant-network service, not a finished engineering
        deliverable, not a copy of a commercial simulator.
      - The kit-rebuild release step is added to 4.5 as well.
- [ ] 4.9 Dependency upgrade cadence: a quarterly task that bumps three, Rapier, and pymodbus, reruns
      the parity trace and the Modbus round trip, and records the result in `docs/research/`.
      *Why:* Rapier and pymodbus both changed APIs under us during planning (§2.1, 1.7).
- [ ] 4.10 Stretch, on request only: OpenUSD exporter (`export/usd.py`) mapping scene components to
      USD prims so a scene can be opened in NVIDIA Omniverse or any USD viewer for photoreal client
      presentations, plus import of SimReady USD assets as component meshes.
      *Why:* OpenUSD is the industry's converging scene format (Siemens, Rockwell Emulate3D, NVIDIA).
      OpenFactory stays the lightweight logic-and-SCADA layer; USD is the ladder up to the high-end
      tools without building on them (GPU and license requirements rule that out, see ADR-0001).
- [ ] 4.11 Stretch: import Open Industry Project scenes (MIT-licensed flat JSON, see
      `docs/research/landscape.md`) into OpenFactory YAML for the part types we share (conveyors,
      sensors, diverters), with attribution per their license. Lets the two open projects exchange
      layouts instead of competing on them.
- [x] 4.12 Instructor view (deferred from 2.11, ruled 2026-10-30): a separate URL that takes the
      fault token, shows fault markers even in exam mode, and shows the exam switches read-only,
      so an instructor watches what the trainees cannot see without touching their inputs.
      *Ruled 2026-11-10:* build it on the 4.3 broadcaster. *Done when:* a Playwright test with
      one instructor page and two trainee pages shows the marker only on the instructor page,
      the instructor cannot turn a switch, and a wrong token is refused.
      *Done (2026-11-10):* `/instructor` serves the viewer, which reads its path
      (`web/src/instructor.ts`). The token comes from the URL fragment (`#token=`, never sent in
      a request line; kept for the tab, taken off the address bar) or the tab's stored token,
      and is the first message on `WS /ws/instructor`. The server checks it: a configured
      token is always required, loopback too (the stream shows what exam mode hides); with none
      the stream is open on loopback and refused beyond it, like `/api/fault`; a refusal is
      `{"error", "refused": true}` and close 1008. An accepted instructor joins the 4.3
      broadcaster: each frame, while any instructor is connected, a second text is serialized
      once, the trainee frame plus `instructor: true`, `faults` (`{id: kind}`, every active
      fault) and `comms`, and every instructor gets it; `/api/status` adds `instructors`. The
      viewer marks faults from that list, its HUD says INSTRUCTOR, and its exam panel is
      read-only (buttons disabled, no click handler; "(read-only)"). It still follows the
      switches, so a trainee's turn shows there. A refused page says "INSTRUCTOR VIEW REFUSED"
      and does not retry. `tests/integration/test_instructor_view.py`: the sorter in exam mode
      with a token, one instructor page, two trainee pages and one with a wrong token; the wrong
      one is refused with no frame and the server counts one instructor among three viewers;
      `CYL_01 jammed` injected through the API shows its marker on the instructor page and on
      neither trainee page after forty more frames; every instructor button is disabled, a
      forced click leaves `PE_01_DI` off at `/api/exam`; a trainee's turn shows ON on the
      instructor's panel (screenshot looked at: the yellow `CYL_01 ⚠ jammed` label and the
      read-only panel). `tests/unit/test_instructor_socket.py` (3): wrong, missing and malformed
      tokens refused on loopback with 1008; open on loopback with no token, refused beyond it;
      the instructor frame lists the fault the exam-mode trainee frame hides.
      `web/src/instructor.test.ts` (4). Docs: `docs/scene-format.md`, exam mode.
      *Noted, not changed:* on loopback the fault menu and `GET /api/fault` stay open to every
      viewer (§6.4), so a trainee on the same machine can still read faults there; a classroom
      runs beyond loopback with a token. At 640 px wide the exam panel covers the end of the
      HUD's first line, for trainees too.
- [x] 4.13 Viewer framing (found during 4.2, added 2026-11-07): a scene of only operator devices
      must frame them. *Done when:* a screenshot of a scene with one pushbutton and one pilot
      light shows both.
      *Cause:* with no conveyor, `floorFor` guesses the floor 0.75 m below the lowest device,
      and the viewer added that floor point to the bounds it frames. A panel at 1.2 m became a
      box 2 m tall and 0.2 m wide; at 2.1 times its radius both devices sat 27 degrees above
      the view axis, past the 22.5 degree half field of view, and nothing was drawn.
      *Done (2026-11-07):* `web/src/framing.ts` frames the components' own bounds (conveyor
      legs and pedestals already reach the floor in all 25 scenes under `scenes/` and
      `tests/scenes/`, so the floor point changed none of them) and moves the camera back
      along the same direction until every corner of the bounds is within 85 % of the half
      field of view above and below the centre, never closer than the old 2.1 radii. At
      1280 x 720 every one of those 25 scenes keeps its camera exactly. Only the vertical field
      of view is fitted: fitting across too would pull every conveyor scene back about three
      times on a portrait phone, where the line is cut off at the sides today (left for a task
      of its own). `tests/scenes/operator-devices-only.yaml`: both devices and both labels in
      view, 20.8 % non-background (`tools/screenshot.py`, looked at).
      `web/src/framing.test.ts` fails on the old framing (26.6 degrees) and with the floor
      point put back (the camera 3 m off).
      *Follow-up (ruled 2026-11-07, not now):* portrait-phone framing. On a portrait screen the
      horizontal field of view is narrower than the vertical, and conveyor lines are cut off at
      the sides; fitting across too pulls every conveyor scene back about three times there.
- [ ] 4.14 Viewer lighting and materials (ruled 2026-11-11, from the rendering review in
      `docs/research/viewer-rendering-notes.md`). Give the plant a lit, grounded look without
      moving a status color.
      *Why:* the viewer has no environment map, no tone mapping and no shadows. Metal and
      painted parts render flat and the plant floats above the floor. The models are already
      built in code (4.2), so the work is lighting and materials, not geometry.
      *Done when:*
      1. **Status colors are exact at every level.** Every material that carries an ISA-101
         status color is unlit and excluded from tone mapping and from environment reflections,
         at low, standard and high alike (ruled 2026-11-11). A Playwright test samples pixels on a
         running belt, a stopped belt and an alarm indicator (a red pilot light,
         `tests/scenes/operator-devices-only.yaml`) at each of the three levels. Each must be within
         a small tolerance of its hex value in the style table. The tolerance and the reason for it
         are stated here.
      2. **Only solid housings and parts cast shadows.** Outline volumes, beams, labels, helpers
         and selection or edit-mode gizmos cast and receive none. Parts opt in explicitly in
         `web/src/parts.ts`. A test walks the scene graph and asserts this.
      3. **Shadows are soft, and the shadow camera is fitted to the scene's bounds,** so a large
         scene is covered. Screenshots of sorting-by-height, palletizer-lite, tank-level-pid and
         `tests/scenes/operator-devices-only.yaml`, each looked at.
      4. **A quality setting with three levels:**
         - low: today's lighting for everything except status materials, which become exact
           (item 1);
         - standard: tone mapping and the environment, no shadows;
         - high: standard plus shadows.

         It is chosen by a URL parameter and a HUD control and remembered per browser. The
         default is standard until the owner's reading. CI and `tools/screenshot.py` use low
         unless a test asks otherwise, so run times do not move. Status materials change at low
         too, so existing screenshots and any image checks that sample a belt are expected to
         change. Each one that changes is listed here with its before and after values and
         updated in the same commit as the change. The default is recorded below as open for
         ruling.
      5. **The environment comes from three's own `RoomEnvironment`** (MIT, already in the
         package), built once. No HDR file, no new dependency, no new binary. Report the bundle
         size before and after.
      6. **Performance on the software renderer for each level,** at 200 parts on
         `tests/scenes/load-200.yaml`: frame time, render time and draw calls, before and after,
         recorded here. The owner then reads the same numbers on real hardware from the HUD; that
         reading is listed under 2.9's open reading as one visit that settles both.
      7. **Parity traces, kits and recordings are unchanged.** `npm test` and the full
         `tools/ci_local.sh` on a fresh clone pass.

      Not ticked until the owner's GPU reading is in.
      *Note (2026-11-11), from the reverted spike:* sampled from `tools/screenshot.py` images
      of sorting-by-height:

      | Belt | Today's lighting | Spike (tone mapping and environment) | Style table |
      |------|------------------|--------------------------------------|-------------|
      | Running | `#24B457` | `#8BDC93` | `#22C55E` |
      | Stopped | `#8F95A0` | `#C6C9CD` | `#9CA3AF` |

      Today's lighting already misses, because the belt is a lit, shaded
      `MeshStandardMaterial`. An exact status color therefore needs a material that lighting,
      the environment and tone mapping all leave alone at every level, for example unlit with
      `toneMapped` off. That changes how status surfaces look at low as well, and moves the
      existing image checks. *Ruled 2026-11-11:* status materials are unlit at every level, low
      included (items 1 and 4 amended).

      Three r186 (the pinned version) logs that `PCFSoftShadowMap` has been removed and falls
      back to `PCFShadowMap`. Choose the softening from the r186 documentation.
      *Open for ruling:* the default quality level. It stays open until the owner's GPU reading.
      *Candidates, parked (ruled 2026-11-11, no task):*
      - A read-only trainee panel in run mode: a component's ports, tags and faults beside the 3D,
        from facts the core already has (CONTEXT.md, 2.19).
      - Live checks in the viewer during a run: the scene's `checks:` (2.11) shown as they pass or
        fail, not only at the end of `openfactory test`.

      *Progress (2026-11-11), built on branch `viewer-lighting-4.14`, not ticked (waits for the
      owner's GPU reading).* *Accepted 2026-11-12 as built at `57c25df`* (the reviewer sampled
      the six screenshots: running green, stopped grey, lit and unlit amber exact, soft
      shadows, a pressed pushbutton reads as lit) and merged into main; still unticked until
      the GPU reading, and the default level stays open.
      *Merged with 4.1c (2026-11-12):* both behaviours kept in `web/src/main.ts`: a rebuild
      during editing keeps the camera where the user left it and still refits the shadow
      camera; a selection outline around one view or a composite instance stays at the exact
      highlight color. Edit mode's overlays are marked with `editOverlay` (`web/src/lighting.ts`):
      the move and turn gizmo and both selection outlines cast and receive nothing, also when the
      roles are set over the whole scene (`web/src/lighting.test.ts`; the test fails only when
      both `editOverlay` and the transparent-material rule are removed). The palette adds through
      a select and a button, with no drag preview, and the grid snap draws no markers, so there
      is nothing else to mark. `tests/integration/test_status_colors.py` runs edit mode at low,
      standard and high: edit mode never starts the simulation, so stopped grey is the only
      status color it can show, and both of the sorter's belts read `#9CA3AF`, off by 0, at
      every level. The quality control (x 16 to 148 at the bottom left) and the editor panel
      (from x 920 at 1280 px, from x 280 at 640 px) do not overlap; both screenshots looked at
      (`tools/screenshots/4.14-editor-1280.png`, `4.14-editor-640.png`).
      - **Status materials (item 1).** `statusMaterial` and `asStatus` in
        `web/src/components/types.ts` give each status surface a `MeshBasicMaterial` with
        `toneMapped: false`. In r186 `scene.environment` reaches only standard, Lambert and Phong
        materials, and an unlit material receives no shadow.
        - Status meshes: belts, rollers, the turntable platform, pusher plates, stopper blades,
          diverter paddles, gripper pads, sensor beams, lamp lenses, counter segments,
          valve handles, pump volutes, heater elements, drive doors, the fallback marker and
          direction arrows.
        - Outline volumes and the edit-mode selection box are not tone mapped.
        - Equipment that shows a status color only while active switches to a status material
          for it (`showStatus`) and keeps its lit material otherwise: lift posts, supply and
          drain stubs, a tank's liquid while it overflows (then opaque), and a pushbutton's cap
          while pressed.
        - Behaviour changes (*ruled 2026-11-12*):
          - An unlit pilot lamp is a dark tint of its color, one rule for every lamp color:
            unlit = sRGB(0.2 x linear(lit)) per channel. Accepted provisionally; the reviewer
            looks at the screenshot. The rule and each color's result are in
            `docs/components.md`, "Status colors" (the repository has no separate style
            document; `web/src/isa101.ts`, the style table, points there), and a unit test holds
            the code to that table.
          - A pressed pushbutton shows as lit: its cap takes the status material while pressed
            (`showStatus`, as above) and moves its 7.5 mm. Released, it is shaded paint. (Built
            first without the lit state; ruled back: a lit button is a status indication, and
            the travel cannot be seen from a plant view.)
          - Pipes, posts and liquid keep their shading except while showing a status: accepted.

        `tests/integration/test_status_colors.py` samples the per-channel median of a 9 x 9
        patch, at low, standard and high, on CONV_01 forced running and CONV_02 stopped
        (sorting-by-height), on the lit lamp of `tests/scenes/operator-devices-only.yaml`, and
        on that scene's green pushbutton pressed (a copy set `maintained` and clicked in the
        viewer). The lamp is amber (`alarmMedium`), not red, so the test also samples a copy
        recolored red (`alarmHigh`); accepted 2026-11-12. The scene itself is unchanged.
        **Tolerance: 2 levels of 255 per channel.** An unlit, untone-mapped material differs
        from its hex only by color management: sRGB to linear on the CPU, the sRGB output
        transform in the shader, then 8-bit quantisation, which can move a channel by 1. One
        more level covers SwiftShader's float precision. Measured: all 12 samples (2 belts and
        2 lamps, each at 3 levels) were off by 0; the pressed button (2026-11-12) was off by 0
        at all 3 levels, and the released cap read `#137C3A` at low and `#6CD17C` at standard
        and high (73 and 74 levels away), so a released button never reads as pressed.
      - **Shadow roles (item 2).** `applyShadowRoles` in `web/src/lighting.ts` sets the roles:
        - Solid equipment casts and receives shadows.
        - Status meshes cast but do not receive.
        - Outline volumes (transparent) cast and receive nothing, nor do beams and decals
          (`noShadow`), lines, labels, the grid or the selection box.
        - Parts opt in in `web/src/parts.ts`.

        `web/src/lighting.test.ts` walks a scene graph holding every component type (idle and
        active), a part, the floor, the grid and a selection box. Six deliberate breaks each
        fail it: a belt left lit, a beam allowed to cast, outlines allowed to cast, a fixed
        shadow camera, parts not opting in, and the environment rebuilt per level change.
      - **Soft, fitted shadows (item 3).** `PCFShadowMap` with `shadow.radius` 4 on a 2048
        square map. r186's PCF filter spreads its samples by the radius; `PCFSoftShadowMap` is
        removed in r186. The camera is fitted to the bounding sphere of the framed bounds
        extended to the floor, plus 0.5 m. A unit test shows it covers a small cell and a 40 m
        line, and that the reverted spike's fixed 12 m box misses the line.
        Screenshots at high, each looked at (git-ignored, `tools/screenshots/`):
        - `4.14-sorting-by-height-high.png`
        - `4.14-palletizer-lite-high.png`
        - `4.14-tank-level-pid-high.png`
        - `4.14-operator-devices-only-high.png` (lamp lit, button released)
        - `4.14-operator-devices-pressed-lit-high.png` (button pressed, lamp lit; 2026-11-12)
        - `4.14-operator-devices-unlit-high.png` (lamp unlit, for the provisional tint;
          2026-11-12)
        Retaken on 2026-11-12 after the pushbutton change.
      - **Quality setting (item 4).** `web/src/quality.ts`; the control sits at the bottom left
        (the top right and bottom right are taken by the exam, editor and trend panels).
        - Precedence: `?quality=` in the URL (that page only), then the remembered control
          choice, then low in an automated browser, else `DEFAULT_QUALITY`, which is standard.
        - *Ruled 2026-11-12:* the automatic level is accepted on condition that the detection
          lives in one function, is documented, and has a test per branch. It is
          `isAutomatedBrowser` in `web/src/quality.ts`, and it detects exactly one thing:
          `navigator.webdriver` is the boolean `true` (the WebDriver standard's flag for a
          browser under automation; verified only under Playwright's Chromium: with no
          `?quality=` and nothing stored, the flag read `true` and the viewer started at low).
          False, missing, a non-boolean value or no navigator count as a person; headless mode,
          the user agent and the screen size are not looked at. `tools/screenshot.py` (and so
          `test_status_colors.py` and `parity_trace.py --live`) always passes `?quality=`, so
          the address decides there; the check decides for the Playwright tests and tools that
          open the viewer without it: `test_instructor_view`, `test_multi_viewer`,
          `test_editor`, `test_exam_mode`, `test_viewer_reconnect`, `test_trend_panel`,
          `test_comms_menu`, `test_replay` and `tools/tick_drift.py`. Documented in `docs/guides/README.md`, "Viewer quality", and
          in the control's tooltip (`QUALITY_TOOLTIP`). `web/src/quality.test.ts` has one test
          per branch and one tying the tooltip to it.
        - The HUD's frame line names the level.
        - `tools/screenshot.py --quality`, default low.
        - Documented in `docs/guides/README.md`, "Viewer quality".

        Image checks that changed, measured with the exact CI step (`tools/screenshot.py`
        sorting-by-height, 20 s under the stand-in):

        | | Non-background | Near-black | Colour bins | Draw calls |
        |-|----------------|------------|-------------|------------|
        | Before (`origin/main`) | 12.8 % | 0.0 % | 294 | 33 |
        | After (low) | 13.0 % | 0.0 % | 450 | 33 |

        The verdict is OK both times, and the thresholds (at least 2 % foreground, at most 90 %
        near-black) are unchanged. The bins rise with the flat exact belt colors and the new
        control. No check samples a belt pixel.
        The README capture `docs/media/sorting-by-height-openplc.png` (with the `.webm` it
        links) was made with OpenPLC on 2026-10-25 and is not regenerated here: a faithful one
        needs an OpenPLC run. *Ruled 2026-11-12:* left as is; recapturing both is a Docker item.
      - **Environment and bundle (item 5).** three's `RoomEnvironment`, prefiltered once by
        `PMREMGenerator` the first time standard or high is chosen; low never builds it. No HDR
        file, dependency or binary.

        | Bundle | `origin/main` | This branch |
        |--------|---------------|-------------|
        | `index` | 744,456 bytes (193.63 kB gzip) | 752,293 bytes (195.90 kB gzip) |
        | Rapier | 4,335,104 bytes | unchanged |

        `index` grows by 7,837 bytes, 2.27 kB gzipped.
      - **Software-renderer performance (item 6).** Method:
        - `tests/scenes/load-200.yaml` with every DO forced, at 200 parts, 1280 x 720, headless
          Chromium with SwiftShader.
        - Read from `window.__openfactory` 30 s after the viewer reached 200 parts, so the
          120-frame window holds only 200-part frames.
        - Two rounds in opposite order. The machine was shared with another active session, so
          compare levels within this table, not with 2.9's 2026-11-04 numbers.

        | Level | Frame ms | p95 ms | Render ms | State ms | Draw calls |
        |-------|----------|--------|-----------|----------|------------|
        | Before (`origin/main`) | 329.0-344.4 | 600-667 | 6.3-8.6 | 9.3-13.6 | 502 |
        | Low | 308.5-339.7 | 583-667 | 6.5-8.0 | 10.4-14.7 | 502 |
        | Standard | 590.7-592.9 | 1400-1533 | 15.0-22.4 | 14.2-14.9 | 502 |
        | High | 718.5-727.1 | 1850-1883 | 22.7-22.8 | 13.0-14.6 | 762 |

        The before row was measured on a baseline worktree of `origin/main`, removed after these
        numbers were recorded (ruled 2026-11-12). Low is within run-to-run spread of before. Standard costs about 1.8 times the frame on
        SwiftShader, because PBR environment shading is rasterised on the CPU. High adds the
        shadow pass (260 draw calls) for about 2.2 times. A GPU decides the default (the owner's
        reading, 2.9).
      - **Unchanged outputs and CI (item 7).** The full `tools/ci_local.sh` ran on a fresh clone of
        `4788d86` (12th Gen Intel Core i7-1260P, 8 threads, WSL): CI OK, 14 steps in 1012 s.
        - pytest: 1142 passed, 1 warning (Starlette's `httpx` test-client deprecation), no
          skips.
        - `npm test`: 102 passed.
        - Parity traces are current (`parity_trace.py --check`).
        - All 11 kits are current with their recordings (`build_kit.py --check`).
        - The tick-cost gate is ok: load-200 +4.1 %, queue-200 +10.7 %, tank-level-pid -8.8 %
          against the baseline. The core is untouched here.
        - The working tree was unchanged after the run.


### Milestone 5 — Plants from drawings (proposed 2026-11-14; Phase B and 5.14 ruled 2026-11-15)
*Proposed 2026-11-14. Ruled 2026-11-15: Phase B (5.1 to 5.4, split into the sub-tasks below)
and 5.14, with the build order 5.14a, 5.14b, then 5.3a, 5.2a, 5.2b, 5.2c, 5.1a, 5.1b, 5.1c
and 5.4; 5.3b when the work reaches it, its run on the bench PLC staying with 2.2; the rest of
5.14 after 5.1 to 5.3. Phase A, Phase C, Phase D and the owner's questions at the end of this
milestone are not ruled yet.* Given a plant's drawings (P&IDs, a general
arrangement, an I/O list), an assistant builds a scene that looks like that plant, uses the
plant's own tag names, runs against its PLC program, and is checked by a person before anyone
trusts it.

**Two rules that shape every task.**
- A customer's drawings and the scenes made from them never enter this repository. They live in a
  private project folder. The repository's tests use a drawing set we draw ourselves.
- A new primitive is added only when no composite can produce the behaviour (§5).

#### Phase A: find the real gaps (before any building)
- [ ] 5.0 Pilot: one real drawing set, built by hand with an assistant, outside the repository:
      one small area, with one P&ID, one layout and its I/O list.
      *Done when:* the scene loads, lints, and passes checks written from the plant's sequence of
      operations; every piece of equipment that had no component is listed; the "awkward" list is
      written into PLAN in general terms (no customer name, no drawing content), as 4.1h did. The
      list re-orders or cuts the tasks below.

#### Phase B: what any drawing needs
- [x] 5.1 Takeoff file: the reviewed middle step. A `takeoff.yaml` between the drawing and the
      scene: equipment (tag, type, size, sheet and grid reference), connections, I/O points, a
      confidence mark and open questions per row. `openfactory takeoff check` validates it;
      `openfactory takeoff scaffold` writes a scene skeleton from it and reports every row it
      could not place. *Why:* reading a drawing is error-prone; a person must be able to check a
      table against the drawing before a scene exists.
      *Done when:* our own sample P&ID has a takeoff in the repository, `scaffold` turns it into a
      scene that loads, and a row with an unknown type is reported, not dropped.
      *Ruled 2026-11-15* ([the design note](research/plants-from-drawings-design.md), §3; decisions 6 to 8 and 11 as recommended, with
      additions A, B and C): the takeoff schema with a confidence mark and open questions per
      row; `takeoff check` (an `along` past the belt is an error, never clamped); `takeoff
      scaffold` (an unknown type becomes a stand-in carrying its I/O, every row reported, exit 3
      when rows remain); one file-level `checked` mark (decision 6); no stored `mounted_on`, the
      takeoff holds the mount and the scene keeps plain positions from
      `core/mounts.place_on_rail` (decision 8, the question left open on 2026-11-14); the sample
      set in SVG and CSV under CC-BY-4.0 (decision 11). Additions: (A) the `checked` mark also
      carries a digest of the rows (`drawings`, `equipment`, `connections`, `io`; not the mark),
      so a row changed after checking makes it stale as a changed sheet revision does, and
      `takeoff check` prints which; (B) positions are recorded as the drawing gives them: a
      layout sheet carries a `frame` (units, the datum's place in the scene, rotation), a row
      gives `layout: {sheet, x, y, elevation}` as printed and the tool converts to scene metres,
      and `at:` in scene metres stays for a row on no layout sheet, reported as such; (C) 5.1c
      also commits each sheet as a PNG rendered from its SVG. The mount key is `conveyor:`, not
      `on:` (PyYAML reads a bare `on` as true; found while recording the rulings).
      *Done (2026-11-16)* by 5.1a, 5.1b and 5.1c: the sample P&ID has its takeoff in the
      repository, `scaffold` turns it into a scene that loads, and the agitator's unknown type
      is reported as a stand-in, not dropped (5.1c's guide clause waits for 5.13).
      *Ruled 2026-11-17 (the owner, on review of 5.1a to 5.1c):* (1) the sample fails `--strict`
      on its six open questions, as 5.1a's Done-when says (the prompt that asked it to pass was
      wrong); (2) confirmed as built: `layout.heading_deg`; "a `read` row has no questions" is not
      enforced; duplicate scene ids and scene tags are errors; an equipment row's `type`
      defaults to `unknown`; a stand-in on a rail is placed as a photo eye would be; a missing
      parameter with no default is "unplaced"; `--in-scenes` is required to write inside
      `scenes/`; (3) a parameter left at its type's default is not marked in the scene (the
      text that said `# ASSUMED <param>` was corrected to what the code does); (4) 5.1c's
      authoring-guide clause is carried to 5.13.
      *Review fixes (2026-11-17), each with a test that failed before:* (1) **one-line text**:
      every text in a takeoff (tag, `ref.grid`, `line`, `questions`, `rail`, `conveyor`,
      `from`, `to`, `description` and the rest) refuses control characters, line breaks
      (including U+0085, U+2028 and U+2029, which PyYAML also breaks on) and tabs, an error
      naming the row; and `scaffold` writes every comment through one helper (`comment`) that
      turns any of them into a space, and every value through `flow`, which is one line or an
      error. A question holding a line break had become a line of scene YAML: a component no
      row names, while the report said every row was accounted for
      (`test_text_with_a_line_break_or_control_character_is_an_error_naming_its_row`, 63
      cases; `test_the_scaffolded_scene_has_exactly_the_components_its_rows_account_for`,
      which feeds a question past the schema; `test_comment_never_holds_a_line_break`). (2)
      **duplicate keys**: the loader (`TakeoffLoader`) refuses a key written twice in one
      mapping, naming the key and its line, and refuses anchors, aliases and the merge key
      before anything expands (`test_a_key_written_twice_is_refused_naming_the_key_and_its_line`,
      `test_anchors_aliases_and_merge_keys_are_refused`,
      `test_an_alias_chain_is_refused_before_it_expands`). (3) **parameters**: `check` validates
      each known-type row's `params` against its type's model (`param_problems`): an unknown
      name, a wrong-typed value (`length: "long"` raised ValueError in `check` before) and a
      placement key (`id`, `type`, `description`, `position`, `rotation_deg`, `mesh`, `io`) are
      errors naming the row (`test_params_are_checked_against_the_rows_type`, 9 cases). (4)
      **self-load**: before writing, `scaffold` loads its own scene, keys written twice
      refused, then the scene loader and `build`, unless a row is unplaced for a parameter with
      no default; a failure is a bug in scaffold, exit 1, nothing written, the loader's message
      printed (`test_a_scene_that_does_not_load_is_a_scaffold_bug_exit_1_nothing_written`,
      `test_a_measures_naming_no_row_fails_the_self_load_not_the_run`). (5) **assumed I/O and
      connection rows** have the outcome "assumed"; the order is unplaced or not connected,
      stand-in, assumed, placed; the sample's counts do not move
      (`test_an_assumed_io_or_connection_row_is_assumed`). (6) **pipe ids**: `check` runs the
      scaffold's `plan_connections` and makes a pipe id that equals an equipment row's scene
      id an error on the connection row naming both rows
      (`test_a_pipe_id_equal_to_an_equipment_rows_scene_id_is_an_error_naming_both`). (7) **the
      sample**: PE-201's ref is C4, where L-001 draws it; the sample was marked again (`--mark
      XX`), so its rows digest is now `1e822ddb5b17bd3a` (was `2c9c61b4ae244f3a`), and the
      tests, `docs/takeoff.md` and the design note quote it; P-001's title block moved below
      the bottom zone letters (the sheet is 20 px taller, 1400 by 1020), both PNGs rendered
      again (L-001's bytes did not change) and looked at. (8) **a stand-in with two DIs and two
      AIs**: the scene builds and runs, the second DI reads false and the second AI the low end
      of its range, and that AI's comment now says so (it said "follows the first AO")
      (`test_a_stand_in_with_two_dis_and_two_ais`).
  - [x] 5.1a The takeoff file and `takeoff check` (note §3.2, §3.3): the schema and its JSON
        Schema export, `--strict` (`--against` is 5.6), the `checked` mark with its rows digest
        and `--mark INITIALS` (A), and layout frames with positions as printed (B).
        *Done when:* the sample takeoff (note §3.6) passes `check`, and `--strict` fails it on its
        six open questions; one test per error of note §3.3 fails with that row named, including
        `along: 3400` on the 3000 mm belt; `--mark XX` writes a mark with the rows digest and
        changes no other byte of the file, and `check` then prints "checked: current"; a test
        edits one row of the checked sample and sees `--strict` fail with "rows changed since
        checking", and another changes a sheet's `rev` and sees it fail naming that sheet; a
        test converts the sample's `layout` and `mount` rows and gets the note's scene positions
        within 1 mm, and the same positions from a copy of the frame and dimensions turned 90
        degrees.
        *Done (2026-11-16):* `src/openfactory/takeoff.py` and `openfactory takeoff check TAKEOFF
        [--strict] [--mark INITIALS]` (a `takeoff` command group). **The file is data:** read
        with PyYAML's safe loader, at most 2 MB (`MAX_BYTES`), validated by `Takeoff`, whose
        every model refuses unknown keys; nothing in it is evaluated (a parameter is a literal
        for the scene). Its JSON Schema is `web/public/takeoff.schema.json`, written by `python -m
        openfactory.takeoff` and held current by `--check` in CI's schema step and a test.
        **The mark (A):** `rows_digest` as the note specifies; the sample's mark, computed on
        2026-11-15, is current (`2c9c61b4ae244f3a`; `1e822ddb5b17bd3a` since the review fixes of
        2026-11-17, when PE-201's ref became C4). `mark_state` gives the reasons a mark is
        stale: "rows changed since checking", "<sheet> is rev B, checked at rev A", a sheet not
        checked or no longer listed. `--mark` writes only when there is no error, through the
        4.7 `Document` writer: it replaces the `checked` value (a flow mapping on one line) or
        adds the key at the end, and keeps the file's other bytes and line endings. **Positions
        (B):** `layout_position` is the note's rule; `position_of` adds the mount
        (`place_on_rail`, `along` in the sheet's units, an emitter's tallest part for a pusher)
        and `at`. **Findings** each name their row and sheet (a position's sheet, else the first
        ref's, else "no sheet"; a ref to a missing sheet names that sheet). **Decisions beyond
        the ruling:** (1) `layout.heading_deg` (default 0), the item's direction on the sheet,
        added to the frame's rotation for its scene heading; without it a belt on a turned
        sheet would turn with the sheet, and the 90-degree test could not hold for the mounted
        devices. (2) The note's sentence "a row is never `read` with open questions" is not an
        error: §3.3 does not list it, and the worked example's own EQ-5 is `read` with a
        question about its type; the sample must pass. (3) A duplicate derived scene tag and a
        scene id that is not capitals, digits and `_` are errors too (the note lists the scene
        tag; the id follows). Docs: `docs/takeoff.md`, in the site's Reference. *Tests*
        (`tests/unit/test_takeoff.py`, 34): the sample passes, and `--strict` fails on its six
        open questions (EQ-1, EQ-5, EQ-8, EQ-12, EQ-13, IO-15); 26 errors, one each, every one
        naming its row and sheet and exiting 1 (among them `along: 3400` on the 3000 mm belt,
        "along 3400 mm is 3.4 m, outside CV-201 (0 to 3 m); it is not clamped"); the two
        warnings; `--mark` writes the mark with the digest, every other byte as it was, then
        "checked: current", adds a mark to a file without one, and writes nothing with an error;
        one row edited fails `--strict` with "rows changed since checking"; L-001 at rev B fails
        it with "L-001 is rev B, checked at rev A" and no rows reason; the 13 rows' positions
        within 1 mm of the note's (0 mm in fact), and the same from a copy turned 90 degrees
        (dimensions `x' = y`, `y' = -x`, `heading_deg: -90`); a Python tag refused by the safe
        loader, a file over the limit refused, an unknown key an error, a brace in a parameter
        never evaluated; the JSON Schema current and closed. The sample takeoff is committed
        here, at `docs/samples/wash-skid/wash-skid.takeoff.yaml`, the note's text with a
        header; its sheets come in 5.1c.
  - [x] 5.1b `takeoff scaffold` (note §3.4).
        *Done when:* the sample takeoff scaffolds to a scene that validates; its report accounts
        for all 32 rows (25 placed, 4 assumed, 3 stand-in), and AG-101 is a stand-in type
        carrying IO-5 and IO-6; the exit code is 3; PE-201 and CYL-201 sit where `place_on_rail`
        puts them; the report's positions line lists 9 rows from L-001, 2 on CV-201's rail and
        the 2 `at:` rows (EQ-12, EQ-13) as in scene metres with no layout sheet; removing a row's
        `type` (making it `unknown`) adds one stand-in and never removes a row; running the
        scaffold twice writes the same bytes.
        *Done (2026-11-16):* `src/openfactory/scaffold.py` and `openfactory takeoff scaffold
        TAKEOFF -o SCENE [--force] [--in-scenes]`. It refuses a takeoff with any `check` error
        and writes nothing (exit 1); it writes only the `-o` path, never over an existing file
        without `--force`, and never inside the project's `scenes/` without `--in-scenes`.
        **Outcomes** as note §3.4, one per row, in the takeoff's row order, with the sum
        printed: placed, assumed, stand-in (a local `standin_<id>` composite on a `scenery`
        body, its io exactly the row's I/O rows), unplaced (a holding row past the far edge,
        1.5 m apart) and not connected (a comment at the end). **Connections:** two conveyors a
        `connections:` entry; into a valve or pump that link's `from`; out of a link to a node,
        or node to node, a `pipe` (`PIPE_101` on) at the ends' midpoint, its diameter from the
        line number's size (25 mm), the link's `to` naming it. A parameter that names another
        row's tag (`measures: TK-101.level`) is written with its scene id. Each binding is
        `{tag, plc_tag}` with the I/O row's `range` and `units`. **Decisions beyond the
        ruling:** (1) an equipment row's `type` defaults to `unknown`, so removing it makes a
        stand-in; (2) a stand-in on a rail is placed where `place_on_rail` puts a photo eye; (3)
        a type parameter with no default that the row does not give (a transmitter's
        `measures`, a link's `from`) is a comment to fill in, and the row is reported unplaced,
        since the scene cannot load until a person gives it; the note's "missing a required
        parameter ... at its documented default" cannot arise, as a parameter with a default is
        left to it; (4) `--in-scenes`, so a scaffold never lands among the shipped scenes by
        accident. **The sample:** 32 rows, 25 placed, 4 assumed (EQ-1, EQ-8, EQ-12, EQ-13), 3
        stand-in (EQ-5 AG-101 as `standin_ag_101`, IO-5 -> run, IO-6 -> running), exit 3; the
        positions line lists 9 rows from L-001, 2 on CV-201's left rail and EQ-12 and EQ-13 in
        scene metres with no layout sheet; the scene validates, and `openfactory lint` reports
        15 warnings, all W201 and W202 ("no PLC side"), as expected with no program. Docs:
        `docs/takeoff.md`. *Tests* (`tests/unit/test_scaffold.py`, 9): the sample's report and
        counts, exit 3, the scene loading with AG-101's stand-in carrying `AG101.Run` and
        `AG101.Running`, the six `# QUESTION` comments; lint only W201 and W202; PE-201 and
        CYL-201 equal to `place_on_rail`'s output, (1.2, 0.1, 2.5) and (2.0, 0.1375, 2.2); the
        positions line; LT-101 and PE-201 each with its `type` removed add one stand-in and keep
        32 rows, and still load; two runs the same bytes; an existing file, `scenes/` and an
        invalid takeoff refused with nothing written; a row with no position (the holding row,
        3.15 + 1.5 m) and a conveyor-to-tank connection (not connected) reported, 33 = 33; a
        clean takeoff exits 0.
  - [x] 5.1c The sample drawing set (note §3.7): `docs/samples/wash-skid/` with P-001 and L-001
        as SVG drawn by us, `IO-001.csv` and the takeoff, and each sheet as a PNG rendered from
        its SVG (C) by `tools/render_sheets.py` through Playwright's Chromium (no image library).
        *Done when:* the files are there with their license line; each SVG is under 100 KB and
        each PNG under 1 MB; the test that cross-checks the I/O CSV against the takeoff passes;
        a test finds each PNG's recorded source digest equal to its SVG's; each sheet has been
        opened and looked at, as SVG and as PNG; the authoring guide (5.13) shows the PNGs.
        *Done (2026-11-16), but for the authoring guide, which does not exist yet:* 5.13 now
        carries "shows the sample's PNGs"; until then `docs/takeoff.md` shows them.
        `docs/samples/wash-skid/`: `P-001.svg` and `L-001.svg` (10.8 KB and 14.8 KB), written by
        `tools/make_sample_sheets.py` from SVG primitives only (no symbol library, no template,
        a title block of our own; instrument bubbles after ISA-5.1); `P-001.png` and
        `L-001.png` (68 KB and 83 KB, 1400 by 1000, 1020 since 2026-11-17, and 1400 by 1100) rendered by
        `tools/render_sheets.py` in Playwright's Chromium, each with the SVG's SHA-256 in a
        `tEXt` chunk `openfactory-source`; `IO-001.csv` (the I/O CSV's columns and `plc_tag`,
        row 15 abbreviated as `CYL201 RET` on purpose); the takeoff (from 5.1a); `LICENSE.txt`
        (CC-BY-4.0, a plant of our own invention, no real plant, customer, vendor or product).
        **Looked at, as PNG** (and as SVG text): P-001 shows the zone grid A to E and 1 to 4, the
        make-up supply in A1 dropping on line 25-W-100 to control valve LV-101 in B2, line
        25-W-101 into the top of TK-101 in C2, agitator AG-101's motor in C1 with its shaft
        into the tank, LSH-101 at the tank's top and LT-101 in C3 on dashed signal lines, pump
        P-101 in D2 on suction line 25-W-102, discharge 25-W-103 through FE and FT-101 (D3) to
        "TO WASHER" in E2, four notes and our title block. L-001 shows the zone grid, the datum
        at a building column with +X, +Y and north, X ordinates 1000 to 5600 along the top and
        Y ordinates 4000 and 1500 at the left, the skid on its line at Y 4000 with each item's
        EL, the tank's circle with the agitator on it, and CV-201 (3000 by 500, flow +X) with
        PE-201 and CYL-201 on its north rail dimensioned 1200 and 2000 from its infeed. Two
        rounds of fixes before that: labels crossing the skid outline, the ordinate lines and
        the tank. **Grid references:** the note's L-001 references could not all hold on one even
        zone grid (P-101 at X 4200 in C and PE-201 at X 4200 in B); the grid of 1600 mm columns
        from X 700 keeps every one but PE-201's, which sits 300 mm into C. Fixed 2026-11-17 as
        ruled: PE-201's ref is C4 and the sample was marked again. Fixed 2026-11-18 on review:
        "WASH WATER TANK" touched the agitator's shaft on P-001; TK-101's two labels now sit at
        X 686, left of the shaft at X 745, the sheet re-rendered and looked at (the label ends
        about 15 px short of the shaft; the takeoff and its mark did not change), held by
        `test_no_text_touches_a_vertical_line` (each text's box, 0.66 em a character and 4 px
        clear, against every vertical line on both sheets). *Tests* (`tests/unit/test_sample_set.py`, 6 since 2026-11-18): the files, their
        licence lines and sizes; the SVGs current and made of primitives only, with no link or
        script; each PNG's recorded digest equal to its SVG's and its size the SVG's (a render
        is not the same bytes on every machine, so pixels are not compared); the I/O list
        against the takeoff, row by row, the inferred IO-15 against its question's quote; the
        sample passes `check`, fails `--strict` on its questions, and a copy with them answered
        is stale until marked, then passes `--strict`. Scaffolding the sample gives the worked
        example's counts, 25 placed, 4 assumed, 3 stand-in (5.1b's test).
- [x] 5.2 Generic equipment. Something on the drawing has no component: it must still appear, with
      its I/O and a simple behaviour (a delay, a first-order lag, a permissive). First test how far
      a composite goes today (computed DIs exist, 2.16); add a `machine` primitive only for what a
      composite cannot do.
      *Done when:* a scene holds an item no primitive covers, with a mesh or a box, three ports and
      a start delay, and its tags export like any other.
      *What exists today (2026-11-15):* a composite type (2.16, `core/composite.py`) is built only
      from built-in parts, which it expands into at load. Its `io` can pass a PLC command to a
      part's DO or AO port (`to`, optionally `invert`), pass a part's DI or AI through (`from`),
      or compute a DI from a boolean expression over the parts' DI and AI values (`from:
      "<expression>"`, the `core/expr.py` grammar), with the binding's `invert`, `on_delay_s` and
      `off_delay_s` (2.17, `core/transforms.py`): a start delay on a computed DI exists today.
      2.5b added `command` io (a PLC tag no part receives, read only by `logic:`) and `logic:`,
      which drives a part's DO port from such an expression, one tick behind. There is no
      computed AI, no first-order lag or other state of the composite's own, and the instance has
      no `mesh` key of its own: it is drawn as its parts, each as its built-in type. A composite
      whose `io` is empty is W208.
      *Ruled 2026-11-15* ([the design note](research/plants-from-drawings-design.md), §2; decision 5 as recommended): command names and `AO`
      commands in io expressions, numeric `kind: AI` io and a conditional expression (composite
      additions), `lag_s` on an AI binding (a 2.17 transform), and the 5.11 `scenery` primitive
      brought forward for the body; no `machine` primitive.
  - [x] 5.2a Composite additions: command names in io expressions, `command: AO`, `kind: AI`
        numeric io, the conditional expression, computed io reading computed io (cycles
        refused).
        *Done when:* a scene's composite reports `running` one tick after `start` with no part
        having a running output; `on_delay_s: 2.0` on that binding makes it 100 ticks at 50 Hz;
        a `speed_fb` AI follows `speed` while running and reads 0 when stopped; a cycle
        `a <- b <- a` is a load error naming it; `test_expr.py`'s generated expressions still
        raise only `ExprError`.
        *Done (2026-11-16):* **Grammar** (`core/expr.py`): the conditional `A if TEST else B`
        is the only new node; its test must be true or false and only the branch it picks is
        evaluated, so `x / y if y != 0 else 0` is safe; every other rule is as before.
        **Composites** (`core/composite.py`, `CompositeIo`): `command: AO` with `range` and
        `units`; `kind: AI` with `range` (and `units`) on an expression io; an io expression may
        name the composite's commands and its other expression io by io name. **Decisions:** (1)
        a command reads as its tag holds it when the transform stage runs (the PLC's write that
        tick) and an expression io as its own tag, written earlier the same tick, so a feedback
        sees the other io after its delays (`speed_fb` follows the delayed `running` the PLC
        sees); the stage evaluates signals in dependency order, and `_check_cycles_in_io`
        refuses a cycle at load, "io expressions read each other: a <- b <- a". (2) `_probe`
        now types a result as the io's kind (DI true/false, AI a number) and tries each
        true/false reading both ways, so both branches of a conditional are typed at load; an AO
        command probes as a number, in `logic:` too, where `LogicStage` now reads an AO command
        as a number instead of `bool()`. (3) A computed AI keeps its last value on a reading it
        cannot use (a division by zero); a computed DI reads false, as before. (4) An expression
        with no part ref belongs to the composite's first part (it had no owner before). (5) A
        command binding may be a mapping (5.3a) and is validated as a DO or AO binding; the
        command's I/O list description is unchanged, so the accumulation demo's exports did
        not move. The Done-when scene is `tests/scenes/composite-additions.yaml`: `lamp_drive`,
        whose only part is a pilot light, with `start` (DO), `speed` (AO), `running: {from:
        "start"}` and `speed_fb: {from: "speed if running else 0", kind: AI}`. Docs:
        `docs/scene-format.md`, a section; the keys are YAML lines for now (the editor's form
        does not edit a composite's definition). *Tests*
        (`tests/unit/test_composite_additions.py`, 14; `tests/unit/test_expr.py`, 11 more):
        the PLC reads `running` one tick after writing `start`, from no part's output, and 101
        ticks after with `on_delay_s: 2.0` (100 more at 50 Hz); `speed_fb` reads 0 stopped,
        40 and then 75.5 running, 0 stopped again, and with a 0.1 s start delay stays 0 for the
        5 ticks `running` does; the four tags export as DO, AO, DI and AI owned by `MX_01_LAMP`
        with their ranges; `a <- b <- a` and `a <- a` are load errors naming the cycle; eight
        more load errors; an AO command read by `logic:`; the conditional's values, branch not
        taken, a conditional 50 deep refused (851 characters over the length limit; chained 41
        deep under it, refused by the depth limit), a huge literal (400 digits, `1e999`)
        raising `ExprError`, and 2,000 generated expressions with conditionals raising only
        `ExprError`, beside the existing fuzz, unchanged. Shipped scenes: `build_kit.py
        --check` current, `test_runs_unchanged.py` passes, `migrate --check` current.
  - [x] 5.2b `lag_s` on an AI binding, in `TransformStage`, with the transmitter's
        discretization.
        *Done when:* a step from 0 to 100 through `lag_s: 4` reads within 0.5 of 63.2 at 4 s and
        within 0.5 of 98.2 at 16 s at dt 0.02; the run is identical twice; the CSV's transform
        column says `lag 4 s`.
        *Done (2026-11-16):* `lag_s` (seconds, 0 is none) on `AiTagSpec`, so on any part's AI
        port and on a composite's computed AI; refused with `window:` (a DI). `Transform.lag_s`,
        shown as `lag 4 s` in the transform column. `TransformStage` builds an AI signal for an
        AI binding with a lag (as it does for a computed AI, 5.2a); the component no longer
        writes that tag itself (`Component.conditioned`), so the component's own value, the
        viewer and `look`'s sentence are the unlagged ones and only the PLC's tag lags.
        **Discretization:** the transmitter's, forward Euler `y += (u - y) * min(dt / lag_s,
        1)`, stepped with `dt` only, no clock; the first value is taken as it is at load, as the
        transmitter's is. **Readings** (dt 0.02, `lag_s: 4`, a step of the composite's speed
        command from 0 to 100 while running): **63.3042 at 4 s** (200 ticks) and **98.1867 at
        16 s** (800 ticks), against the continuous 63.21 and 98.17 and the Done-when's 63.2 and
        98.2 within 0.5. Docs: `docs/scene-format.md`, a section; a YAML line for now. *Tests*
        (`tests/unit/test_lag.py`, 5): the two readings, also equal to `100 (1 - (1 - dt /
        4)^n)`; the run identical twice; the CSV column `expr(speed if running else 0); lag 4
        s`; a transmitter's pv lagged again on its binding equals the filter of the port's raw
        value every tick for 500 ticks, starting at the raw value; `lag_s` refused with a
        window and below 0. Shipped scenes use no `lag_s`: kits current, run references
        unchanged.
  - [x] 5.2c The `scenery` primitive (5.11's type brought forward: `size`, `color`, `mesh`, no
        I/O, no physics) and the `driven_item` and `variable_item` library composites (note
        §2.2).
        *Done when:* 5.2's own Done-when holds: a scene holds a `variable_item` drawn as a box and
        then with a mesh, with its start, running, speed and speed feedback ports and a 2 s start
        delay, and its tags export like any other; the screenshot is looked at; a unit test, a
        docstring spec and a section in `docs/components.md` cover `scenery`.
        *Done (2026-11-16), and with it 5.2's own Done-when, so 5.2 is ticked too:* **`scenery`**
        (`components/scenery.py`, `Scenery` in the schema): `size` [x, y, z] m (default 1 m
        each), `color` `#rrggbb` (default the neutral grey `#8b939c`), `mesh`; no ports, no
        faults, an empty state, a `step` that does nothing; origin the centre of the box;
        `describe` "a 1.00 x 0.80 x 0.60 m body, no I/O". Parts pass through it: the core never
        tests parts against it and `web/src/physics.ts` makes no collider for it. The viewer
        draws one box of its size and colour as its housing, which a mesh replaces (2.15). The
        editor's palette offers it as `SCN_nn`, standing on the floor. **Library composites**
        (`scenes/_library/driven_item.yaml`, `variable_item.yaml`, new files; no existing
        library file changed): a `scenery` body sized by `length`, `height` and `width`, and a
        three-position hand-off-auto selector on its +x face starting in auto; `start` (DO
        command), `running`, `hoa_hand` and `hoa_auto`; `variable_item` adds `speed` (AO, %) and
        `speed_fb` (AI, `speed if running else 0`). **Decisions beyond the ruling**, for the
        owner to confirm: (1) **a mesh per instance:** an instance's `mesh:` is given at
        expansion to the composite's one scenery part (refused with none or several), since a
        library file's parts cannot know a scene's mesh; the viewer, parity and exports still
        see only built-in types, which was the reason §2.3(b) refused a mesh key that the viewer
        would read on a composite. (2) **`running` is `hoa.pos_a or (start and hoa.pos_b)`**,
        not the note's `start and hoa.pos_b`: the selector's `pos_a` is a required port and
        must be exposed (`hoa_hand`), and a hand position that did not run the item would be a
        trap; off stops it whatever the PLC says. **The demo** is
        `scenes/examples/variable-item-demo.yaml` with its script beside it: a short box line
        (checks `parts_reached` and `none_lost`; 2 of 2 pass at 60 s, 19 boxes, and at 300 s, 99
        boxes) and two `variable_item` instances, `MX_01` drawn as its box and `MX_02` with
        `models/mixer-drive.glb` (ours, 14,900 bytes, built from boxes and cylinders by
        `tools/make_item_mesh.py` with `make_models.py`'s writer; CC-BY-4.0 sidecar;
        THIRD_PARTY.md and ATTRIBUTION.md regenerated), a 2 s start delay and a 4 s lag on its
        speed feedback. It lints with no finding under its script. It is a new scene in
        `scenes/examples/` (beside `accumulation-demo`), so §9 rule 13 holds it by a new reference, `tests/references/
        variable-item-demo.run` (new, not a renewal), and `test_runs_unchanged.py` counts 14
        scenes. **Screenshots** (`tools/screenshot.py`, at 8 s, looked at): with the mesh, `MX_01`
        is a plain grey box and `MX_02` a geared drive on a base plate (gearbox block, motor
        barrel with its fan cover toward +x, terminal box, output flange on top), no box behind
        it; with the scene's `mesh:` line removed, both are identical grey boxes. In both, the
        selector is a small knob on each body's +x face and the boxes ride the belt to the
        remover. *Tests* (`tests/unit/test_scenery.py`, 10): no I/O, no behaviour, its sentence,
        no fault, one tag (the heartbeat); four refusals (a named colour, a short hex, a zero
        size, an io port); parts pass through, a scenery box straddling the sorter's infeed belt
        leaving every part's position the same as without it over 30 s; the mesh rebuilt byte
        for byte, under 1 MB, its sidecar; the demo's box and mesh bodies, its ports and their
        transforms; the 2 s delay (`running` read 101 ticks after `start`, 100 more than
        without), hand runs without the PLC and off stops it; `driven_item` with a mesh, and a
        mesh refused on a composite with no scenery part. `web`: one builder test (161 pass).
        `docs/components.md` (a Scenery section), `docs/scene-format.md` (the two composites),
        the §5 table row; the JSON Schema regenerated. Kits current; the other run references
        unchanged.
        *Ruled 2026-11-16 (owner, review of 996e1c8):* the five decisions made beyond the
        rulings in 5.2a and 5.2c are confirmed as built: (1) an instance's `mesh:` goes to its
        composite's one `scenery` part; (2) `driven_item`'s and `variable_item`'s `running` is
        `hoa.pos_a or (start and hoa.pos_b)`; (3) an io expression with no part reference
        belongs to the composite's first part in the exports; (4) AO commands read as numbers in
        `logic:`; (5) the load-time type check tries each true/false reading both ways, so both
        branches of a conditional are typed.
- [ ] 5.3 The plant's tag names. `plc_tag:` per binding (§6.2, named but not built), so a scene
      port maps to the plant's tag or UDT member, and an area prefix per scene.
      *Done when:* the I/O CSV, the L5X and the SCADA tag export carry the plant's names, the
      lockfiles do not move, and a plant name outside the HIS pattern loads and exports with no
      error; a binding left without a plant name in a mapped scene is a warning (W215).
      *(Reworded 2026-11-15 by decision 2 of the design note. It said: "lint flags a name
      outside the HIS pattern as a warning, not an error".)*
      *What exists today (2026-11-15):* a binding's tag must match `^[A-Z][A-Z0-9_]*_(DI|DO|AI|AO)$`
      and be at most 40 characters (`core/schema.py`): any other name is a load error, not a
      warning. §6.2 says the PLC tag name equals the scene's tag name by default, with an optional
      `plc_tag:` per binding; that key is not in the schema (noted under 2.2: "`plc_tag:` per
      binding ... is not built"), so the Logix path reads and writes the scene's names. The
      exporters all use the scene's names: the I/O CSV one row per bound tag
      (`export/io_list_csv.py`); the L5X stub one controller tag per CSV row, with an optional
      `--prefix AREA1` that prepends `AREA1_` to every name and description and checks the Logix
      name rule (`export/l5x_stub.py`); the SCADA tag JSON (2.4) one UDT instance per component,
      named by the component id, in one area folder named by `--area` or the scene name in
      capitals, its members named by port. The Modbus lockfiles map tag names
      to addresses (ADR-0004), one per scene file and analog encoding.
      *Ruled 2026-11-15* ([the design note](research/plants-from-drawings-design.md), §1; decisions 1 to 4 as recommended, decision 2 as (a)):
      (1) `plc_tag:` beside the scene's tag, which stays the key for everything inside
      OpenFactory, so the lockfiles cannot move; (2) no warning for a well-formed `plc_tag`,
      W215 for a binding with no `plc_tag` in a scene where others have one, and the Done-when
      above reworded; a malformed or duplicate `plc_tag` is a load error; (3) the L5X stub
      declares controller and program tags and lists members, elements and bits in a comment
      without declaring them; (4) the Logix path sets `init_program_tags=True` only when a
      `Program:` name is present (pycomm3 1.2.16 replaces its tag cache when one program is
      uploaded alone), the upload time on a large program to be measured on the bench PLC.
  - [x] 5.3a `plc_tag` in the scene and the exports: the key, its grammar, duplicate and
        case-duplicate errors (once the name rules are verified against the vendor's published
        manual), `scene.area`, `plc.heartbeat_plc_tag`, the CSV and `CONTEXT.md` column, the L5X
        stub rules by form, the SCADA export parameter, and W215.
        *Done when:* a copy of `sorting-by-height` with every binding given a `plc_tag` of each
        form (controller, program, member, bit) exports a CSV whose `plc_tag` column equals the
        scene's, an L5X with one controller tag, one program tag and the member and bit rows
        listed in its comment, not declared, and a SCADA export whose device-path parameters
        are the plant names; removing one binding's `plc_tag` raises W215 for that binding
        only; every shipped scene's exports, lockfiles, kits and run references are
        byte-identical (CI's gates); a test enumerates the grammar's accepted and refused names.
        *Blocker (2026-11-16), lifted 2026-11-17 (below):* the controller's name rules were not
        read. The vendor's published programming manual (publication 1756-PM004, the I/O and
        tag data programming manual) answered 403 at the vendor's literature library, and a
        second try at a public copy elsewhere was refused by the session's permissions. So no
        rule is written from memory: the grammar is the shape pycomm3 1.2.16 splits a request on
        (`_parse_tag_request`, cited in `core/plctag.py`), and these are left out until the
        manual is read and cited by section: a length limit, whether a name may start with `_`
        or hold doubled or trailing underscores, the bit range per data type, and whether names
        that differ only in case are one name (the case-duplicate error). `test_plc_tag.py`
        holds three names that pass today only for want of those rules (60 characters,
        `Tag__Name_`, `Word.99`), to be turned round when they are. *To unblock:* the owner puts
        the manual's PDF in the session's scratch folder or names a URL a session can read.
        *Built (2026-11-16), all but the rules above:* `src/openfactory/core/plctag.py`:
        `parse_plc_tag(name)`, the shape and the form (controller, program, member, element,
        bit). **Keys:** `plc_tag` on every binding mapping (`TagSpec`, so DI, DO, AI and AO; and
        a composite instance's pass-through and command io, whose mapping form a command now
        takes, validated as a DO binding), `scene.area` (capitals and digits, letter first) and
        `plc.heartbeat_plc_tag`, in the schema, the exported JSON Schema and
        `docs/scene-format.md`; YAML lines for now, which the editor's text operations keep as
        written. **Load errors:** a malformed `plc_tag`; a name two points share, where a point
        with none counts by its scene tag and the heartbeat by its `heartbeat_plc_tag` or
        `SIM_HEARTBEAT_DI`, so a plant name cannot collide with an unmapped point either; exact
        duplicates only (case: the blocker). **Exports:** the I/O CSV's `plc_tag` column and
        `CONTEXT.md`'s "Plant tag" column, written only when some point has one; the L5X stub by
        form: a controller name one controller tag, a `Program:` name one tag under
        `<Programs><Program Use="Context">`, a member, element or bit listed in a comment before
        the root with its form, its base and its scene tag and not declared, and `export
        --format l5x` prints their count on standard error; `--prefix` only on rows without a
        `plc_tag`, defaulting to `scene.area`; the SCADA export's `Tag_<Member>` (and the
        heartbeat's path) is the plant name with `--device` and the scene tag without, its area
        folder `scene.area` by default. **Lint:** W215, one per binding without a `plc_tag` in
        a scene where others have one (`lint.py`, `docs/scene-format.md`). **Look:** a tag with
        a plant name shows it, `PE_01_DI [plant Sorter_DI_Word.4] = false`; the scene document
        (and so a recording's header) carries `plc_tag` on a tag and `area` on the scene only
        when set, so the 12 kits are current and the golden file did not move. The Logix path is
        5.3b's and still uses scene tags. *Tests* (`tests/unit/test_plc_tag.py`, 46): 15 accepted
        names with their forms and 21 refused; the three unchecked names; a malformed name and
        three kinds of duplicate as load errors naming the bindings; the Done-when's sorter copy
        (one program, one controller, five member, three bit and one element names, the
        heartbeat a bit): its CSV column equals the scene's, its L5X declares one controller tag
        (`Conv1_Speed`, REAL) and one program tag (`Program:Sorter.Conv1_Run`) and lists ten in
        the comment, its SCADA export with a device carries the plant names and without one the
        scene tags, its `CONTEXT.md` the column; one `plc_tag` removed gives W215 for that
        binding only; the sorter exports as before (no column, no comment, no W215); `scene.area`
        prefixes only the unmapped row; a composite's command and pass-through io carry it;
        `look` shows it. Shipped scenes: `build_kit.py --check` current, `migrate --check`
        current, every lockfile unchanged.
        *Done (2026-11-17):* the rules, from the manual the owner supplied (publication
        1756-PM004L-EN-P, revision L, November 2023, "I/O and Tag Data" programming manual),
        read at the pages below and stated in our own words; the PDF stays out of the
        repository (excluded on the owner's machine only, and no test needs it). **Checked**
        (`core/plctag.py`, each cited in its code comment and error message): (1) **length**: a
        tag name has at most 40 characters (ch. 2, "Guidelines for tags"); it is the declared
        tag's name that is measured (`Tag` in `Tag`, `Program:P.Tag`, `Tag.Member`, `Tag[3]`,
        `Tag.4`), as the rule is for a tag name; (2) **underscores**: no two together and none
        at the end of a tag name (same section; a leading `_` is allowed there, as the shape
        already allowed); (3) **bit range**: a bit is a bit of a SINT, INT or DINT (ch. 2,
        "Address tag data"), the widest 32 bits (ch. 2, "Data type"), so a bit above 31 is
        refused by the parser; the range for the tag's own type needs its data type, which a
        name does not carry, and is 5.3b's connect-time type check; (4) **case**: tag names are
        not case sensitive (ch. 2, "Guidelines for tags"), so `plc_tag_key` folds the tag
        name's case and the scene's duplicate check (bindings, unmapped scene tags and the
        heartbeat) and the takeoff's compare keys: names that differ only in that case are a
        load error saying so. **Not checked, as the manual does not state them:** any rule for
        a program's name or a structure member's name beyond the shape (their length,
        underscores or case; so `CV201.Running` and `CV201.RUNNING` stay two names); the bit
        range by the tag's own type (5.3b, as above). The takeoff check uses the same parser and
        key, and the sample's fifteen `plc_tag`s still pass (`takeoff check` on the sample: 0
        errors). Shipped scenes carry no `plc_tag`, so their exports, lockfiles, kits and run
        references do not move (`build_kit.py --check`, `migrate --check`, the lockfile and
        golden-file tests). *Tests* (`tests/unit/test_plc_tag.py`, 79): per rule, the names
        accepted and refused (four rules, 14 accepted and 18 refused, each refusal citing the
        publication); the three names that passed for want of the rules turned round (60
        characters, `Tag__Name_`, `Word.99`, each refused with its reason); names differing only
        in the tag name's case a load error, the heartbeat included, and program and member
        case kept apart; `test_takeoff.py`: a `plc_tag` differing only in case a duplicate
        naming both rows, and `.99` refused.
  - [x] 5.3b The Logix path by plant names: the client reads and writes `plc_tag`s, maps results
        back to scene tags, sets `init_program_tags` when a `Program:` name is present, and runs
        the connect-time type check with the bit split.
        *Done when:* `tests/unit/test_logix.py`'s fake controller holds a program-scoped tag, a
        structure with a `BOOL` member and a `REAL` member, and a `DINT` whose bit 4 is a DI; the
        sorter copy exchanges all of them in one write and one read per cycle; a wrong-type
        member is "mismatched" and left out; a bit on a `REAL` is refused at connect. The run on
        the bench PLC stays 2.2's Done-when.
        *Done (2026-11-18), on the fake controller (the bench PLC run is 2.2's, not claimed
        here):* `LogixClient(..., plant=)` (`protocols/logix.py`; `cli._logix_client` passes
        `plant_names(scene)`: each binding's `plc_tag` and the heartbeat's
        `heartbeat_plc_tag`) sends the one `write()` and the one `read()` of a cycle by the
        plant's names and puts each result back under its scene tag (pycomm3 returns results
        under the requested name: `LogixDriver.read` and `write` build `Tag(user_tag, ...)`).
        `init_program_tags` is `True` exactly when a name starts with `Program:` (the
        `LogixDriver.__init__` docstring: tag definitions are needed to read or write; its
        `init_program_tags` uploads program tags through `get_tag_list(program="*")`).
        **Connect-time check:** `split_bit` splits a trailing digit segment off as
        `_parse_tag_request` does, `LogixDriver.get_tag_info` looks the rest up (it does not
        strip a bit itself), and `type_problem` accepts for a bool point a `BOOL` (tag or
        structure member), a `BOOL` array element (pycomm3 types it `DWORD`), or a bit of a
        SINT, INT or DINT within 0 to 7, 0 to 15, 0 to 31; for a float32 point a `REAL`; for an
        int16 point an `INT`; anything else "mismatched" with the reason, an unknown name
        "missing", both logged once naming the plant name and left out. **Bit range by type,
        cited:** publication 1756-PM004L-EN-P, revision L (November 2023), chapter 2: "Address
        tag data" (a bit is a bit of a SINT, INT or DINT) and "Data type" (their bits: SINT 0
        to 7, INT 0 to 15, DINT 0 to 31), stated in our own words. **Decision beyond the
        ruling:** a bit of a LINT is refused, as that manual names no bit of one (the design
        note's list had LINT from pycomm3). A scene with no `plc_tag` takes the old path
        unchanged (scene tags, `init_program_tags=False`, the check against `tags`): the 14
        tests above this section in `test_logix.py` pass unchanged (the file only gained
        lines). Docs: `docs/logix.md` ("The plant's own names"), `docs/scene-format.md`.
        *Tests* (`tests/unit/test_logix.py`, 11 more): a second fake controller with
        definitions in pycomm3's shape that looks names up and parses requests with pycomm3
        1.2.16's own `get_tag_info`, `_get_tag_info` and `_parse_tag_request`, holding
        `Program:Sorter.Conv1_Run`, structure `Conv1` with `Running` (BOOL) and `Speed` (REAL),
        `Sorter_DI_Word` (DINT) whose bit 4 is PE_01_DI and bit 0 the heartbeat, and the rest
        of the sorter's names. Done-when lines: (1) the sorter copy with every binding mapped
        exchanges all of them, the first write holding every input's plant name and every read
        every output's, the call sequence `(w?r)+` (at most one write, then one read, per
        cycle), values back under scene tags both ways and bit 4 set without moving bit 0
        (`test_the_sorter_by_plant_names_one_write_and_one_read_per_cycle`); (2) a structure
        member of the wrong type mismatched and left out of every batch, the rest running, and
        (3) a bit on a `REAL` refused at connect citing the publication
        (`test_a_wrong_type_or_a_bit_out_of_range_is_mismatched_and_left_out`, 5 cases, with
        bit 8 of a SINT, bit 16 of an INT and a bit for a REAL point); bits 7, 15 and 31
        accepted; an unknown name missing, logged with the plant name; `init_program_tags`
        True only with a `Program:` name, False for the plain sorter. **5.3 stays unticked:** its
        ruling (4) leaves the upload time with program tags on a large program to be measured
        on the bench PLC, which is 2.2's run.
        *Ruled 2026-11-18 (the owner, on review of 268ccf4..f37541e):* confirmed: a bit of a
        LINT is refused; an element of a BOOL array counts as a BOOL. 5.3 stays unticked until
        the bench run (2.2) measures the upload time with program tags. The design note's list
        (§1, "The type check needs its own split") still had LINT and is corrected.
- [x] 5.4 Plants in several files. `include:` so one plant is one file per area with shared
      composites, one address map, and lint across areas.
      *Done when:* a two-area sample runs as one plant, each area also runs alone, and the address
      map is the same either way. *As ruled 2026-11-15 (note §5):* the two-area sample plant
      runs as one plant and passes its checks at 60 s and 300 s; each area runs alone with
      `--area` and passes its own checks; the address of every tag is the same in all three
      runs (read from the Modbus server); a duplicate id across areas is a load error naming
      both files; the editor opens the plant with the other areas dimmed and saves only the
      file it edits; the plant is held by PLAN §9 rule 13.
      *What exists today (2026-11-15):* nothing like `include:`; a scene is one file, and its
      model refuses unknown keys, so `include:` is a load error today. What is shared between
      files is composite types: `composites: [{library: name}]` loads `_library/<name>.yaml` from
      the scene's folder or the nearest folder above it, no higher than the project root
      (`core/composite.py`). The address map is per scene file: one lockfile per scene and analog
      encoding (`<scene>.<encoding>.modbus.yaml`, ADR-0004). Lint runs on one scene at a time
      (`openfactory lint scenes` lints each file in turn). A bundle (`.ofz`, 2.18) carries one
      scene with the library composites and meshes it uses.
      *Ruled 2026-11-15* ([the design note](research/plants-from-drawings-design.md), §4; decisions 9 and 10 as recommended): a plant file with
      `include:`, ids and tags global across the plant and never prefixed (a clash is a load
      error naming both files), one plant lockfile owned by the plant file, `--area` to run one
      area at the plant's addresses; the editor opens one file at a time, with no save across
      files.
      *Known limit, not a task:* two identical lines cannot share one area file. Ids and tags
      are global and never prefixed, so a second copy of an area clashes with the first; each
      line is its own area file with its own ids and tags.
      *Ruled 2026-11-18 (the owner, on review of 5.4a to 5.4d):* confirmed: `MAX_SCENE_BYTES` of
      4 MB on every scene file; `--area` on `export` selects the area on a plant file and names
      the SCADA folder on any other scene; a plant file may hold components of its own; an area
      run never writes `CONTEXT.md` and refuses to start without the plant lockfile holding its
      tags; `bundle` refuses a plant file; `--mark` writes the mark on one line. The help text
      of `export --area` states both meanings in one sentence each
      (`test_the_help_of_export_area_states_both_meanings_in_one_sentence_each`).
      *Review fix (2026-11-18), with a test that failed before:* a `plc_tag` two files share
      was refused by the merged scene without naming the files; `merge` now reports it as it
      reports ids and scene tags, naming both files and comparing by `plc_tag_key` (once any
      point of the plant has a `plc_tag`, a point without one counts by its scene tag, and the
      heartbeat by its name in the plant file), so names that differ only in case are said to
      (`test_plant.py`: `test_a_plc_tag_two_areas_share_is_a_load_error_naming_both_files`,
      three cases, and `test_a_plc_tag_equal_to_the_plants_heartbeat_names_both_files`).
      *Sub-tasks (written 2026-11-17, before the build, in the ruled order of note §4 and §5;
      ruled additions of 2026-11-17: paths relative to the including file, inside the plant's
      folder, read with the scene's safe loader and size limit; a cycle a load error naming the
      files; an error in an included file naming that file and its line):*
  - [x] 5.4a `include:` at load. A plant file's `include: [{area, file}]`, its own
        `connections:` for links across areas, and the merge (note §4.2, "Load"): each area's
        components and connections, then the plant file's; the plant's `scene`, `plc` and
        `checks` apply; `composites` merged by type name; ids and tags global, never prefixed.
        Paths: relative to the including file, no absolute path, no `..` that leaves the
        plant's folder, no symlink out of it; each file read with the scene's safe loader and
        one size limit shared with scenes (new, as a scene had none). One level deep; a cycle
        is a load error naming the files. Keys in the schema, its JSON Schema and
        `docs/scene-format.md`, and a YAML line for the editor's form for now.
        *Done when:* a test per path rule (absolute, `..` out of the folder, a symlink out, a
        file over the limit, a YAML tag) refuses the plant naming the file; a plant that
        includes itself (by another spelling, and through a symlink) is a cycle naming the
        files; an area with `include:` is refused (one level); a schema error in an area names
        that file and its line; a duplicate id, and a duplicate tag, across two areas is a load
        error naming both files; an identical composite in two areas loads once and a
        differing one is a load error naming both files; a scene without `include:` loads
        exactly as before (every shipped scene's kit, lockfile and run reference unchanged).
        *Done (2026-11-17):* `src/openfactory/core/plant.py` (`merge`, `include_path`,
        `line_of`) and, in `core/schema.py`, the `include` key (`Include`: `area`, `file`),
        `read_scene_text` with `MAX_SCENE_BYTES` (4 MB), and `parse_scene(text, scene_dir, *,
        source, area)` / `load_scene(path, area=None)`, which merge a plant before validating
        it; `Scene.plant` (the files, each component's source file, the area run alone, notes).
        **Each area is validated alone first**, so its errors carry its file and line (the
        error's `loc` walked through the composed YAML); then ids (expanded) and tags are
        compared across files, then composites merged by type name, then the merged scene is
        validated as any scene. **Paths:** absolute (POSIX, drive letter or UNC), a lexical
        `..` out of the folder, and a resolved path outside it (a symlink) are refused, each
        naming the plant file; a plant reaching itself (any spelling, or a symlink) and an area
        including the plant are cycles naming the files; an area with `include:` is refused as
        one level deep. **Decisions beyond the ruling:** (1) a scene size limit did not exist,
        so "the same size limit as a scene" made one, `MAX_SCENE_BYTES` = 4 MB, for every scene
        file and included file (and for text the editor and lint parse); (2) the plant file
        may hold components of its own, which belong to no area (left out of an `--area` run);
        (3) `mesh.file` in an area is rebased onto the plant's folder; (4) `bundle` refuses a
        plant file (bundles are outside 5.4's Done-when); (5) the `--area` cut lives here too
        (5.4b uses it). `include` and `area` are in the schema, `web/public/scene.schema.json`
        and `docs/scene-format.md` ("`include:`"), and are YAML lines for now (4.1k). No
        shipped scene changes; the scene document carries no `include`, so kits and recordings
        are as they were. *Tests* (`tests/unit/test_plant.py`, 15): the plant and an area
        alone; a scene without `include` has no areas; five path rules (absolute POSIX, drive,
        UNC, `..`, `areas/../..`) and a symlink out; an included file over the limit and one
        with a Python tag, each naming the file; three spellings of the plant including itself
        and a symlink to it, cycles naming the files; an area including the plant (a cycle) and
        an area including another (one level); a schema error in an area naming
        `areas/b.yaml: line 10`; an id and a tag in two areas, and an id in an area and the
        plant file, naming both files; one composite in two areas one type, a differing one an
        error naming both files.
  - [x] 5.4b The plant lockfile and `--area` (note §4.2, "One address map", "One area
        alone"). The lockfile is the plant file's; `run`, `test` and `export` take `--area
        NAME`, which loads only that area's components at the plant's addresses, reads the
        plant lockfile and never writes it, refuses an area tag the lockfile lacks, drops a
        cross-area connection with an end outside the area and says so once, grades with the
        area's own `checks:`, and takes the include's `area` as the default `scene.area`.
        *Done when:* a test runs the plant and each area alone, and reads every tag's address
        from the Modbus server: the same address in all three runs, the other areas' addresses
        holes (0 or false); an area run leaves the plant lockfile byte-identical; an unknown
        `--area` is an error naming the areas.
        *Done (2026-11-17):* `scene_register_map` (`protocols/modbus.py`) knows an area run
        (`Scene.plant.area`): it reads the plant file's lockfile, never writes it, and refuses
        a run when the lockfile does not exist or lacks one of the area's tags ("run, test or
        export the whole plant once"); every command that serves or exports addresses goes
        through it. `--area NAME` on `run`, `test` and `export` (`cli.AreaOption`, `_load`) loads
        the plant cut to that area (5.4a's `merge`), logs each dropped cross-area connection
        once, grades with the area's own `checks:`, takes the area's name as `scene.area`, and
        writes no `CONTEXT.md` (it would replace the plant's). The holes behave as the design
        note says, as the Modbus server already treated a retired lockfile entry: 0 or false
        inside a table, ILLEGAL ADDRESS past its end. **Decision beyond the ruling:** `export`
        already had `--area` for the SCADA export's folder (PLAN 2.4); on a plant file it now
        selects the area (whose name is that folder by default), and on any other scene it
        means the folder, as before. Docs: `docs/scene-format.md` ("One area alone").
        *Tests* (`tests/integration/test_plant_addresses.py`, 3): a two-area plant served three
        times (the plant, A alone, B alone), every tag of the plant read from the Modbus server
        at the plant's address with a pymodbus client: each run's own tags read back their
        values, the other area's addresses read 0 or false or are refused past the table's
        end, the addresses equal the plant's, and the plant lockfile is byte-identical after;
        an area run with no plant lockfile, or one lacking a tag, is refused and writes
        nothing; `export --area B` exports only B's rows and leaves the lockfile as it was,
        and `run --area C` exits 1 naming the areas A and B.
  - [x] 5.4c Lint across areas (note §4.2, "Lint across areas"). `lint PLANT` lints the plant
        as one scene; an included file's own `scene`, `plc` and `checks` are reported once per
        file as info; W216 for a file a plant includes with a lockfile beside it; `lint` and
        `validate` on a folder skip the files a plant in it includes.
        *Done when:* a test of each: the info lines, W216, a W205 overlap across the area
        boundary found only in the plant, and a folder lint that lints each area once, inside
        its plant.
        *Done (2026-11-17):* `lint_scene` on a plant file lints the merged plant (5.4a), so every
        check runs across the areas; `_plant_findings` adds `I216` (a new level, info, printed
        `INFO` and never counted toward the exit code) once per included file, and `W216` for an
        included file with a lockfile of either encoding beside it. `cli._scene_files` leaves
        out of a folder the files a plant in that folder includes (`_included`, through 5.4a's
        `include_path`), for `lint` and `validate`. The lockfile checks (W201, W202, E101) read
        the plant's lockfile, as the plant file is the scene path. Docs: `lint.py`'s spec,
        `docs/scene-format.md` ("Lint across areas"). *Tests* (`tests/unit/test_plant_lint.py`,
        4): the two `I216` lines; `W216` for `areas/b.float32.modbus.yaml`; two belts of two
        areas overlapping give W205 in the plant and in neither area; a folder holding a plant,
        its two areas and another scene lints and validates two files, the areas only inside
        the plant.
  - [x] 5.4d The two-area sample plant, held by PLAN §9 rule 13: `scenes/plants/<plant>/`
        with the plant file, two area files, the plant lockfile and the stand-in script; a
        shared composite used in both areas and a connection across them; CI's scene loops
        run the plant and each area (`--area`) under the script; a run reference holds the
        plant; `run_reference.shipped()` learns `scenes/plants/*/`.
        *Done when:* the plant passes its checks at 60 s and 300 s, each area alone passes
        its own at 60 s and 300 s, `tests/references/<plant>.run` is current, and the
        address comparison of 5.4b holds on this sample.
        *Done (2026-11-17):* `scenes/plants/wash-plant/`, a parts-wash line of our own
        invention (no real plant, customer or product): `wash-plant.yaml` (the plant: its
        scene, `plc`, the include of two areas, the connection `CV_101 -> CV_201` across them,
        and seven checks), `areas/load.yaml` (LOAD: carton source EMT_101, infeed belt CV_101,
        photo eye PE_101, dust fan FAN_101; checks alone: `max_cycle_s: 15`, cartons keep
        moving and leave the belt's end), `areas/wash.yaml` (WASH: wash belt CV_201, photo eye
        PE_201, discharge REM_201, make-up supply SUP_201 through on/off valve XV_201 into
        wash-water tank TK_201 with high and low level switches, spray pump P_201 to drain
        DR_201, vapour fan FAN_201; checks alone: the level band, no overflow, no dry run, one
        pump start), the two lockfiles, `wash-plant.CONTEXT.md`, and `wash-plant.py`, one
        stand-in script for the plant and each area (each section runs only when its tags are
        in the run, `"TAG" in io`; the infeed waits for the wash belt only when it is there).
        **Why it exercises the two:** both fans are the library composite `driven_item`,
        declared in each area, so the plant merges one type from two files; the infeed belt in
        LOAD feeds the wash belt in WASH only through the plant file's connection, so cartons
        reach REM_201 in the plant and leave CV_101's end when LOAD runs alone. Results, under
        the script: the plant 7 of 7 at 60 s (12 cartons reached REM_201, none lost, level 0.466
        to 0.600 m) and at 300 s (72 cartons, level 0.399 to 0.901 m as the valve cycles); LOAD
        alone 1 of 1 and WASH alone 4 of 4 at both. **Rule 13:** `tests/references/wash-plant.run`
        (65 KB) holds the plant's run; `run_reference.shipped()` lists each
        `scenes/plants/<plant>/<plant>.yaml` (not its areas), and `build_kit.source` and `record`
        find a plant and copy its folder; CI validates and lints each plant with its script,
        checks its formats with `migrate --check`, and tests the plant and each area (`--area`,
        the areas read from the plant file) at 60 s. *Tests*
        (`tests/integration/test_plant_addresses.py`, 7 more): the sample's address comparison
        over the three runs against its committed lockfile, which no run writes; the plant and
        each area at 60 s and 300 s; `test_runs_unchanged.py` counts 15 shipped scenes and three
        references.
  - [x] 5.4e The editor (note §4.3): `edit PLANT` shows the whole plant with the included
        areas dimmed and not editable ("in areas/x.yaml: edit it with `openfactory edit PLANT
        --area X`", amended 2026-11-18); `edit
        PLANT --area NAME` edits that area's file with the others dimmed, saves only that
        file, and runs the plant-wide lint on save.
        *Done when:* a test saves an edit under `--area` and finds only that file changed and
        a clash with another area refused; the screenshot with the dimmed areas is looked at.
        *Done (2026-11-18):* **How the file is chosen:** `edit PLANT --area NAME` edits the
        area's file as the plant's `include:` names it; `edit PLANT` without `--area` edits the
        plant file itself (its own components and its connections across areas), every area
        dimmed; an unknown area is an error naming the plant's areas, and `--area` on a scene
        that is not a plant an error saying so. `EditSession(path, area)`
        (`server/editor.py`) edits that one file (text, base, operations, source and save are
        that file's); its preview document is the plant merged with the unsaved text in the
        file's place (5.4a's `merge` gained `overrides`, through `parse_scene` and `lint_scene`),
        each component carrying `source` (its file, relative to the plant's folder; a
        composite's part its instance's) and `editable`, and the document `edit: {file, plant,
        area}`. Preview and save lint the **whole plant** with that text, so an id, a scene tag
        or a `plc_tag` another file has is an E100 naming the other file and Save is refused
        (422); a text that would make the plant fail to load is refused with the loader's
        message (`load_error`, word for word) and nothing written. A palette component is
        numbered past every id in the plant. The viewer (`web/src/editor/dim.ts`) draws every
        other file's component dimmed (its own transparent copies of its materials, opacity
        0.25, after the housings attach); selecting one shows "in areas/load.yaml: edit it with
        `openfactory edit wash-plant.yaml --area LOAD`" (amended 2026-11-18) instead of the
        form, with no gizmo, and Delete and the form have no
        data for it, so no edit operation applies; a moved conveyor neither snaps to another
        file's conveyor nor drops a device on its rails (that connection belongs in the plant
        file). The refusal of a5ea0b4 is removed. Docs: `docs/editor.md` ("A plant in several
        files"), `docs/scene-format.md`. *Tests:* `tests/unit/test_plant_editor.py` (8): the
        area's file edited and the whole plant shown marked by file (a composite's parts too);
        the plant file edited with every area dimmed; a save under `--area` after which only
        `areas/wash.yaml` differs among every file of the copy (bytes compared, none added or
        removed); an id of LOAD's (sent as an `add` operation: the form shows a component's
        id read-only, and the palette numbers new ids past the plant's) and a tag of LOAD's
        set on a WASH port, each refused at preview and at save (422) naming
        `areas/load.yaml`, nothing written; deleting the end of the plant's
        cross-area connection refused with the loader's own message; a palette id clear of the
        plant's; the `--area` errors. `web/src/editor/dim.test.ts` (2). Playwright,
        `tests/integration/test_editor_plant.py`: `edit wash-plant.yaml --area WASH` at
        `?quality=low`, LOAD's components dimmed and WASH's not, CV_101 clicked shows the
        message and no form and Delete sends nothing, `PE_101_DI` typed on PE_201 refused
        naming `areas/load.yaml` with Save disabled, CV_201's speed saved, and only
        `areas/wash.yaml` changed. **Screenshot looked at** (`editor-5.4e.png`, quality low,
        written to `$OPENFACTORY_EDITOR_OUT`, never the repository): LOAD's infeed belt, carton
        source, eye and dust fan drawn faint and see-through on the left; WASH's belt, fan,
        tank, pump, valve and discharge solid; the panel head "EDIT MODE · areas/wash.yaml
        (plant wash-plant.yaml; the rest dimmed)".
      *Not in the Done-when, so not built here:* a bundle of a plant (note §4.2, "Bundles");
      `bundle` refuses a plant file, saying so, rather than pack a part of it.
      *Ruled 2026-11-18 (the owner, on review of 268ccf4..f37541e):* confirmed: `edit PLANT`
      without `--area` edits the plant file itself; another file's conveyors are not snap or
      rail-drop targets; new palette ids are numbered across the plant. **New:** the message on
      a dimmed component names the command, not only the file. It replaces the wording quoted
      above and in design note §4.3, both amended: "in areas/load.yaml: edit it with
      `openfactory edit wash-plant.yaml --area LOAD`", and for a component of the plant file
      itself "in wash-plant.yaml: edit it with `openfactory edit wash-plant.yaml`". The editor's
      document gains `edit.areas` (each area's file and name; the edit document only, so no
      recording moves). *Tests:* `dim.test.ts` ("says which command edits another file's
      component", both forms and the plant file edited); `test_editor_plant.py` reads the new
      message.
- [ ] 5.5 Layout from topology. A P&ID has no geometry. `openfactory layout` places equipment and
      routes pipes when positions are missing; a general arrangement drawing overrides it.
      Depends on 4.1g (nozzle positions).
      *Done when:* the sample P&ID's scaffold, with no positions, lays out with no overlap finding,
      and the screenshot is looked at.
      *What exists today (2026-11-15):* every component needs a `position`. 4.1e's placement on
      Add runs in the browser (`web/src/editor/place.ts`, pure maths over footprints taken from
      what the viewer drew): a new component that overlaps the plant moves to the nearest clear
      spot (0.1 m steps, up to 15 m, never past the 20 m floor edge), and a new conveyor near an
      open end snaps onto it; nothing in Python looks for a free spot, and the overlap lint
      (W205) covers conveyors only. 4.1g1 placed devices on a belt's rail in Python
      (`core/mounts.py`, `place_on_rail`). 4.1g2 gave a tank's nozzle an optional fixed point on
      the wall (`{height, bearing_deg}`, `core/snaps.py` `nozzle_wall`) and published pipe ports
      for every process type; a nozzle without a bearing is still drawn facing its link. A pipe
      is drawn as straight runs from its `from` end through its `path` points to its `to` end, a
      valve or pump as a body at its origin with runs to its two ends; there is no pipe routing,
      and a pipe's default length (its friction) is its drawn length.
- [ ] 5.6 Review overlay. A top view of the scene drawn over the layout drawing, and a table of
      takeoff rows against scene components, so the checker sees what was missed.
      *Done when:* removing one item from the sample scene shows it in both.

#### Phase C: equipment found in a plant (order set by 5.0)
- [ ] 5.7 Bulk solids. ADR first: material as a mass flow, not as parts. Then hopper and silo
      (level, load cell), screw conveyor, bucket elevator, rotary airlock, a belt carrying bulk.
- [ ] 5.8 Air and heat. ADR first: temperature added to the flow network. Then fan or blower,
      damper, heat exchanger, dryer, burner. A simulated burner is a training picture only: no
      burner management, no flame safety, and the scene says so.
- [ ] 5.9 Process fill-ins. Check valve, relief valve, agitator, filter, and flow, pressure and
      temperature kinds of transmitter where missing.
- [ ] 5.10 Discrete fill-ins. Curve, incline, scale or checkweigher, code reader, stack light,
      emergency-stop station (a plain input, not safety-rated). As composites where possible.
- [ ] 5.11 Plant surroundings. `scenery`: structure, platforms, panels, motors, building outline,
      with no I/O and no physics, placed from the layout drawing.

Each type in 5.7 to 5.11 has the usual Done-when: a unit test, a docstring spec, a section in
`docs/components.md`, and one shipped scene that uses it and passes at 60 s and 300 s.

#### Phase D: size and hand-over
- [ ] 5.12 Plant-size scenes. Finish 2.9; then a 300-component, 1,000-tag sample holds its tick
      rate with one viewer, numbers recorded.
      *Note (2026-11-16, review of 5.14a):* a live run now copies the whole tag table every
      tick for the change log (`IoLog.feed`, PLAN 5.14a; +0.06 to +0.07 ms a tick on
      `load-200`, which has 21 tags and 190 parts). Re-measure that copy and the diff at 1,000 tags as part of
      this task, and say whether it needs to become a list of the tags written that tick.
- [ ] 5.13 Authoring guide for assistants. `docs/authoring-from-drawings.md` (public): the steps,
      the takeoff file, what to ask the person. The HIS skill that runs it on a customer's
      drawings lives outside this repository.

      *Carried from 5.1c (2026-11-16):* the guide shows the sample's PNGs,
      `docs/samples/wash-skid/P-001.png` and `L-001.png`.
#### Seeing the plant together
- [ ] 5.14 Seeing the plant together (ruled 2026-11-15). *Goal:* while a PLC program is being
      written, the person and the assistant see the same plant at the same moment, and both can
      answer "what happened and why". The person watches the viewer; the assistant reads text
      and still images. Every sub-task gives both of them something.
      *What exists today (read from the code, 2026-11-15; two lines corrected from the draft,
      marked):*
      - `GET /api/state`: the last tick's snapshot: `t`, `tick`, `components` (each one's
        `state()`), `parts` (id, name, size, position, velocity, `carrier`, `spawn_tick`,
        `spawn_position`, `follow`, `color`, and `yaw_deg` when turned) and `io` (every tag).
        *(Corrected: the draft listed only some of a part's fields.)*
      - `GET /api/events`: the last 1000 runtime events: injected and cleared faults, the comms
        fault, exam switches, viewer button presses and selector turns, and the browser
        physics' own `sensor` and `part` reports, which are kept for parity and never change a
        tag. No tag changes. The core's own raw beam edges are in `GET /api/parity`, not here.
        *(Corrected: the draft said it holds no sensor edges and left out the browser's
        reports and the comms fault.)*
      - `GET /api/history`: AI tags only, 2 Hz of simulated time, ten minutes. No history of a
        DI, DO or AO in a live run, and none in a replay.
      - `GET /api/checks`: each check `pending`, `passed` or `failed` with one sentence of
        detail. A failure does not say when it happened or which part or component caused it
        (`CheckResult` is name, passed, detail, settled).
      - A recording (`run --record`, `.ofrec`, 2.12) holds every tag change at every tick, a full
        state frame every 5th tick (10 Hz at 50 Hz) and every event. `openfactory replay` plays
        it in the viewer at a speed (`--speed`, `--loop`, `--exit-at-end`), and `--io` prints
        the change log. It cannot pause, seek or step.
      - A live run cannot pause or step. (`openfactory edit` holds a scene whose simulation is
        never started; that is not a pause of a run.)
      - `tools/screenshot.py` runs a scene to a simulated time and saves one image from the
        default framing. It has no camera option. The viewer has `framing.fitPose` and
        `boxInView` (4.1j) and a test hook that reads the camera position
        (`__openfactoryCamera`); nothing sets a view from outside. The only URL parameters are
        `quality` and `trend`.
      - Labels show a component's id, and a fault marker. They show no tag and no value.
      - A stand-in script is `step(io, t)`. Its own state (which step of a sequence it is in)
        is visible to nobody.
      *Ruled 2026-11-15, all six as the draft recommended:* (1) order: 5.14a and 5.14b first
      (text, no browser), then 5.14c and 5.14d, then 5.14e, 5.14f and 5.14g, then 5.14h, 5.14i
      and 5.14j; (2) captures go to `openfactory-captures/` beside where `openfactory run` was
      started; (3) the 60 s rolling buffer is always on in a live run, and its cost is measured
      in 5.14e and reported; (4) pause with a real PLC attached is allowed, with the banner;
      (5) the strip image is joined in the browser, and no image library is added; (6) 5.14a
      and 5.14b are built now, before the Phase B tasks, because the pilot (5.0) will use them;
      the rest after 5.1 to 5.3.
      *Ruled 2026-11-18 (review of e191c3b..1d4510b):* ruling (3) now reads "the 60 s rolling
      buffer is **always on while a viewer can connect**": it is off in a run with no viewer
      (`--headless`, `openfactory test`), as 5.14e built it. With a viewer, a snapshot every 5th
      tick is allowed on the tick path, which amends 2.9's rule (note under 2.9). The decisions
      5.14d, 5.14e and 5.14f made beyond their text are confirmed (each noted under its task).
  - [x] 5.14a Tag change log in a live run. *What:* a ring of every tag change (tick, simulated
        time, tag, value, and who wrote it: the simulation or the PLC), the last 600 s or
        20,000 changes, whichever is smaller; `GET /api/iolog?since_tick=N&tags=A,B`.
        *How:* `src/openfactory/core/iolog.py`, fed after each tick from the same change set the
        recorder makes, outside the step lock, with memory fixed by the cap; a replay serves the
        same endpoint from the recording.
        *Done when:* a unit test runs the sorter 30 s and finds `PE_01_DI`'s rising edge and
        `CYL_01_EXT_DO`'s within the ticks the recording gives; the buffer never grows past its
        cap in a 600 s run; the tick time at 200 parts moves by less than 0.2 ms (measured and
        recorded here).
        *Done (2026-11-16):* `src/openfactory/core/iolog.py`: `IoLog`, a ring of `(tick, tag,
        value)`, capped at the last 600 s (`WINDOW_S`) or 20,000 changes (`MAX_CHANGES`),
        whichever is smaller, with `dropped_to` (the newest tick a dropped change had), so a
        reader is told `complete: false` when a change it asked for is gone. Time is `tick *
        dt` and the writer comes from the tag's kind (`simulation` for DI and AI, `plc` for DO
        and AO, `WRITER_FOR_KIND`), so neither is stored. **One diff:** `iolog.changed` is now
        the function the recorder uses for its I/O lines too, so the log and a recording are
        the same change set by construction. **The tick path:** under the step lock a tick
        only copies the I/O table's values (`IOTable.values()`, or the snapshot's while
        recording); the diff and the append run after the lock is released, under the log's
        own lock, and no snapshot is built (2.9's rule). The first tick also logs every tag's
        value before it, as a recording's tick 0 does. `Runtime.iolog_query` and `GET
        /api/iolog?since_tick=N&tags=A,B`: `{dt, tick, since_tick, first_tick, complete,
        changes: [{tick, t, tag, value, writer}]}`, 404 for a tag not in the scene, GET only,
        behind the server's usual loopback bind and Host check. A replay builds an uncapped log
        from the recording (`IoLog.from_changes`) and serves it up to the tick being shown.
        Documented in the app's docstring and in `docs/guides/README.md` ("Every tag change").
        **Tick time at 200 parts** (2026-11-16, i7-1260P, CPython 3.11.15): `Runtime._step` on
        `tests/scenes/load-200.yaml` with every DO forced on (as `tools/bench.py` sets it up),
        warmed 3000 ticks (190 parts on the belts), the code before this task (`runtime.py` at
        39aee6a, loaded from git into the same process) and after it, in alternating blocks of
        50 ticks in one session. Run 1, 40 blocks each: before 3.070 ms, after 3.126 ms
        (+0.057 ms). Run 2, 80 blocks each: before 3.481 ms, after 3.554 ms (+0.073 ms). Under
        the 0.2 ms limit both times. The two runs differ from each other by more than the change
        (the machine's own drift, 2.9), which is why the comparison alternates within a run.
        `tools/bench.py` times `LoadedScene.step`, not `Runtime._step`, so its baseline does
        not see this cost and is not touched.
        *Tests* (`tests/unit/test_iolog.py`, 7): the sorter under its stand-in script for 30 s
        logs every change its kit recording holds for ticks 0 to 1500, tick for tick, which
        includes `PE_01_DI`'s and `CYL_01_EXT_DO`'s rising edges, with their writers; `since_tick`
        and `tags` filter, and an unknown tag is a KeyError naming it; a 600 s run of the sorter
        keeps within the cap at every tick (it held 2,037 changes at the end, the window having
        dropped tick 0's); the cap by count and by time, and a tick with more changes than the
        ring; `GET /api/iolog` live (404 for an unknown tag, 405 for a POST) and in a replay
        (stops at the tick shown, every recorded change up to it). Recordings, kits
        (`build_kit.py --check`: all 12 current), run references, parity traces
        (`parity_trace.py --check`) and lockfiles did not move.
  - [x] 5.14b The plant in words: `openfactory look`. *What:* a text report of the plant now, or
        at a time in a recording: one line per component in plain words with its tags and
        values; each part and where it is ("P000007 tall, on CONV_01, 1.84 m from the infeed,
        moving 0.5 m/s"); what changed in the last N seconds; the checks; active faults.
        Markdown, under 150 lines for a 15-component scene; `--component ID` and `--since S`
        narrow it. *Why first:* text is the cheapest and most exact thing an assistant can read,
        and it needs no browser.
        *How:* `src/openfactory/observe.py`, a pure function of the scene document, a snapshot
        and the change log. Each component type gives its own sentence through a `describe`
        beside `state()`. `GET /api/look` (text) on a running server; `openfactory look [--url
        URL]` and `openfactory look FILE.ofrec --at T`.
        *Done when:* every registered type has a `describe` (a test fails for a type without
        one); the report of the sorter at 6.0 s from its kit recording is a committed golden
        file; the same call on a live run and on its recording at the same tick gives the same
        text.
        *Done (2026-11-16):* `src/openfactory/observe.py`: `look(document, snapshot, changes,
        plant)`, a pure function of the scene document (as a recording's header stores it,
        `scene_document(meshes=False, snaps=False)`), one snapshot, the change log up to its
        tick, and a `Plant`, the two things the document lacks: the declared `checks:` and each
        link's `from`/`to`. A live run takes them from the parsed scene, and a recording from
        the scene's YAML in its header. It reads no clock and prints no path, host, port or
        version. **Sections, in order:** the scene in one line; checks; active faults;
        components (the type's sentence, then each bound tag and value, then tags no component
        owns, such as the heartbeat); parts (id, name, carrier, distance from that carrier's
        infeed end in its own belt frame, which turns with a turntable's deck and rises with a
        lift's, and speed; a part carried by nothing is held by a gripper, falling, or at rest,
        with its position; 30 listed, the rest counted by carrier); changes in the last N s
        (default 5, `--since`; DI and DO edges one per line up to 40; an AI or AO, or a DI or DO
        with more than 4 changes in the window such as the heartbeat, on one line: first and
        last value and the count; tick 0's starting values left out). `--component ID`: that
        component, what it is connected to (conveyor connections, link ends, references such as
        `measures`, `tank` or `drives`, and devices standing on a belt by W214's footprint
        rule), the parts it carries and its tags' changes. **Decisions:** (1) `describe` is a
        classmethod, `describe(state, geometry)`, beside `state()`: a recording has no
        component objects, so the sentence is made from the state and the component's entry in
        the scene document, nothing else; `Component.describe` returns "" and each of the 28
        registered types overrides it (`roller_conveyor` inherits `belt_conveyor`'s); a
        `core.component.num` formatter keeps numbers fixed and never prints `-0.00`. (2) The
        checks section says what each declared check asks and what the snapshot shows of it now
        (each remover's count of each name, the oldest part's age, a tank's level or
        temperature, overflowing or dry), and says the grade is `openfactory test`'s (live,
        `GET /api/checks`) at `duration_s`. It does not grade: grading needs the removal log,
        lost parts and a pump's starts, which a snapshot does not hold, and a recording holds
        no grades. So the report is the same live and from a recording; 5.14g's `at_tick` is
        where a check's failure will get a time. (3) `GET /api/look?since=S&component=ID` is
        `text/markdown`, GET only, 404 for an unknown component and 400 for `since` <= 0. It
        waits up to 1 s for the change log to catch up to the snapshot's tick, since the log is
        fed just after the step lock is released (5.14a). A replay serves it at the frame shown.
        (4) `openfactory look [FILE.ofrec] [--at T] [--url URL] [--since S] [--component ID]`:
        with a recording, the frame at or before T (the last frame without `--at`); without one,
        `GET /api/look` on the `[web]` endpoint. (5) The generated `CONTEXT.md` gains "Looking
        at the plant" with the two commands for its scene; the 2.19 golden files are renewed in
        this commit: the 13 `scenes/**/*.CONTEXT.md` and the 12 kits' copies (each kit's
        `CONTEXT.md` is a kit file, so `build_kit.py --check` needed them; no recording, export,
        lockfile or other kit file moved). `tests/scenes/*.CONTEXT.md` are git-ignored. The rest
        of 5.14j waits for 5.14c to 5.14g. Docs: `docs/looking-at-the-plant.md` in the site's
        Reference.
        **Line counts** (`openfactory look`, whole report, at 6.0 s and at 60 s from each kit
        recording; `accumulation-demo`, which has no kit, from a 60 s recording made in scratch
        under its script): batch-mix-heat (11 components) 49 and 46; buffer-and-release (7) 42
        and 57; conveyor-start-stop (6) 39 and 39; inline-reject (7) 41 and 42; palletizer-lite
        (9) 54 and 68; plantpax-tank-level (10) 45 and 45; sorting-by-color (8) 45 and 50;
        sorting-by-height (7) 41 and 54; tank-level-onoff (6) 37 and 37; tank-level-pid (7) 39
        and 39; two-lane-merge (13) 57 and 72; two-tank-cascade (10) 45 and 45;
        accumulation-demo (11) 48 and 43. No shipped scene has 15 components; the largest,
        two-lane-merge with 13, is at most 72 lines, under the 150-line limit.
        *Tests* (`tests/unit/test_look.py`, 11): every registered type overrides `describe`, and
        each gives one line for a real state (a component from every scene in `scenes/` and
        `tests/scenes/`; `junction` and `vfd_motor`, which no scene uses, from a state of their
        `state()`'s shape); the sorter at 6.0 s from its kit recording equals the golden file
        `tests/golden/look-sorting-by-height-6.0s.md` (41 lines); a live scripted run of the
        sorter and its own recording give the same text at 6.0 s and, narrowed to `CONV_01`
        with a 1 s window, at 4.2 s, `GET /api/look` serves that text as `text/markdown`, and
        it equals the golden file; `--component` narrows (`CYL_01` is connected to `CONV_01`);
        404, 400 and 405; the CLI on a recording, `--at` without one (exit 2), an unknown
        component (exit 1); a turntable mid-turn and a lift mid-move give the frame
        `KinematicModel._frame` carries parts in; a part held, falling or at rest. Kits:
        `build_kit.py --check` current; run references unchanged.
        *Line count at the size limit:* no shipped scene has 15 components, so the stand-in is
        `tests/scenes/load-200.yaml` (30 components, every belt and emitter forced on, no
        script): 103 lines at 6 s (60 parts), at 30 s (200 parts, the cap) and at 60 s (190
        parts), with 30 parts listed and the rest counted by carrier. Twice the components of the
        limit, it stays under 150 lines.
        *Review fixes (2026-11-16, the owner's reviewer on 857f0c3):* (1) **Exam mode.** An exam
        run's snapshot hides faults, so the report said "Active faults: None." while a fault was
        active. `look` now takes `exam`: in exam mode the section reads "Faults are hidden in
        exam mode." and nothing else. A live run passes it from `loaded.exam`, and a recording
        made with `--exam` carries `"exam": true` in its header (written only then, so no other
        recording moves) for `look FILE` and a replay. Checked first: the report and the change
        log show a trainee nothing `/api/state` does not, since `look` reads the same
        exam-filtered snapshot (a misaligned eye's offset reads 0) and the log holds only the
        tag values `/api/state` serves. (2) **A window longer than the log.** When the window
        starts before the newest change the live log has dropped (`IoLog.dropped_to`, read
        with the entries under the log's lock by the new `IoLog.held`), the heading gives the
        time the listing really starts from: "Changes after 18.00 s, up to 30.00 s (the last
        30.0 s were asked for; older changes are no longer held)". A recording holds every
        change and is never short. (3) The two attribute comments in `Runtime.__init__` each
        sit on their own attribute. The golden file did not move: the sorter's kit recording is
        not an exam run and its log is complete. *Tests* (3 more in `test_look.py`): an exam run
        of the sorter with its eye misaligned by 0.137 m at 2 s, where `look`, `look
        --component PE_01`, `GET /api/look` and `GET /api/iolog` show neither `misaligned` nor
        `0.137` and the log's last values equal `/api/state`'s, while the same run without exam
        mode lists `PE_01: misaligned` and the offset; `run --exam --fault ... --record` then
        `look` on the recording; a 40-change cap over a 30 s run, asked for 30 s.
  - [x] 5.14c Aimed views and a plan view. *What:* `?look=ID` frames one component and what
        touches it; `?view=px,py,pz,tx,ty,tz` sets an exact camera; `?view=top` is a straight-down
        orthographic plan view with north up and a scale bar. `tools/screenshot.py` gains
        `--look-at ID`, `--view ...` and `--view top`. A "Copy view" control puts the current
        view's URL parameters on the clipboard.
        *How:* `framing.fitPose` over one component's drawn box plus its connected neighbours,
        grown 30 %; the plan view is a second camera, not a new renderer.
        *Done when:* a Playwright test opens `?look=CYL_01` and finds CYL_01's screen box inside
        the window and filling at least a fifth of its width; `--view top` of the sorter puts
        every component on screen with no perspective (two points 1 m apart are the same pixel
        distance at both ends of the plant); screenshots looked at.
        *Done (2026-11-18):* `web/src/views.ts` (DOM-free, `views.test.ts`, 8): `parseView`
        takes six finite numbers (position and target apart) or the word `top`, anything else
        the default view with a warning, never an exception; `lookTargets` is the component (a
        composite instance's parts with it), the components a `connections:` entry joins it to,
        link fields when the document carries them, and `linkTouches`: a pipe, valve or pump
        whose drawn end (`points`' first or last) is within 5 cm of a component's drawn box
        (the scene document carries a link's `points`, not its `from`/`to`, and adding them
        would move every kit and run reference, so the geometry decides); `grow` 30 %; the
        plan view's frame (`planFrame`, `aimPlan`: straight down, `up` -z, so north, the
        scene's -z as `rotation_deg` turns, is up and +x right); `scaleBar` (1, 2 or 5 times a
        power of ten metres, the first at least 80 px); `viewParam`. `main.ts`: `?look=` frames
        with `framing.fitPose` over the grown box; `?view=` poses the camera; `?view=top`
        switches to a second, orthographic camera with its own controls (pan and zoom, no
        orbit) that the same renderer draws, picks and projects through, and shows the scale
        bar with "N ↑"; the editor ignores `?view=top` with a warning (its gizmo works in the
        3D camera); with both, `?view=` wins; "Copy view" (bottom left) copies `view=...` (the
        clipboard, and `window.__openfactory.copiedView`). `tools/screenshot.py`: `--look-at
        ID`, `--view px,py,pz,tx,ty,tz`, `--view top`, printing the view and the viewer's
        warnings. Docs: `docs/looking-at-the-plant.md` ("Aimed views and a plan view").
        **The two measurements** (`tests/integration/test_views.py`, 1280 by 720, quality low):
        `?look=CYL_01` puts CYL_01's screen box (its drawn box's corners, projected) at 474 to
        756 px across and 199 to 582 px down, inside the window and 282 px wide, 22.1 % of the
        width (at least a fifth); `?view=top` of the sorter puts every component's screen box in
        the window, and 1 m along x is 253.613 px at the west end, at the east end, 1.5 m up,
        and across (z), equal to 0.01 px, with -z up and +x right, the scale bar's px per metre
        the same. The same tests: five bad `?view=` values and `?look=NOPE` each the default
        camera with a console warning and no page error; Copy view's value reopens the same
        camera (to 2 mm) and in the plan gives `view=top`. **Screenshots looked at:** the
        sorter's look (the pusher, its housing and plate over the running belt, filling the
        middle of the window) and plan (both belts, the eye's beam across CONV_01, the pusher
        above it, the removers, a 500 mm scale bar); the wash plant's `?look=TK_201` (the tank
        with its level, XV_201 and its line from SUP_201 above, P_201 and its line to DR_201
        beside it) and `--view top` (the two belts end to end, the two fans below them, the
        supply, valve and tank above; the checks panel at the top right covers P_201 and DR_201
        in the plan, as it covers that corner in every view, and the fans' two labels overlap,
        which is 5.14d's to fix).
        *Ruled 2026-11-18 (the owner, on review of 268ccf4..f37541e):* confirmed: "what touches
        it" also counts a pipe, valve or pump drawn within 5 cm; north is the scene's -z;
        `?view=` wins over `?look=`; a `?view=` whose position equals its target falls back to
        the default; the editor ignores `?view=top` with a warning.
        *Fix (2026-11-18, the review's item 3):* the checks panel covered the top right of every
        picture. `?panels=off` (`views.ts` `panelsShown`; a class on `<html>` whose `!important`
        rules hide `#checks-panel`, `#exam-panel`, `#trend-panel` and `#trend-button`, so the
        panels' own refreshes cannot show them again) and `tools/screenshot.py --no-panels`
        leave them out; the status lines, the scale bar, the north mark and the bottom-left
        controls stay. *Tests:* `views.test.ts` (`?panels=off`); `test_views.py::
        test_panels_off_leaves_the_panels_out_of_the_wash_plants_plan` (the panels hidden
        after the checks panel's polls, the scale bar and "N ↑" shown, P_201 and DR_201 on
        screen, and with `?panels=on` the checks panel's box over one of them). **Re-taken and
        looked at:** the wash plant at 10 s, `--view top --no-panels`: SUP_201, XV_201, TK_201,
        P_201 and DR_201 along the top with nothing over them, the two belts end to end with
        three cartons, the two fans below, the scale bar (1 m) and "N ↑ plan view" at the
        bottom left. The fans' two labels still overlap (5.14d).
  - [x] 5.14d Values in the picture. *What:* a labels control with three settings: ids (today),
        tags (each component's bound tags with live values beside it), off. URL `?labels=tags`;
        `tools/screenshot.py --labels tags`.
        *How:* the scene document already lists each component's bindings; values come from the
        frame's `io`. Text in the normal colour; alarm colours stay for faults only (the
        ISA-101 rule in `isa101.ts`). Labels that would overlap are stacked, and past 40
        components only the selected component and its neighbours show values.
        *Done when:* a Playwright test reads CYL_01's label and finds `CYL_01_EXT_DO` with the
        value `/api/state` gives at that tick; no two labels overlap in the sorter at 1280 px
        (boxes compared); screenshot looked at.
        *Done (2026-11-18):* `web/src/labels.ts` (DOM-free, `labels.test.ts`, 6): `parseLabels`
        (`ids` by default; a value it does not know is `ids` with a console warning);
        `boundTags`, each component's own tags from the document's `tags` (their `owner`), so
        nothing is added to the scene document and no recording, kit or run reference moves;
        `formatValue` (a bool `true` or `false` as `/api/state` gives it, a number to 3
        decimals, `–` for a tag the frame has no value for: no value is invented);
        `labelLines`; `showsValues` (past 40 components only the focus); `stackLabels`, a pure
        function of the label boxes: taken top to bottom, then left to right, then by id, each
        label moves down below every placed label it would overlap (2 px apart), so the same
        picture stacks the same way every frame. `main.ts`: a label is an outer element the
        CSS2D renderer places (it rewrites that transform every frame) and an inner one with
        the text, which the stacking shifts (`translateY`), read from the outer boxes at most
        every 100 ms or after a text change, labels the renderer hides left out; the labels
        control (bottom left, beside Copy view) and `?labels=`; values from each frame's `io`,
        `window.__openfactory.labelsTick` the tick they are from; a fault keeps its alarm
        colour and marker in every setting. `tools/screenshot.py --labels ids|tags|off`. Docs:
        `docs/looking-at-the-plant.md` ("Values in the picture").
        **Decisions beyond the text:** (1) `off` still shows a faulted component's label (its
        id and the fault), so the setting never hides a fault. (2) The selected component, past
        40 components, is `?look=`'s, else the last component clicked in the viewer (the live
        viewer had no selection). No shipped scene has more than 40 components, so this path is
        held by `labels.test.ts` only.
        *Confirmed 2026-11-18 (owner's ruling): both decisions. Held in the browser since then
        by `tests/integration/test_labels.py::test_past_40_components_only_the_focus_and_its
        _neighbours_show_values` on a 42-belt scene the test writes in its own folder.*
        **The Done-when, measured** (`tests/integration/test_labels.py`, 4, 1280 by 720,
        quality low): the sorter's kit recording replayed to its end (which holds its last
        frame): `/api/state` at tick 3000 has `CYL_01_EXT_DO` false and CYL_01's label, drawn
        from tick 3000, reads `CYL_01` / `CYL_01_EXT_DO false` / `CYL_01_EXT_DI false` /
        `CYL_01_RET_DI false`; a live scripted run, the label's text and tick read together:
        `CYL_01_EXT_DO false` at tick 303 and `true` at tick 430, the change log (the values
        `/api/state` serves) having False and True at those ticks. No two labels overlap
        (each label's drawn box against every other): the sorter at 6 s, 7 labels in tags and
        in ids, none in off; the wash plant's plan view (`--view top --no-panels`), 15 labels
        in tags and in ids, the fans' `FAN_101_BODY` / `FAN_101_HOA` and `FAN_201_BODY` /
        `FAN_201_HOA`, which overlapped in 5.14c, apart. **Screenshots looked at:** the sorter
        in tags (EMT_01 and CONV_01 stacked at the infeed, PE_01, CYL_01 with its three tags,
        CONV_02 below PE_01, REM_01 and REM_02 by their boxes; the checks panel at the top
        right as usual); the wash plant's plan in tags (TK_201's level and switches over the
        tank, XV_201 and P_201 stacked below the line, both belts' labels, each fan's HOA
        selector with its three inputs above its body's start command) and in ids (each fan's
        BODY label with its HOA label one row below, apart).
  - [x] 5.14e Capture: "show the assistant what I see". *What:* a Capture button (and the C key)
        writes a folder holding the picture as the person sees it, the camera, the selected
        component, a typed note ("box jammed here"), the `look` report at that tick, the full
        snapshot, and the last 60 s as a recording. `openfactory captures` lists them, newest
        first, and prints the newest folder's path.
        *How:* the live runtime keeps the last 60 s of frames and tag changes in memory (600
        frames at 10 Hz, in the recorder's format, so the piece written is a valid `.ofrec`).
        `POST /api/capture` takes the image and the camera; the server names the folder itself
        (`openfactory-captures/<scene>/<tick>/` beside where `openfactory run` was started, ruling
        2; git-ignored; the base set by `--capture-dir`). The buffer is always on (ruling 3);
        its cost is measured here and reported.
        *Security:* a write endpoint. Loopback only, Origin and JSON checked as the edit
        endpoints are (4.1c fix 6), the image capped at 5 MB, no client-supplied path or file
        name, off in exam mode for a trainee.
        *Done when:* a Playwright test presses Capture during a sorter run and finds the folder
        with all six files; the recording in it replays and ends at the capture's tick; a
        request from another Origin and one over the size cap are refused; the note appears in
        `report.md`; the buffer's cost per tick at 200 parts is measured and recorded.
        *Done (2026-11-18):* `src/openfactory/capture.py`: `CaptureBuffer`, the last 60 s of
        state frames, one every `FRAME_EVERY` (5) ticks, 600 at 50 Hz; the frame is the
        snapshot built on that tick under the step lock (as while recording) and kept as
        `Runtime._snapshot`, so a viewer reading that tick reuses it; the tag changes between
        frames are the change log's (5.14a, 600 s) and the events the runtime's event log's, so
        the buffer stores frames only. `piece_lines` writes the recorder's own format: the
        header `run --record` writes (now `record.recording_header`, shared by both, so a
        recording is byte for byte what it was), a first frame whose `io` is the whole table
        (as tick 0's is), the log's changes, a frame every 5 ticks, the events, the snapshot at
        the capture's tick as the last frame (it is rarely a multiple of 5) and an end record
        `{"capture": {"from_tick", "tick"}}`; when the log no longer holds every change after
        the first frame, the piece starts at the first frame after the newest dropped change.
        `write_capture` writes the folder. **The six files** (the What names seven items; the
        selected component goes in `view.json` and `report.md`): `view.png`, `view.json`
        (camera, `view=` value, selected component, labels setting, the picture's tick),
        `report.md` (scene, time, tick, selected component, the note), `look.md` (`look` at the
        tick, then `look --component` of the selected one), `snapshot.json`, `last-60s.ofrec`.
        `Runtime.capture`; `POST /api/capture` (`server/app.py`); `run --capture-dir` (default
        `openfactory-captures/` in the folder `run` starts from, resolved at start, ruling 2);
        `openfactory captures [--capture-dir]` (newest first by `report.md`'s time, then the
        newest folder's path; exit 1 with none); `.gitignore` gains `openfactory-captures/`.
        Replays: `ReplayRuntime` times playback from the first frame's tick (a piece starting
        at tick 30,000 played nothing for ten minutes before), and `look` on a piece (live in a
        replay and `look FILE --at T`) treats its first tick as the start of the log
        (`Recording.held_from`), so the whole table on its first line is not listed as changes;
        a whole run's first tick is 0, so its reports, the golden file included, are unchanged.
        The viewer (`main.ts`): Capture (bottom left) or C takes the picture at once, then opens
        the note box (Enter saves, Shift+Enter a new line, Esc cancels); the picture is the
        WebGL canvas drawn that moment with the labels, the status lines and the plan view's
        scale bar drawn onto it (they are page elements); the panels are left out. The button
        is hidden in the editor, a replay, and in exam mode outside the instructor view.
        Docs: `docs/looking-at-the-plant.md` ("Capture: show the assistant what you see").
        **Decision beyond the text:** the buffer is off in a run with no viewer (`--headless`,
        `--as-fast-as-possible`, `openfactory test`): nothing could capture it, and ruling 3's
        "always on in a live run" is read as every run with a viewer. *Confirmed 2026-11-18 (owner's ruling):* the buffer is off in a run with no viewer
        (ruling 3 amended to "always on while a viewer can connect"); with a viewer a snapshot
        every 5th tick on the tick path is allowed (2.9 amended); the captured picture leaves
        the panels out; 5 MB means 5 MiB (5,242,880 bytes, as `MAX_IMAGE_BYTES` is); six files,
        the selected component in `view.json` and `report.md`.
        *Fixed 2026-11-18 (review fixes):* a held C (auto-repeat) types nothing into the note,
        and an editable element is a text field too; a second capture cannot start while one is
        written (the viewer waits for the answer, and the server refuses a second with 409:
        `test_capture_viewer.py::test_the_c_key_writes_one_capture_held_none_typed_and_none
        _while_one_is_written`, `test_capture.py::test_a_second_capture_cannot_start_while_one
        _is_being_written`); a `selected` id that is not a component is named not found in
        `report.md`, one line, escaped, and is in neither `look.md` nor `view.json`
        (`test_an_unknown_selected_id_is_named_not_found_and_never_reaches_look`). The docs say
        a capture is as private as the plant and give the buffer's memory: 2.6 MB full at the
        sorter's 2 parts (tracemalloc, 3000 ticks, 600 frames, 2026-11-18), 95.6 MB at 200.
        **What this changes of
        2.9:** a tick no longer builds no snapshot: with a viewer, every 5th tick builds the
        frame the buffer keeps. `test_snapshot_off_tick.py` keeps 2.9's assertion for a run
        without the buffer and gains `test_the_capture_buffer_builds_one_every_5th_tick_and_a
        _reader_reuses_it`. A recording's bytes are unchanged (`build_kit.py --check` current
        for all 12 kits, `parity_trace.py --check` and `test_runs_unchanged.py` pass).
        **Done-when, each with its test:** Capture pressed during a sorter run finds the folder
        with all six files (`tests/integration/test_capture_viewer.py::
        test_capture_during_a_sorter_run_writes_the_six_files`: the C key opens the note box and
        Esc writes nothing; the button, a note, Enter; 1280 by 720 picture, not blank); the
        recording replays and ends at the capture's tick (the same test, and `tests/unit/
        test_capture.py::test_the_piece_replays_and_ends_at_the_capture`, which also runs
        `openfactory look` on it, and `test_the_piece_is_the_whole_recording_over_the_last_60
        _s_and_ends_at_the_capture`: the sorter run 75 s under its script and recorded whole,
        captured at tick 3750: the piece's 600 frames, ticks 755 to 3750, and every change
        after its first frame equal the whole recording's, its first line the whole table); a
        request from another Origin (403) and over the size cap (413) are refused
        (`test_another_origin_a_form_and_a_request_off_loopback_are_refused`,
        `test_a_picture_over_5_mb_is_refused_before_and_after_decoding`); the note appears in
        `report.md` (the integration test, `<b>#1</b>` escaped, and
        `test_a_capture_is_six_files_in_a_folder_named_by_the_server`).
        **Security, each with its test** (`tests/unit/test_capture.py`): loopback only, a
        server bound to 0.0.0.0 refuses with 403 and writes nothing (`test_another_origin_a_form
        _and_a_request_off_loopback_are_refused`, which also holds the Host check's middleware
        in place); Origin and JSON checked as the edit endpoints are, by the same middleware:
        another Origin 403, `text/plain` 415, this server's own Origin accepted (same test); the
        image capped at 5 MB: a body that could hold more is refused (413) before it is parsed,
        by `Content-Length` and while it is read, and 5 MB plus one byte decoded is 413 while
        exactly 5 MB is taken; a data URL that is not PNG, or bytes without the PNG signature,
        400 (`test_a_picture_over_5_mb_is_refused_before_and_after_decoding`,
        `test_no_client_path_or_file_name_and_only_a_png`); no client-supplied path or file
        name: the request model forbids any other key (`path`, `folder`, `filename` each 422,
        nothing written), the folder is `<base>/<scene>/<tick>` with the scene's name reduced to
        letters, digits, `-` and `_` (`scene_slug("../../evil name")` is `evil-name`), `-2`
        after a tick captured already (`test_no_client_path_or_file_name_and_only_a_png`,
        `test_escaping_and_cleaning_rules`, `test_two_captures_of_one_tick_get_two_folders`);
        off in exam mode for a trainee: 403 without the fault token or with a wrong one, and the
        instructor's token accepted (`test_off_in_exam_mode_for_a_trainee`). **The note is
        data:** cut to 2000 characters, control characters but newline and tab dropped, `&`,
        `<` and `>` as entities and every Markdown punctuation character behind a backslash,
        each line quoted; a selected id that is not a component is left out
        (`test_the_note_is_data_escaped_in_the_report_and_never_a_path`, five notes: a fence,
        a heading and a script element; `../../../etc/passwd`; `$(touch pwned)` with backticks
        and a redirect; a link, an image and a table row; CR, NUL and an escape sequence: only
        the folder and its six files are created, every note line is quoted, and no `<script>`,
        fence, heading, link, NUL or escape reaches `report.md`). **The capture folder is
        git-ignored:** `test_the_capture_folder_inside_the_repository_is_git_ignored` captures
        into `openfactory-captures/` in the repository and finds `git status --short
        --untracked-files=all` as it was. `openfactory captures`:
        `test_openfactory_captures_lists_newest_first_and_prints_the_newest`. A replay answers
        404 (`test_a_replay_has_nothing_to_capture`).
        **The buffer's cost** (2026-11-18, i7-1260P, CPython 3.11.15): `Runtime._step` on
        `tests/scenes/load-200.yaml` with every DO forced on (as `tools/bench.py` sets it up),
        warmed 3000 ticks (190 parts, 200 at the end), alternating blocks of 50 ticks with the
        buffer off (`capture_buffer` None, the path before this task) and on, in one process,
        no viewer reading (so with it on every 5th tick builds a snapshot). Run 1, 40 blocks
        each: off 2.700 ms, on 2.849 ms, **+0.148 ms a tick**; run 2, 80 blocks each: off 3.866
        ms, on 4.095 ms, **+0.229 ms a tick** (the runs differ from each other by more than the
        change: the machine's drift, 2.9). That is a snapshot (about 1 ms at 200 parts) every 5
        ticks; a viewer reading at 20 Hz builds most of those snapshots anyway, so with a viewer
        open the extra cost is less. **Memory:** a full buffer at 200 parts grew the process by
        95.6 MB (tracemalloc over 3000 ticks, 600 frames), about 160 kB a frame; at the sorter's
        2 or 3 parts it is a few MB. *Screenshot looked at:* the integration test's `view.png`,
        the sorter at 11.2 s with labels in tags: both belts, the boxes, the stacked labels with
        their values and the four status lines, as the viewer drew them.
  - [x] 5.14f Pause, seek and step in a replay; the film strip. *What:* `openfactory replay`
        gains pause, a time slider, and a step forward or back by one frame, for the person.
        `openfactory strip FILE.ofrec --at T --span 2 --frames 6 [--look-at ID] [--labels tags]`
        gives the assistant motion: one image of six stills in a row, each stamped with its
        time, and a text table of what changed between them (tags, and each part's carrier and
        distance moved).
        *How:* `ReplayRuntime.seek(tick)` and a paused mode, `POST /api/replay/seek` (replay
        only). The strip drives the viewer with Playwright as the screenshot runner does, waits
        on the viewer's own tick, and joins the stills in the page (a canvas), so no image
        library is added (ruling 5). A capture folder (5.14e) is a valid input, so "what I just
        saw" becomes a strip with one command.
        *Done when:* seeking to tick 300 of the sorter's kit recording shows the frame the
        recording holds at 300 (state compared); the strip around the first tall box's push
        shows the plate retracted in the first still and extended in a later one (pixel sample
        on the plate), and its table lists `CYL_01_EXT_DO` going true between the right two
        stills; image looked at.
        *Done (2026-11-18):* `ReplayRuntime` (`record.py`): `paused` (and `ReplayRuntime(paused=
        True)`, `replay --paused`), `seek(tick)` (the frame at or before it, clamped to the
        recording; the event log and counts rebuilt up to it, the browser's own parity reports
        kept), `pause(bool)` (play after a finished replay starts from the first frame),
        `step(n)` (pauses, moves n frames, stops at either end), `replay_status()`; playback is
        timed from the frame it (re)started at, and a seek restarts the timing; a replay that
        plays to its end without `--loop` still ends its thread and is `finished`, as before.
        `GET /api/replay`, `POST /api/replay/seek | pause | step` (`server/app.py`; replay only,
        404 on a live run; extra keys 422; guarded as `/api/fault` is, and another Origin 403 by
        the write middleware). The viewer: `web/src/replaybar.ts` (`replaybar.test.ts`, 4): the
        bar (step back, play/pause, step forward, a slider over the recording, the time),
        Space and the arrow keys; a frame that goes back, or further on than `jumpLimit` (two
        recorded frames, or three times the ticks a 20 Hz state frame covers at the replay's
        speed, whichever is more, so a `--speed 4` or `--speed 10` replay still eases), puts
        every part at its place at once (`isJump`, `PartMeshes.snap`), and
        `PartMeshes.settled` says when every part is drawn where its frame has it.
        `src/openfactory/strip.py` and `openfactory strip FILE.ofrec|CAPTURE [--at T] [--span
        2] [--frames 6] [--look-at ID] [--labels ids|tags|off] [--size 960x540] [--out PNG]`:
        the stills' times are spread over the span centred on T (the end without `--at`),
        moved inside the recording, each the recorded frame at or before its time; the
        recording is served paused in-process on a free loopback port, the viewer is driven in
        headless Chromium (`?quality=low&panels=off`), each still is a seek, then a wait on the
        viewer's own tick with every part settled (no sleep), then the viewer's capture picture
        (5.14e: the plant, labels and status lines); the stills are joined in the page on a
        canvas, half the viewer's size each, 4 px apart, under a 22 px band stamped "t = ... s
        tick N", so no image library is added (ruling 5). The table (`change_table`, written
        beside the image as `.md` and printed): each tag that changed between two stills with
        its value at both and the count when more than one, and each part's carrier at both
        and the distance moved, or that it appeared or was removed. A capture folder is read
        through its `last-60s.ofrec`. Playwright stays a development dependency, imported only
        by `strip`, which says what to install when it is missing. Docs:
        `docs/looking-at-the-plant.md` ("Pause, seek and step in a replay", "The film strip"),
        `docs/guides/README.md`.
        **Done-when, each with its test:** seeking to tick 300 of the sorter's kit recording
        shows the frame it holds at 300: `tests/unit/test_replay_seek.py::
        test_seek_to_tick_300_shows_the_recorded_frame` (`/api/state` equals the recorded frame,
        whole) and, in the viewer, `tests/integration/test_strip.py::test_seek_draws_the_recorded
        _frame_and_the_bar_steps` (paused replay, `POST /api/replay/seek` 300: the viewer at
        tick 300 with every part settled, each part drawn at the recording's position to 1e-6 m,
        the bar reading "6.00 s / 60.00 s"; step forward 305, two arrow-key steps back 295,
        play and Space to pause). The strip around the first tall box's push
        (`test_the_strip_of_the_first_tall_box_push`; the push is tick 430, 8.60 s; stills at
        ticks 380, 400, 420, 440, 460, 480, `--look-at CYL_01 --labels off`): the plate's stroke
        0, 0, 0, 0.33, 0.27, 0 m; the pixel at the point 0.2 m out along the stroke is the
        belt's green (34, 197, 94) in the first still, the pushed box at 440, and the plate's
        own ISA-101 stopped grey (156, 163, 175) at 460; the table puts `CYL_01_EXT_DO false →
        true` in "Still 3 to 4" only, the stills either side of tick 430
        (`test_the_table_lists_the_push_between_the_right_two_stills` too, with the pushed box
        going CONV_01 → CONV_02). Also: pause and play from a seek and after the end, the event
        log after a seek back (a recording with a fault at 3 s), a live run 404, another
        Origin 403, the stills' times kept inside the recording, a capture folder as input
        (`test_replay_seek.py`, 9); the wash plant from the command line
        (`test_a_strip_of_the_wash_plant_from_the_command_line`: a 20 s recording made in the
        test's folder, `strip --at 12 --labels tags`, exit 0, the table printed, a 2900 by 292
        image); a capture folder end to end (`test_capture_viewer.py`: `strip <capture folder>
        --frames 3` exits 0 and its last still is the capture's own tick, off the 5-tick grid).
        `python -m openfactory.server.app` now sets the recording header too, so its captures
        carry the scene's YAML.
        **Images looked at:** the sorter, `--look-at CYL_01`: the plate retracted behind the
        belt in stills 1 to 3 with the tall box arriving, the box pushed off the belt in still
        4, the plate out over the belt in still 5, back in still 6; the same moment in the
        default view with `--labels tags` (still 4 cropped at full size: `CYL_01_EXT_DO true`,
        the box at the pusher). The wash plant, default view, `--labels tags`, 11.0 to 13.0 s:
        the cartons advance along both belts from still to still and TK_201's level reads
        0.576 to 0.572; at half size the whole plant with every tag is dense, which is why the
        docs point to `--look-at`.
        **Decision beyond the text:** `--at` is the centre of the span (the end of the
        recording when left out, so a capture's strip shows its last `--span` seconds).
        *Confirmed 2026-11-18 (owner's ruling).*
  - [x] 5.14g Why it happened: `openfactory explain`. *What:* for a failed check, or for one
        part, the chain of events in order with times: where the part was spawned, each
        conveyor it rode, each sensor it tripped, each command that acted on it and where the
        part was at that moment, and where it ended. In the viewer, clicking a part draws its
        trail and lists the same events.
        *How:* `CheckResult` gains `at_tick` and `subject` (the part or component of the first
        failure). The chain is worked out from a recording's frames (`carrier`, position) and
        tag changes; which part tripped a sensor is whichever part was inside the beam at that
        tick, from positions. `openfactory explain FILE.ofrec --check NAME | --part ID`; `GET
        /api/explain` on a live run uses the 60 s buffer of 5.14e.
        *Done when:* on a sorter run whose program fires the pusher 0.5 s late, `explain` for the
        failed `parts_reached` check names the part, the tick of the late command, and how far
        past the pusher the part was (within 2 cm of the recording); the trail is drawn and
        looked at.
        *Done (2026-11-19):* `src/openfactory/explain.py`: one path for a file, a capture's
        piece and a live run (`Runtime.piece()`, the capture's own piece of the last 60 s, read
        by `record.recording_from_lines`); a replay explains the recording up to the frame shown
        (`explain.up_to`). A part's story: made (`spawn_tick`, `spawn_position`, the emitter
        within 0.5 m), its carrier in the first frame and each change of carrier ("between ticks
        A and B"), each photo eye whose detect tag went true while the part's box touched the
        beam at that tick (and its going false), each change of a tag of the carrier or of a
        device within 1 m with where the part was (the carrier, the distance from its infeed
        end, how far past or before the device along the flow), a push across the flow that
        left it on the belt (2 cm or more), faults and presses near it, and how it left: the
        remover whose `by_name` count rose between two frames, gone with none (lost), or still
        in the scene. Positions between frames are interpolated (after the last frame, carried
        on with the velocity). Every line names its tick and what it rests on; it says that
        commands are listed by nearness, not intent. Checks: a recording holds no grades, so
        `parts_reached`, `none_lost` and `max_cycle_s` are worked out again from the frames; a
        process check is named with a note that `explain` cannot tell its cause. `CheckResult`
        gains `at_tick` and `subject` (the first part another remover took, the first part lost,
        the first part over `max_cycle_s` and when; a process check's component), and
        `GET /api/checks` carries them on a failed row; `format_results` does not print them, so
        `openfactory test` output and the run references are unchanged. `World.losses` logs
        lost parts. `openfactory explain [FILE.ofrec|CAPTURE] [--check NAME | --part ID]
        [--json]`; without a file, the running server. `POST /api/explain` (`{part}`,
        `{check}` or `{}`): **read-only, guarded as `/api/look` and `/api/iolog` are** (no
        token; the loopback Host check, 421), and, being a POST, the write middleware's Origin
        (403) and JSON (415) checks; 404 for a part or check not there, 422 for both or another
        key; in exam mode fault, clear and comms events are left out. The viewer
        (`web/src/explainpanel.ts`, `explainpanel.test.ts`, 2): a click on a part asks for its
        story, draws its trail in the highlight blue with a marker at each step, and lists the
        steps (tooltip: what each rests on); Esc or × clears. Docs:
        `docs/looking-at-the-plant.md` ("Why it happened"), `docs/guides/README.md`.
        **Measured, and a departure from the text:** at 0.5 s late the plate still catches every
        tall box and all four checks pass (`test_half_a_second_late_still_passes`); 0.6 s late is
        the first tenth that fails `parts_reached REM_02: tall`, so the Done-when runs at 0.6 s.
        **Done-when, each with its test** (`tests/unit/test_explain.py`, 7): the failed check
        names the part (P000002, the same part `CheckResult.subject` gives from the run's own
        removal log), the tick of the late command (460, 9.20 s, the run's first
        `CYL_01_EXT_DO` rise after the part tripped PE_01, 30 ticks after the kit's on-time
        command at 430) and how far past the pusher it was (0.21 m; the run itself had 0.2100 m)
        (`test_the_failed_check_names_the_part_the_late_command_and_how_far_past_it_was`, which
        also finds a tick and a basis on every line); every tick of the part's life is placed
        within 2 cm of the run (worst 1.5 cm, as it drops onto the belt; exact on it:
        `test_positions_between_frames_are_within_2_cm_of_the_run`); live and replay through the
        same path, the endpoint's guards and exam mode (`test_live_and_replay_explain_through
        _the_same_recording_path`, `test_post_api_explain_is_read_only_and_guarded_as_a_read_is`,
        `test_exam_mode_leaves_fault_events_out`). The trail drawn
        (`tests/integration/test_explain_viewer.py`, 2): a paused replay of the late run at tick
        560, a click on P000002: the panel's 13 rows are the endpoint's steps, "9.20 s:
        CYL_01_EXT_DO went true ... its centre 0.21 m past CYL_01's centre", the trail's pixel
        where the part was at tick 400 is (99, 161, 237), the highlight blue; a live run answers
        a click from its last 60 s. **Picture looked at:** the sorter at 11.2 s, the panel at
        the left listing the steps, the blue trail from the emitter along CONV_01 with markers at
        PE_01 and at the pusher's events, a jog where the late plate pushed the box 0.15 m across
        the belt without taking it off, and the box near REM_01.
  - [x] 5.14h Pause and single-step a live run. *What:* Space pauses the simulation; a Step
        button advances 0.1 s. For the person, at the moment something looks wrong.
        *How:* the scheduler stops calling `step`; a stand-in script pauses with it. With a real
        PLC the PLC keeps running, so its timers run on: the viewer shows "simulation paused,
        PLC still running" (ruling 4: allowed, with the banner), and the heartbeat tag stops as
        it already does when the simulation stops.
        *Done when:* a test pauses a scripted run, sees the tick hold for two wall seconds, steps
        five times and sees it advance five frames' worth; parity traces and recordings of an
        unpaused run do not move.
        *Done (2026-11-19):* `Scheduler.pause` / `step_ticks` (`core/scheduler.py`): a pause
        takes effect after the tick in progress and answers once the run's thread holds; a step
        runs its ticks on the run's own thread (so `stats.ticks` and a `duration_s` limit count
        them) and answers once they have run; resuming restarts the real-time schedule from that
        moment (no catch-up burst, no dropped ticks). `Runtime.pause`, `Runtime.step` (0.1 s a
        step, `pause` and `step` events in the event log), `run_status`; `GET /api/pause`, `POST
        /api/pause` `{paused}`, `POST /api/step` `{steps}` (1 to 100): **write endpoints guarded
        as `/api/fault` is** (open on loopback, the fault token beyond it; the write middleware's
        Origin 403 and JSON 415) **and off for a trainee in exam mode** (403 without the
        instructor's token, the check factored out of capture's); 404 in a replay (its own bar).
        While paused `/ws/state` frames carry `sim_paused: true`, outside the snapshot, so no
        recording holds it. The viewer (`web/src/pausebar.ts`, `pausebar.test.ts`, 2): Space
        (not typed in a text field, not on auto-repeat, not in a replay, the editor, or for a
        trainee), Pause/Resume and Step 0.1 s beside Capture, and the banner at the bottom
        centre on every viewer: "Simulation paused, PLC still running (Modbus TCP): its timers
        run on, and the heartbeat has stopped" with a protocol server (ruling 4), else
        "Simulation paused (the stand-in script, if any, pauses with it)".
        **What a protocol client sees while paused** (`tests/integration/test_pause_modbus.py`):
        the server keeps answering from the I/O table, which no tick changes: the heartbeat read
        over Modbus changed 3 times in 1.6 s running, 0 times in 2.0 s paused, 3 times in 1.6 s
        resumed; a coil the client wrote while paused (`CONV_01_RUN_DO`) was in the table at
        once, the tick did not move, the belt did not travel and its feedback stayed off until a
        step, after which `CONV_01_RUN_DI` was on and the belt had moved.
        **Done-when, each with its test** (`tests/unit/test_pause.py`, 6): paused, the tick and
        the script's scan count hold for two wall seconds; five steps answer ticks +5, +10, +15,
        +20, +25 (five frames' worth) and the script scanned 25 more times
        (`test_a_paused_run_holds_for_two_seconds_and_five_steps_advance_five_frames`); a paused
        run with `duration_s` 1 s, held 2 s of wall time, grades nothing and every check stays
        pending, then grades when simulated time reaches 1 s
        (`test_a_paused_run_never_fails_a_checks_deadline_by_waiting`); resuming runs at real
        time from then (25 ticks in 26 ticks' worth of wall time, none dropped:
        `test_resuming_starts_from_now_without_catching_up`); the guards
        (`test_pause_and_step_are_write_endpoints_guarded_as_api_fault`,
        `test_off_for_a_trainee_in_exam_mode`); frames and snapshot
        (`test_frames_carry_sim_paused_and_the_snapshot_never_does`). Parity traces and
        recordings of an unpaused run do not move: `tools/build_kit.py --check` (12 kits),
        `tools/parity_trace.py --check` and `tests/integration/test_runs_unchanged.py` pass
        unchanged. In the browser (`tests/integration/test_pause_viewer.py`): Space pauses, a
        second viewer shows the banner, the tick holds a second, Step runs 5 ticks, Space in a
        text field does nothing, Space resumes. **Picture looked at:** the sorter paused at
        5.2 s, the yellow-bordered banner at the bottom centre, Resume and Step 0.1 s beside
        Capture, the checks panel at "t = 5 s". The first picture showed the pause glyph as a
        missing-font box, Resume covering Step, and the banner at the top touching the checks
        panel; all three fixed before this one.
  - [x] 5.14i The program's own state. *What:* a stand-in script can publish watch values, for
        example `io.watch("step", 20)`. They show in `look`, in tag labels, in a recording and in
        `explain`. They are not tags: no address, no export, and the lockfiles do not move.
        *Later, with 2.2:* extra controller tags to watch, named in `openfactory.toml`, read
        with the Logix path.
        *Done when:* the sorter's stand-in script publishes its step, `look` prints it, and both
        lockfiles and the I/O CSV are byte for byte unchanged.
        *Done (2026-11-19):* `PlcIO.watch(name, value)` (`pyplc.py`): a name matches
        `[A-Za-z_][A-Za-z0-9_.]{0,39}` (else ValueError: never a path, a file name or a tag),
        at most 32 names; a value is kept as a bool, a whole number, a finite float or `None`,
        else as text (a non-finite float as `nan`/`inf`, since a recording's JSON allows no
        NaN), control characters dropped (newline and tab as a space), cut to 80 characters.
        The runtime's snapshot gains `watch` only when the script published any, so a run
        without them, and its recording, is byte for byte what it was. `look` lists them in
        "Program (watch values)", a string quoted and escaped; a recording's frames hold them;
        `explain` says when each changed while the part was in the scene (between two frames,
        at most 40); the viewer shows them as one more status line with labels: tags
        ("program: step 20 · boxes_in_transit 0", `labels.ts` `watchLine`, text only, cut to
        160 characters), which the capture picture and the strip's stills draw too. The
        sorter's script publishes `step` (10 belts starting, 20 feeding, 30 a tall box on its
        way, 40 pushing) and `boxes_in_transit`. Docs: `docs/looking-at-the-plant.md` ("The
        program's own state").
        **Decision beyond the text:** "in tag labels" is read as the status lines under labels:
        tags: a watch value belongs to no component, so it has no component label to go on.
        **Done-when, each with its test** (`tests/unit/test_watch.py`, 7): the sorter's script
        publishes its step and `look` prints it (`test_the_sorters_script_publishes_its_step_and
        _look_prints_it`, which also sees steps 20, 30 and 40 go by); both lockfiles and both
        I/O CSVs, exported from a copy of the scene in the test's folder as the kit builder
        does, equal the committed ones byte for byte, and no watch name is a tag
        (`test_they_are_not_tags_the_lockfiles_and_the_io_csv_do_not_move`); in a recording and
        in `explain` ("the program's step went 20 to 30" as the tall box reaches the eye:
        `test_watch_values_are_in_the_recording_and_in_explain`); the data rules
        (`test_watch_values_are_data`, `test_a_watch_value_is_shown_as_text_in_look`); a script
        that publishes none has no `watch` (`test_a_script_that_publishes_none_has_no_watch_in
        _its_snapshot`); in the browser, `tests/integration/test_labels.py::test_the_programs
        _watch_values_show_with_the_tags_values` and `labels.test.ts`.
        **What moved, and why** (PLAN §9 rule 13; the owner's instruction of 2026-11-18 for
        this part: "kits and run references do not move, or the report says which moved and
        why"): **the sorting-by-height kit**, renewed in this commit: its script copy (the
        script now publishes its step) and its recording, which `build_kit.py --check` reported
        first differing at tick 5 (the first frame after the script's first scan). The run did
        not move: with the frames' `watch` key removed, all 633 lines of the new recording equal
        the committed one's (every tag change, every frame's state, the end record); 600 frames
        gained `watch`. **The `look` golden file** (`tests/golden/look-sorting-by-height-6.0s.md`,
        read from that recording) gains the "Program (watch values)" section, `step = 20`. No
        other kit, no run reference (`accumulation-demo`, `variable-item-demo`: their scripts
        publish none) and no parity trace moved.
        **Picture looked at:** the sorter at 5.98 s with labels: tags: the fifth status line
        "program: step 20 · boxes_in_transit 0" under the frame line, every component's tag
        values as before.
  - [ ] 5.14j Tell the assistant how to look. Each scene's generated `CONTEXT.md` gains a
        "Looking at the plant" section with the `look`, `strip`, `explain`, `captures` and
        screenshot commands for that scene.
        *Done when:* the 2.19 golden files are renewed in the same commit and a test finds each
        command in every shipped scene's context file.

#### For the owner to rule
1. Which drawing set is the pilot (5.0), and which private folder holds the scenes made from
   customer drawings?
2. Which family first in Phase C: bulk solids, air and heat, or discrete? Recommended: whatever the
   pilot's drawing needs, and bulk solids if it is a tie.
3. Is the bench PLC free for 2.2? A scene built from a customer's drawing is only a demo for them
   once it runs against their controller family.
4. Does Phase A wait for the open Milestone 4 items (4.1g, 4.4, 4.5), or run beside them?
   Recommended: beside them; the pilot needs no code in the repository.
---

## 8. Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Logix Emulate not reachable from Python | Rockwell demo path weaker | §2.4 spike early in M2; physical CompactLogix as reference; OPC UA via Linx Gateway as fallback |
| Browser physics and headless kinematics diverge | Flaky tests, PLC timing differs by mode | Parity test from day one; keep kinematics authoritative for sensors; tolerance documented |
| Scope creep in component library | Milestones slip | Table in §5 is the list. New types need an issue and a PLAN edit first |
| LGPL in asyncua | License questions from adopters | `THIRD_PARTY.md` explains dynamic import; offer `open62541` binding as alternative later |
| Performance with many parts | Demo looks bad | Cap parts at 200; instanced meshes; profile in 2.9 |
| Doubt that OpenFactory is an original work | Legal exposure | The original-work statement (README, CONTRIBUTING.md "Original Work Policy", docs site) and its contributor terms, DCO sign-off; nothing copied from a commercial product, nothing derived from a commercial 3D simulator's internals, and no vendor SDK, binary or vendor-specific code in this repository (CLAUDE.md, ruled 2026-11-11; wording 2026-11-12, not yet legally reviewed) |
| Scene format freezes at public release | Breaking changes after 4.5 strand users' scenes | Schema versioning and `migrate` (4.7) before release |
| Physics or protocol library API churn | Parity or Modbus binding silently breaks on upgrade | Pinned minors; quarterly upgrade task (4.9) reruns parity and round-trip tests |
| Users mistake it for virtual commissioning or safety validation | Reputational and liability exposure | "What OpenFactory is not" statement (4.8) in README, docs site, and every scene header |
| Analog range is per port type, not per tag | Milestone 3 transmitters and tanks need their own engineering ranges (for example 0–2 m level, 0–150 °C), which `int16_scaled` scaling (ADR-0003) depends on | Closed by 3.7 (2026-11-07): `range` and `units` on an analog binding |

---

## 9. Working Agreement for the Implementing Session

1. **Start each session** by reading `CLAUDE.md`, then the current milestone here, then `git log -5`.
2. **One task per commit** where practical. Tick the box in the same commit.
3. **Test before claim.** "Done when" is the acceptance test. Run it. Paste the result in the commit
   body or PR description.
4. **Visual work is verified visually.** Use `tools/screenshot.py` and look at the PNG.
5. **Protocol facts come from public documents** (specifications, vendor manuals by publication
   number, library documentation and source), not from memory. Cite the source, a publication
   number or URL, in a code comment.
6. **When stuck for more than two attempts**, write the blocker under the task and move on. Do not loop.
7. **New decisions get an ADR.** Short is fine: context, decision, consequences.
8. **Never touch** `E:\Hyper-V VMs` or `E:\VirtualBox-VMs`, and open `E:\04_Software` only at a
   path the owner names (the earlier rule that kept sessions away from vendor material was
   replaced on 2026-11-11; what holds now is in CLAUDE.md, "Original Work and Vendor
   Material"). **Never run
   `find`, `ls -R`, `grep -r` or any other walk above the repository root or the session's scratch
   folder** (ruled 2026-11-10): a walk from `/` or `/mnt/e` crosses all three. A file needed from
   outside is asked of the owner.
   *Incident, 2026-11-10 (ruled: no exposure):* looking for an earlier copy of RM013, a session
   ran `find / -iname "*rm013*"`, which walked `/mnt/e` including `04_Software`, `Hyper-V VMs` and
   `VirtualBox-VMs`; it was stopped within two minutes, printed no path and opened no file. The
   manual was fetched again from the public mirror instead, and the walk rule above was added.
   *Replaced 2026-11-11:* the vendor-material rule this note was written under was replaced by
   CLAUDE.md, "Original Work and Vendor Material" (the walk rule stands).
9. **A parity trace that does not gate carries a PLAN note** (added 2026-10-27). A trace with
   `gate=False` in `tools/parity_trace.py` is reported but not held to its tolerance; it needs a
   note under the task that added it giving the measured worst delta, the model difference that
   causes it, and the ruling that accepted it. Never widen a tolerance to make a trace pass;
   report the numbers instead. As of 2026-10-30 every committed trace gates except
   `parity-turntable` (note under 2.5, ruled 2026-10-30).
10. **A scene's Done-when includes a 300 s `openfactory test` run** (ruled 2026-10-28), as well as
   the 60 s run CI does, so a scene over capacity cannot pass by running short.
11. **Outside apps are reviewed for ideas only** (ruled 2026-11-11): a review of a public browser
   hardware-learning app (`docs/research/viewer-rendering-notes.md`) left task 4.14 and two parked
   ideas, its lighting spike was reverted uncommitted, and nothing of that app's code, text, names
   or assets is used.
12. **One public statement of original work** (owner's draft, 2026-11-12). README,
   CONTRIBUTING.md ("Original Work Policy", with the contributor terms: the work is the
   contributor's own, copies nothing from a commercial product, and includes nothing derived
   from the internals of a commercial 3D simulator; the DCO stays), the docs site and the issue
   and PR templates use it, and it replaces the earlier description of OpenFactory everywhere
   the public reads. Dated entries above keep their wording as history.
   `tests/unit/test_docs_site.py` fails if the old term appears outside PLAN's dated history
   entries and `docs/research/`. **The wording is the owner's draft and has not had legal
   review** (owner's open items).
   *Corrected 2026-11-12:* the sentence about what had not been examined over-claimed; it is
   replaced by a policy that names no product ("Nothing derived from the internals of any
   commercial 3D simulator is accepted into this project.").
   *Ruled 2026-11-12:* CHANGELOG.md is exempt from the check: it is generated from commit
   subjects (`tools/changelog.py`), and commit subjects are not rewritten.
13. **Every scene under `scenes/` runs as it did** (added 2026-11-15). Each one's 60 s run under
   its stand-in script is held by a committed reference, and a change to that run fails CI with
   the scene's name and the tick where it first differs. The twelve scenes with a demo kit are
   held by the kit's recording (`tools/build_kit.py --check`); a scene with no kit by
   `tests/references/<scene>.run`, its check results and one digest per recorded line
   (`tools/run_reference.py`, checked by `tests/integration/test_runs_unchanged.py`, which also
   fails if a scene has neither). Today that is `accumulation-demo` and, since 2026-11-16,
   `variable-item-demo` (PLAN 5.2c, a new scene: its reference is new, not a renewal). Since 2026-11-16 CI's
   `openfactory test` loop also runs each scene in `scenes/examples/` under the script beside
   it at 60 s (about 10 s for the two). **A reference is
   renewed only when the change to the run is intended, and the owner rules that it is.** The
   renewal (`build_kit.py` for a kit, `run_reference.py --update <scene>` for a reference) goes
   in the commit that changes the run, never in a later "refresh" commit, and that commit's body
   names every scene renewed, the tick the check reported, why the run moved, and the ruling.
   A change that should not move a run (a refactor, a new optional key, an editor feature) never
   renews one: if the check fails, the change is wrong. A rebuilt kit whose recording is the same
   run (the check says "the header differs, the run is the same", as after a version bump) is
   not a renewal under this rule.

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
