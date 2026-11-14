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
        sensor, a pusher and a bin, with a stand-in script and checks that pass; write down what
        was awkward, fix the worst three, ship the scene and add it to the docs gallery with a
        screenshot. *Done when:* the scene is shipped like any other (lockfiles, OpenPLC
        program, stand-in script, guide, checks passing at 60 s and 300 s) and a test rebuilds
        it in the browser from `openfactory new` and saves the same values.
        *Done (2026-11-13):* `scenes/inline-reject.yaml`: two 3 m belts (CONV_02 snapped onto
        CONV_01's discharge on Add, connection added), a box source (good 0.2 m, oversize
        0.4 m), a photo eye above the good boxes, a pusher that pushes oversize boxes off the
        side into a reject bin, and a bin at the end. Its checks pass at 60 s and 300 s under
        `examples/pyplc/inline-reject.py` (longest 11.8 s, limit 15 s); `examples/openplc/
        inline-reject.st` is its OpenPLC port; `docs/guides/inline-reject.md` with
        `docs/media/inline-reject.png` (looked at). `tests/integration/test_editor_build.py`
        runs the whole session in Chromium (`openfactory new`, then palette, Add, form fields,
        Use buttons, scene settings, Save) and compares the saved file's values with the
        shipped file's; the shipped file is that session's output. The lockfiles and
        CONTEXT.md came from `openfactory export`. The 60 s run under OpenPLC was not recorded
        (no OpenPLC container on this machine).
        *Awkward, in the order met:* (1) there was no way to start a scene: `edit` opens only a
        scene that loads, and a scene needs a component; (2) the scene's own settings and its
        `checks:` could not be edited at all; (3) optional ports (a belt's `running`, a
        pusher's `extended`, an emitter's `enable`) had to be typed by hand, and the emitter,
        with no `io:` yet, showed "not set" and no way to bind one; (4) a sensor, pusher or bin
        lands clear of the plant (4.1e) and then needs world coordinates typed: there is no
        "on that belt" (mount ports, 4.1g); (5) the view stays framed on the starter belt as
        the plant grows, with no fit-view button; (6) list values (the emitter's `parts`) are
        one line of YAML, shown as JSON; (7) the editor writes no comments (the shipped
        scenes' "measured 11.8 s" note) and no lockfiles, so lint warns W201/W202 until
        `openfactory export` runs once per encoding; (8) typing the value a field already has
        gives no sign it was taken. *Fixed, the worst three:* (1) `openfactory new` (one belt,
        placeholders that hold Save); (2) the scene settings form (`scene:` and `checks:` from
        the schema; a first check creates the block above `components:`); (3) every port
        listed, a **Use TAG** button with the conventional tag, and a first binding creating
        `io:`. Also (6): list values are shown in YAML flow style. Tests:
        `tests/unit/test_templates.py` (+2), `test_sceneedit.py` (+2), `ops.test.ts` (+1),
        `forms.test.ts` (+1), `tests/integration/test_shipped_scenes.py` (the new scene).
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
