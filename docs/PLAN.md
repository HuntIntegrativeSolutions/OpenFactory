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
  may bind a `_DI` tag with `window: [lo, hi]` (PLAN 2.17). `address` pins the tag's Modbus address (§6.1, ADR-0004). `initial`, on DO and AO tags
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
  never infers the connection) when one conveyor's discharge end is within 5 mm of another's infeed
  and no connection joins them.
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
| `belt_conveyor` | run, speed (opt) | running | Moves parts on its top surface at `speed_mps × speed%` when run. Ramp 0.3 s. | 1 |
| `photo_eye` | — | detect | True while any part intersects the beam segment. 20 ms debounce. | 1 |
| `pusher` | extend | extended, retracted | Linear actuator; moves parts it contacts. Both limits false mid-stroke. | 1 |
| `box_emitter` | enable | — | Spawns a part every `interval_s ± jitter` while enabled, once its spawn volume is clear. Max parts in scene = 200. | 1 |
| `part_remover` | — | — | Deletes parts entering its volume. Counts by part name for scene stats. | 1 |
| `pushbutton` | — | pressed | Viewer click sets true for 300 ms (momentary) or toggles (maintained). | 1 |
| `pilot_light` | on | — | Viewer visual only. | 1 |
| `selector_switch` | — | pos_a, pos_b | Two or three position. | 1 |
| `roller_conveyor` | run, speed (opt) | running | Same as belt, drawn as rollers; its own `friction` (default the scene's). | 2 |
| `stopper` | raise | raised, lowered | Blade that rises out of the belt and holds the queue; a part already over it passes. | 2 |
| `turntable` | run, turn | running, at_0, at_90 | Turns its deck conveyor ±90°; `turn` held drives to `turn_deg`, released returns to 0. | 2 |
| `diverter_arm` | extend | extended, retracted | Paddle swinging across a belt; parts slide along it onto a side conveyor. | 2 |
| `pick_and_place` | traverse, lower, grip | at_pick, at_place, lowered, holding | Two-axis gantry with a gripper pad. | 2 |
| `distance_sensor` | — | distance (AI), switching (AI) | Range to the nearest part or belt along its beam, metres 0 to `beam_length`. | 2 |
| `color_sensor` | — | match, detect | Diffuse sensor taught one part `color`; `match` while the nearest part in range has it. | 2 (2.7) |
| `counter_display` | value (AO) | — | 4-digit readout on a panel. | 2 (2.5b) |
| `tank` | — | level (AI), hi_sw, lo_sw | Mass balance: Σ inflows − Σ outflows. Height from volume and footprint. | 3 |
| `valve_onoff` | open | opened, closed | Travel time; flow coefficient Cv passes to connected pipe. | 3 |
| `valve_control` | position (AO) | position_fb (AI) | First-order lag, stroke time. | 3 |
| `pump` | run, speed (opt) | running, flow (AI) | Flow = f(speed, head) with a simple curve. | 3 |
| `pipe` | — | — | Topology only; connects sources and sinks for mass balance. | 3 |
| `heater` | on, power (AO) | temp (AI) | First-order thermal model on connected tank. | 3 |
| `transmitter` | — | pv (AI) | Generic 4–20 mA style scaling of any process variable with noise and fault injection. | 3 |
| `vfd_motor` | run, speed (AO) | running, speed_fb (AI), fault | Drives a conveyor or pump; trip on overload. | 3 |

#### Milestone 1 behavior as built

The full behavior spec for each type is in its module docstring (`src/openfactory/components/`), and
`docs/components.md` is the component reference (parameters, ports, behavior) for every type,
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
- [ ] 2.1 Research spike: Logix Emulate connectivity, options (a)–(d) in §2.4. Write
      `docs/research/logix-emulate.md` and ADR-0002.
- [ ] 2.2 `protocols/logix.py` with pycomm3, batched read/write, heartbeat, reconnect with backoff
      *Done when:* bench CompactLogix runs the sorter with tags generated by `/io-to-l5x`, and pulling
      the Ethernet cable for 10 s then reconnecting resumes control with no restart of either side.
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
      prefix. The L5X format reference is Rockwell publication 1756-RM084 (Logix 5000
      Controllers Import/Export Reference Manual). No `ExportDate` and no `<Data>`, so the file
      is byte-identical per scene for 2.14 and the tags take Studio 5000's defaults; whether an
      import accepts both omissions is part of the Studio 5000 import test below. The text is parsed back before it
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
      is a 2.9 item ("Seam crossing").
- [ ] 2.5b Components split from 2.5: `counter_display` (4-digit readout of an AO on a panel),
      `lift` (moves a short conveyor between two heights; at_top/at_bottom DIs), reversible belts
      (belt and roller conveyors gain `bidirectional: true` with a `reverse` DO and a direction
      arrow in the viewer), and the accumulation (zero-pressure) conveyor, the first
      `scenes/_library/` composite built on 2.16 and 2.0, not a primitive. The W207 gap case (below,
      under 2.20) is settled here with the accumulation work.
      *Added 2026-10-27 (2.6 rulings):*
      - Part rotation on turntables: parts on a turning deck turn with it in orientation, not
        only position (the core keeps every part square to the world axes today, which is right
        for square boxes and wrong for long ones). Needed before long parts or custom meshes
        (2.15). Parts gain a yaw in the core, the state frame, and the parity traces.
      - Pick-and-place pad collision: the lowered pad stops on a part's top (and `lowered`
        reports the true pad position) instead of passing into it.
      - `color_sensor.debounce_ms` (ruled 2026-10-28): an on/off debounce on `match` and
        `detect`, like the photo eye's 20 ms, default 0 so existing scenes keep their timing.
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
      `buffer-and-release` is still to be rebuilt on a composite with 2.16.
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
      Add `tools/bench.py` running a fixed 500-part benchmark scene headless for 60 simulated seconds
      and reporting mean and p99 tick time; CI fails if p99 exceeds 15 ms. The scheduler overrun
      counter from Milestone 1 is the production-side signal for the same limit.
      **Physics engine check:** Jolt (MIT) compiled to multi-threaded WASM supports true conveyor
      surface velocity through its contact listener, which Rapier JS lacks (`libraries.md`). Build a
      spike branch swapping Rapier for `jolt-physics` on the sorter, compare parity trace, frame time at
      200 parts, bundle size, and API ergonomics. Write ADR-0005 either way. Do not switch on a hunch;
      switch if parity tightens or frame time halves.
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
      **Measure against a same-session baseline (added 2026-10-27):** absolute tick times on the
      development machine vary about 30 % from day to day (11.65 ms on 2026-10-26 for the same
      code that measured 15.4 ms and 15.8 ms on a slower day). Every before/after claim runs the
      baseline commit and the candidate in the same session on the same machine and reports both;
      a single absolute number is not evidence of a regression or an improvement.
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
- [ ] 2.13b Comms-loss injection on the OPC UA server (added 2026-10-30, from 2.13 and 2.4):
      `comms: {drop: true}` and `comms: {latency_ms: N}` act on `protocols/opcua.py` as they do on
      the Modbus server, so an OPC UA client (Ignition) sees Bad quality or late values while the
      simulation keeps running.
      *Done when:* an asyncua client sees reads fail under `drop` and recover when it is cleared,
      and reads and writes delayed by `latency_ms`, while the simulation ticks; the faults reach
      `/api/fault`, `--fault comms=...` and recordings as for Modbus.
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
- [ ] 2.15 Custom mesh per component: an optional `mesh: {file: models/press.glb, scale: 1.0,
      offset: [0,0,0]}` on any component replaces the viewer's primitive with a user-supplied GLTF
      (`.glb`/`.gltf`; OBJ via a one-off `tools/convert_mesh.py`). The headless model and physics
      collider keep using the component's declared dimensions; the mesh is visual only. Files resolve
      relative to the scene file; the server serves them under `/models/`.
      *Why:* users want their own equipment on screen immediately, and competitors in this class
      already offer model import. A curated asset library (4.2) is polish on top of this, not a
      prerequisite for it.
      *Done when:* the sorter scene renders a user-supplied `.glb` for CYL_01 in the screenshot
      runner, the parity trace is unchanged, and a missing file falls back to the primitive with a
      loader warning rather than a blank viewer.
- [ ] 2.16 Composite components, declared in scenes without Python: a `composites:` block defines a
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
- [ ] 2.18 Self-contained scene bundle: `openfactory bundle scene.yaml` writes `scene.ofz` (zip)
      holding the scene, its composites, referenced meshes, lockfiles, PLC script, and a manifest with
      the schema version; `openfactory run scene.ofz` works directly.
      *Why:* a scene is a demo only if it survives email. Without this, meshes and lockfiles get lost.
- [ ] 2.19 Generated per-scene assistant context: `openfactory run` and `export` write
      `<scene>.CONTEXT.md` next to the scene listing every tag with kind, address, description and
      transform, every component with its type and parameters, the exercise checks, and the exact
      commands to run, test, and export it. Deterministic, regenerated each time, committed with the
      scene.
      *Why:* OpenFactory's claim is that an AI assistant can author and operate scenes. Give it the
      ground truth in one file instead of making it reverse-engineer the YAML and the lockfile.
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
      pairs exempt). Scripts are scanned as text, never run. The composites check waits for
      2.16. `tools/ci_local.sh` step 3 runs `openfactory lint scenes`; the shipped sorter has
      0 errors and 0 warnings.
      *Pusher checks (2026-10-26):* W206 a plate top below the centre of mass of any emitted part
      on the belt the plate sweeps (it tips the part), W207 a stroke shorter than the retracted
      face's distance to that belt's far edge (the part is left straddling the seam). Both came
      from a phone demo where the sorter's 0.15 m plate at y 0.1 tipped 0.4 m boxes and its 0.5 m
      stroke left them across the CONV_01/CONV_02 seam. An unset `plate_height` is now sized from
      the emitted parts. The sorter's pusher moved to y 0.175 with stroke 0.6 (plate 0.25 m,
      y 0.05 to 0.30); pushed boxes now end centred at z 0.45 on CONV_02 instead of 0.35.
      **Known gap (W207):** the check measures to the far edge of the belt the plate sweeps across
      (ruled correct). When a gap separates that belt from the belt beyond, clearing the swept belt
      does not land the part on the next one; that case waits for 2.5b's accumulation work.

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
- [ ] 3.7 Per-tag `range` and `units` override in the binding form (closes the §8 analog-range risk)
      before any 3.2 component lands.
- [ ] 3.8 Process demo kit (`kits/tank-level-pid/`) built with 2.14, including PlantPAx L5X and
      Perspective faceplates from 3.4.

### Milestone 4 — Editor, assets, community (ongoing)
- [ ] 4.1 In-browser scene editor: drag components, edit properties with schema-driven forms, export YAML.
      Must support placing and editing composite types (2.16) and assigning meshes (2.15) from day one.
      Components declare typed snap ports (`load` for conveyor ends, `pipe`, `mount`) so the editor
      can snap end to end and the lint (2.20) can check them.
- [ ] 4.2 CC-BY GLTF models for the top ten components; `ATTRIBUTION.md`
- [ ] 4.3 Multi-viewer: one core, many browsers (classroom)
- [ ] 4.4 Docs site (MkDocs Material), component reference generated from docstrings
- [ ] 4.5 Public GitHub release, issue templates, Discussions, first good-first-issues
- [ ] 4.6 Desktop packaging (PyInstaller + bundled viewer) for Windows users without Python
- [ ] 4.7 Scene schema versioning: `openfactory: "0.2"` bumps require `openfactory migrate` to rewrite
      older scenes, with a test corpus of every shipped scene at every past version. Must exist before
      4.5, because a public release freezes the format people depend on.
- [ ] 4.8 Release hygiene before 4.5: SECURITY.md with a disclosure contact, CODE_OF_CONDUCT.md,
      semantic versioning policy, CHANGELOG.md, SBOM generated from `THIRD_PARTY.md`, and a one-page
      "what OpenFactory is not" statement (not safety-rated, not virtual commissioning, not a PLC).
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
- [ ] 4.12 Instructor view (deferred from 2.11, ruled 2026-10-30): a separate URL that takes the
      fault token, shows fault markers even in exam mode, and shows the exam switches read-only,
      so an instructor watches what the trainees cannot see without touching their inputs.

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
| Scene format freezes at public release | Breaking changes after 4.5 strand users' scenes | Schema versioning and `migrate` (4.7) before release |
| Physics or protocol library API churn | Parity or Modbus binding silently breaks on upgrade | Pinned minors; quarterly upgrade task (4.9) reruns parity and round-trip tests |
| Users mistake it for virtual commissioning or safety validation | Reputational and liability exposure | "What OpenFactory is not" statement (4.8) in README, docs site, and every scene header |
| Analog range is per port type, not per tag | Milestone 3 transmitters and tanks need their own engineering ranges (for example 0–2 m level, 0–150 °C), which `int16_scaled` scaling (ADR-0003) depends on | Add a per-tag range override in the scene before Milestone 3 component work starts |

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
8. **Never touch** `E:\04_Software`, `E:\Hyper-V VMs`, or `E:\VirtualBox-VMs`.
9. **A parity trace that does not gate carries a PLAN note** (added 2026-10-27). A trace with
   `gate=False` in `tools/parity_trace.py` is reported but not held to its tolerance; it needs a
   note under the task that added it giving the measured worst delta, the model difference that
   causes it, and the ruling that accepted it. Never widen a tolerance to make a trace pass;
   report the numbers instead. As of 2026-10-30 every committed trace gates except
   `parity-turntable` (note under 2.5, ruled 2026-10-30).
10. **A scene's Done-when includes a 300 s `openfactory test` run** (ruled 2026-10-28), as well as
   the 60 s run CI does, so a scene over capacity cannot pass by running short.

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
