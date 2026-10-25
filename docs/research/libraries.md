# Research Notes — Libraries, Licenses, and Tooling

Retrieved 2026-10-24. Versions and licenses of the chosen libraries re-verified 2026-10-25 against the npm
and PyPI registries (primary source) and the upstream LICENSE files. "Latest stable" means the registry's
`latest` dist-tag (npm) or current release (PyPI) on that date. Re-check before pinning in `pyproject.toml`
and `package.json`. Rows with version "not checked" were out of scope for the 2026-10-25 pass.

## Rendering and physics

| Library | Version (released) | License | Source | Notes |
|---------|--------------------|---------|--------|-------|
| Three.js (`three`) | 0.186.1 (2026-09-24) | MIT | https://www.npmjs.com/package/three ; https://github.com/mrdoob/three.js/blob/dev/LICENSE | De facto standard WebGL library. GLTFLoader, OrbitControls, InstancedMesh all built in. Types: `@types/three` 0.186.0 (MIT, DefinitelyTyped). |
| Rapier3D JS (`@dimforge/rapier3d-compat`) | 0.21.0 (2026-09-25) | Apache-2.0 | https://www.npmjs.com/package/@dimforge/rapier3d-compat ; https://github.com/dimforge/rapier.js/blob/master/LICENSE ; https://rapier.rs/docs/ | Rust engine compiled to WASM. Use the `-compat` package to avoid bundler WASM config headaches. In 0.21.0, both `dist/rapier.mjs` and `dist/rapier.cjs` embed the WASM as a 4.1 MB base64 string that decodes to the `\0asm` header. That is about 4.3 MB of JS, so load it lazily. `@dimforge/rapier3d-deterministic-compat` is also at 0.21.0 (Apache-2.0) if cross-machine reproducibility is wanted. Conveyor approach: see "Rapier conveyor approach" below. |
| Babylon.js | not checked | Apache-2.0 | https://github.com/BabylonJS/Babylon.js | Alternative. Has Havok physics plugin (free but proprietary binary). Not chosen. |
| Godot 4 | not checked | MIT | https://godotengine.org/license/ | Strong option for a future desktop or VR build. Web export is large (tens of MB) and harder to script-test. Not chosen for viewer. |

> **Resolved 2026-10-25:** npm `latest` dist-tags read from `registry.npmjs.org`; the license field
> matches each repo's LICENSE file (three.js: MIT, "Copyright © 2010-2026 three.js authors";
> rapier.js: Apache License 2.0). The deterministic variant has moved from 0.19.x to 0.21.0.

Why physics in the browser: see PLAN §2.1. Rapier's kinematic bodies with set velocity model a conveyor
surface well; sensor colliders give photo-eye intersection events without ray casting every frame.

### Rapier conveyor approach

**Answer: use a kinematic, velocity-based belt body, teleported home before every step.** A contact-based
surface velocity is the textbook method, but it is not available in Rapier JS.

**Contact-based surface velocity is Rust-only.**
- Rust Rapier has had a `PhysicsHooks::modify_solver_contacts` hook since PR #120 (merged 2021-02-24,
  https://github.com/dimforge/rapier/pull/120). The PR names conveyor belts as a use case. The hook
  sets `solver_contact.tangent_velocity` per contact, as in
  `website/docs-examples/2d/bevy/examples/contact_modification.rs` in https://github.com/dimforge/rapier.
- The JS/TS binding does not expose it. In the published 0.21.0 package, `ActiveHooks` contains only
  `FILTER_CONTACT_PAIRS` and `FILTER_INTERSECTION_PAIRS`, and `PhysicsHooks` has only
  `filterContactPair` / `filterIntersectionPair` (`dist/pipeline/physics_hooks.d.ts`).
  `TempContactManifold.solverContactTangentVelocity(i)` is read-only.
- The TS binding now lives in the Rapier monorepo. At master `475caa3` (2026-10-10),
  `bindings/typescript/src.ts/pipeline/physics_hooks.ts` still reads
  `// MODIFY_SOLVER_CONTACTS = 0b0100, /* Not supported yet in JS. */`.

**Kinematic velocity-based body works, with a per-step reset.**
- The upstream pattern is the official TS testbed demo
  `bindings/typescript/testbed3d/src/demos/platform.ts` (dimforge/rapier master). It creates
  `RigidBodyDesc.kinematicVelocityBased()`, calls `body.setLinvel({x,y,z}, true)` in a pre-timestep
  action, and dynamic bodies resting on it are carried by friction.
- A kinematic body with a velocity actually moves, though. A conveyor has to stay put, so call
  `body.setTranslation(home, true)` before each step. Teleporting does not add velocity, so the solver
  still sees a surface moving at `v` and drags parts by friction while the belt never drifts more than
  `v·dt`.
- A throwaway probe (scratchpad, not committed) measured this on rapier3d-compat 0.21.0 under node 24
  at dt = 1/60 s. Setup: belt 4 × 0.1 × 0.5 m, part 0.3 × 0.2 × 0.3 m, friction 0.8 both,
  commanded 0.5 m/s, 4 s run.

| Approach | Steady part speed | Time to 95% | Belt drift | Verdict |
|----------|-------------------|-------------|------------|---------|
| A0 `kinematicVelocityBased` + `setLinvel`, no reset | 100% | 0.067 s | 2.0 m (belt slides away) | Unusable as-is |
| **A1 `kinematicVelocityBased` + `setTranslation(home)` + `setLinvel(v)` each step** | **100%** | **0.067 s** | **≤ v·dt (8 mm)** | **Recommended** |
| B `kinematicPositionBased`: `setTranslation(home)` then `setNextKinematicTranslation(home + v·dt)` | 100% | 0.067 s | ≤ v·dt | Equivalent to A1 |
| C `fixed` belt, overwrite part `linvel.x = v` while in contact (`world.contactPairsWith`) | 74% | never | 0 | Belt friction fights the overwrite |
| C3 as C, belt friction 0 with `CoefficientCombineRule.Min` | 100% | 0.15 s | 0 | Works, but a frictionless belt cannot hold a part when stopped, and the overwrite fights pusher contacts |

The acceleration lag in A1/B is friction-limited (`μ·g`). With μ = 0.8, the part trailed the ideal
position by 15 mm after 4 s, which is about 1.5 ticks at 50 Hz and 0.5 m/s. That is inside PLAN 1.12's
2-tick parity budget. Lower friction lengthens the lag, so `kinematics.py` either models the same
`μ·g` ramp or the parity tolerance gets tested at the scene's actual friction.

API calls for PLAN 1.12 (all present in the 0.21.0 typings):
- **Belt:** `RigidBodyDesc.kinematicVelocityBased()`, `ColliderDesc.cuboid(hx, hy, hz).setFriction(μ)`,
  and per step `body.setTranslation(home, true)` then
  `body.setLinvel(R·{x: v·speed%, y: 0, z: 0}, true)`, where `R` is the conveyor's `rotation_deg` so
  the velocity is in world frame.
- **Rendering:** draw the belt mesh at `home`, not at the body's translation.
- **Parts:** `RigidBodyDesc.dynamic()`.
- **Pusher:** `kinematicPositionBased()` with `setNextKinematicTranslation`.
- **Photo eye:** `ColliderDesc...setSensor(true).setActiveEvents(ActiveEvents.COLLISION_EVENTS)`,
  `world.step(eventQueue)`, then `eventQueue.drainCollisionEvents((h1, h2, started) => …)`.
  This was confirmed from the typings only, not exercised in the probe.

> **Resolved 2026-10-25:** the approach PLAN 1.12 already describes ("conveyor top as kinematic surface
> with velocity") is confirmed, with one addition: reset the belt body's translation every step.
> Contact-based surface velocity is not an option in JS until Rapier exposes `MODIFY_SOLVER_CONTACTS`.

## Python core and protocols

| Library | Version (released) | License | Source | Notes |
|---------|--------------------|---------|--------|-------|
| pymodbus | 3.16.1 (2026-10-11), Python ≥ 3.10 | BSD-3-Clause | https://pypi.org/project/pymodbus/ ; https://github.com/pymodbus-dev/pymodbus | Registers are backed live by the IOTable through a `SimDevice(action=...)` hook; `ModbusSlaveContext` no longer exists. Async server via `StartAsyncTcpServer`. API details: see "pymodbus 3.x datastore binding" below. |
| pycomm3 | 1.2.16 (2025-12-22), Python ≥ 3.6.1 | MIT | https://pypi.org/project/pycomm3/ ; https://github.com/ottowayi/pycomm3/blob/master/LICENSE | `LogixDriver` reads and writes tags on ControlLogix, CompactLogix, Micro800. Client-only, which is what we want. Docs disclaim production reliability; fine for a simulator. |
| libplctag + `libplctag` Python | not checked | MPL-2.0 | https://github.com/libplctag/libplctag | Fallback EtherNet/IP stack. Also handles PLC5, SLC, MicroLogix, which could enable legacy-migration demos. |
| opcua-asyncio (`asyncua`) | 2.1.0 (2026-10-06), Python ≥ 3.10 | LGPL-3.0-or-later | https://pypi.org/project/asyncua/ ; https://github.com/FreeOpcUa/opcua-asyncio | Full OPC UA server in pure Python. LGPL is compatible with Apache-2.0 for dynamic use; note it in `THIRD_PARTY.md`. |
| paho-mqtt | not checked | EPL-2.0 / EDL-1.0 | https://github.com/eclipse/paho.mqtt.python | MQTT client. Sparkplug B payloads are protobuf; use the Eclipse Tahu Python reference encoder or write our own small one. |
| Eclipse Tahu | not checked | EPL-2.0 | https://github.com/eclipse/tahu | Sparkplug B reference implementations and `.proto`. |
| FastAPI | 0.162.0 (2026-10-23), Python ≥ 3.10 | MIT | https://pypi.org/project/fastapi/ ; https://fastapi.tiangolo.com/ | HTTP + WebSocket. Serves the built viewer as static files. Pre-1.0: pin the minor version. |
| uvicorn | 0.54.0 (2026-09-25), Python ≥ 3.10 | BSD-3-Clause | https://pypi.org/project/uvicorn/ | ASGI server for FastAPI. |
| pydantic v2 | 2.14.1 (2026-10-11), Python ≥ 3.10 | MIT | https://pypi.org/project/pydantic/ ; https://docs.pydantic.dev/ | Scene schema with JSON Schema export. |
| typer | 0.27.3 (2026-10-06), Python ≥ 3.10 | MIT | https://pypi.org/project/typer/ ; https://typer.tiangolo.com/ | CLI. Pre-1.0: pin the minor version. |
| uv | not checked | MIT/Apache-2.0 | https://github.com/astral-sh/uv | Fast Python env and dependency management. |
| ruff | not checked | MIT | https://github.com/astral-sh/ruff | Lint + format. |
| Playwright (Python, `playwright`) | 1.64.1 (2026-10-22), Python ≥ 3.10 | Apache-2.0 | https://pypi.org/project/playwright/ ; https://playwright.dev/python/ | Screenshot runner and viewer integration tests. Already in use at HIS Controls for GRAFCET verification. |

> **Resolved 2026-10-25:** versions, release dates, and `requires_python` read from the PyPI JSON API
> (`https://pypi.org/pypi/<name>/json`). Licenses come from PyPI metadata: `license_expression` for
> fastapi, uvicorn, pydantic, typer, and playwright; the `license` field and trove classifier for
> pymodbus, pycomm3, and asyncua. They were cross-checked against upstream:
> - **pycomm3 is MIT** in the current `master/LICENSE` ("The MIT License (MIT) — Copyright (c) 2020
>   Ian Ottoway, Copyright (c) 2014 Agostino Ruscito"). The GitHub API reports the same (SPDX `MIT`).
>   The repo is not archived; its last push was 2025-12-22. The earlier "0.6.1" note was a stale
>   reference; the current major line is 1.x.
> - **pymodbus is BSD-3-Clause** per the `dev/LICENSE` file.
> - **asyncua is LGPL-3.0.** The license file is `COPYING`
>   (https://github.com/FreeOpcUa/opcua-asyncio/blob/master/COPYING), the GNU LGPL v3 text, and GitHub
>   reports SPDX `LGPL-3.0`. The PyPI classifier adds "or later" (LGPLv3+).
> - All the Python packages above support Python 3.11, the project baseline.

### pymodbus 3.x datastore binding

**Short answer:** in pymodbus 3.16.1, bind the IOTable through `SimDevice(action=...)`. The classic route
(subclass a datablock, wrap it in `ModbusSlaveContext`) no longer exists and no longer produces live reads.

Source: the pymodbus 3.16.1 wheel from PyPI (`pymodbus/datastore/*.py`, `pymodbus/simulator/*.py`,
`pymodbus/server/*.py`). Verified with a scratch client/server round trip; all checks passed.

**What changed from the API assumed in PLAN 1.7:**
- `ModbusSlaveContext` is gone. Its successor, `ModbusDeviceContext(di=, co=, ir=, hr=)`, is a
  deprecated shim. It deep-copies each block's `simdata` once at construction
  (`datastore/context.py`), so a custom datablock subclass's `getValues` / `setValues` is never called.
  `ModbusSequentialDataBlock` and `ModbusSparseDataBlock` are now just `SimData` containers with no
  get/set methods.
- `ModbusDeviceContext`, `ModbusServerContext`, and the old datablocks all log "deprecated and will be
  removed in v4. Please convert to SimData/SimDevice"
  (https://pymodbus.readthedocs.io/en/dev/source/upgrade_40.html#convert-to-simdata-simdevice).
- Whatever context is passed in, the server wraps it in `SimCore` (`server/base.py`), which runs
  `SimDevice` / `SimRuntime`. The `pymodbus.simulator` package docstring still says "experimental and
  not integrated into production", but in 3.16.1 it is the code path every server request goes
  through.

**How to bind the IOTable live in 3.16.1:**
1. Build `SimDevice(id=0, simdata=(coils, discrete_inputs, holding, input), action=hook)`.
   - Each element is a `list[SimData]`, for example
     `SimData(0, count=n, values=False, datatype=DataType.BITS)` for bits or
     `SimData(0, count=n, values=0, datatype=DataType.REGISTERS)` for registers.
   - The 4-tuple gives the separate DI/coil/IR/HR tables PLAN §6.1 assumes. In this mode, bit
     addresses are per-bit.
   - `id=0` answers any unit ID.
2. Write `hook` as an `async def hook(function_code, start_address, address, count, registers,
   set_values) -> ExcCodes | None`. `SimRuntime` calls it on every request after the address check,
   before the response is built. The hook is invoked for every request, so it should only read
   memory and never block.
   - **Reads** (`set_values is None`): refill the whole block in place from the IOTable, for example
     `registers[:len(packed)] = packed`. The response is built from `registers` after the hook
     returns. Don't resize the list: the block carries a trailing slot, and the length is fixed at
     build time. A whole-block refill avoids offset math. If a block grows large enough that only the
     requested window should be refreshed, note the units:
     - For registers (FC03/04), `address` and `count` are register numbers and the index is
       `address - start_address`.
     - For bits (FC01/02/05/15), `address` is a *bit* address but `start_address` is a *register*
       index and `count` is a *register* count. The probe saw `read_discrete_inputs(0, count=20)` reach
       the hook as `count=2`, so the word index is `address // 16 - start_address`.
     - Bits are packed 16 per word, bit 0 first; use
       `pymodbus.simulator.simutils.SimUtils.bitsToRegisters` / `registersToBits`.
   - **Writes** (FC05/06/15/16): `set_values` holds the request values, one per bit or register,
     starting at the request `address`. Push them into the IOTable here. The hook must be idempotent:
     the probe saw a single FC05 `write_coil` call it twice. The PLC-side write restriction on DI/AI is enforced by the protocol itself, since there is
     no write function code for discrete inputs or input registers. Return an `ExcCodes` member to
     reject a request.
3. Start the server with `await StartAsyncTcpServer(device, address=("127.0.0.1", 5020))`, or with
   `ModbusTcpServer(device, address=...)` and `serve_forever()` when other servers share the process.
   The default `address` is `("", 502)`, which listens on all interfaces. OpenFactory must always pass
   `address` explicitly to keep the loopback default required by CLAUDE.md.

**Addressing and encoding:**
- There is no +1 offset. `SimData.address` is documented as "starting with 0 (identical to the
  requests)", and the probe confirmed that FC04 address 0 returns IOTable index 0. The legacy shim
  still applies `address - 1` inside `ModbusSequentialDataBlock`, which is one more reason to avoid it.
- Float32 helpers: `client.convert_to_registers(v, data_type=client.DATATYPE.FLOAT32,
  word_order="big")` and `convert_from_registers(...)`. These are useful in the roundtrip test (PLAN 1.7).

**Probe results** (scratchpad, not committed; 14 of 14 checks passed using the whole-block refill
pattern above, covering FC01/02/03/04/05/15/16, which include every function code OpenPLC's Slave
Devices poll sends):
- **Live reads:** DI bits 0 and 17 read live from the dict. IR address 0 read live with no offset.
  Changing the dict changed the next read with no register write.
- **Float32:** a float32 in IR 2–3 decoded as 42.5 with big word order.
- **Non-zero starts:** `read_discrete_inputs(17, 3)`, the last bit (31), `read_input_registers(5, 2)`,
  and `read_holding_registers(5, 3)` returned the expected values.
- **Writes:** a coil write at 17 and a float32 HR write at 4–5 landed in the dict.
  `write_coils(14, [True]*5)` set exactly bits 14–18 across the 16-bit boundary.
- **Errors:** an out-of-range read returned exception code 2.

> **Resolved 2026-10-25:** the binding mechanism is `SimDevice(action=...)` on pymodbus 3.16.1, not a
> custom datablock in a `ModbusSlaveContext`. This is an API correction only. The architecture
> (pymodbus server reading the IOTable live) is unchanged. Pin `pymodbus~=3.16.1` (that is,
> `>=3.16.1,<3.17`). v4 removes the deprecated context classes, and `SimDevice` still carries an
> "experimental" docstring, so treat each minor upgrade as a re-test of the PLAN 1.7 roundtrip.

## Soft PLCs and controllers for testing

| Target | Access | License | Notes |
|--------|--------|---------|-------|
| OpenPLC Runtime v3 | Modbus TCP server on 502 by default; polls external slaves via web UI "Slave Devices" (slave ID, IP, port, start address + size per DI / coil / input-register / holding-read / holding-write block) | GPL-3.0 (separate process, no linking; GitHub license API: `GPL-3.0`) | Free end-to-end path. Programs in IEC 61131-3 via OpenPLC Editor. Source: https://github.com/thiagoralves/OpenPLC_v3 (HEAD `b5d4135`, 2026-04-04). Register behavior: see "OpenPLC Runtime v3 Modbus mapping and float32" below. |
| Studio 5000 Logix Emulate | Virtual backplane; Ethernet reachability from third-party clients is the open question | Commercial (HIS Controls licensed) | PLAN §2.4 research spike. Candidate bridges: RSLinx Classic virtual backplane, FactoryTalk Linx Gateway OPC UA, Emulate SDK. |
| CompactLogix 5380 / ControlLogix 5580 (bench) | EtherNet/IP via pycomm3 | Hardware | Reference path for Rockwell. |
| CODESYS Control Win | Modbus TCP, OPC UA server | Free for non-commercial / time-limited | Useful for IEC 61131 demos beyond OpenPLC. |
| Ignition 8.3 | OPC UA client to our server; MQTT Engine for Sparkplug | Trial mode free, 2-hour resets | Primary SCADA target. See `~/.claude/rules/ignition-conventions.md`. |

## OpenPLC Runtime v3 Modbus mapping and float32

Sources are the OpenPLC_v3 source code at commit `b5d41356dab4aeadca0dd7ca64ba542f870b595d` (master,
2026-04-04). Permalink base: `https://github.com/thiagoralves/OpenPLC_v3/blob/b5d41356dab4aeadca0dd7ca64ba542f870b595d/`.
The legacy v3 documentation pages (`autonomylogic.com/docs/2-5-modbus-addressing`, `.../2-6-slave-devices`)
now redirect to the Autonomy Edge docs index. The current Edge addressing page
(https://edge.autonomylogic.com/docs/openplc-editor/communication/modbus/addressing) describes a
configurable layout and does not say whether it applies to v3. For v3, the source code is the authority.

### OpenFactory as slave (default path): OpenPLC polls OpenFactory

- **Address window.** Slave Devices data lands at index 100 onward. The UI says so verbatim: "Slave
  devices are attached to address 100 onward (i.e. %IX100.0, %IW100, %QX100.0, and %QW100)"
  (`webserver/webserver.py#L1179`). The buffers hold 400 entries (`MAX_MB_IO 400`,
  `webserver/core/modbus_master.cpp#L44`), so the usable ranges are `%IX100.0`–`%IX149.7`,
  `%QX100.0`–`%QX149.7`, `%IW100`–`%IW499`, and `%QW100`–`%QW499` (`modbus_master.cpp#L692-L720`).
- **`%IW` comes from both input registers and holding registers.** Each device has five blocks:
  Discrete Inputs → `%IX100.0`, Coils → `%QX100.0`, Input Registers → `%IW100`, Holding Registers – Read →
  `%IW100`, and Holding Registers – Write → `%QW100` (UI labels at `webserver/pages.py#L1533-L1557`).
  Per poll and per device, the input-register block is read with FC04 (`modbus_read_input_registers`,
  `modbus_master.cpp#L500-L534`). The holding-read block is read with FC03 (`modbus_read_registers`,
  `#L536-L569`) and appended *after* the input-register words in the same `%IW` sequence. Devices are
  packed in the order they are listed.
- **`%QW` goes to holding registers.** `%QW100`… are written with FC16 (`modbus_write_registers`,
  `#L571-L600`) as a whole block on every poll, not on change. Coils are written with FC15
  (`modbus_write_bits`, `#L483`).
- **No float32 decoding.** Every register is copied as one raw 16-bit word into one `%IW`/`%QW`
  (`uint16_t int_input_buf[]` / `int_output_buf[]`, `#L48-L51`, `#L527`, `#L562`, `#L581`). OpenPLC
  has no word-order setting on the Slave Devices path. If OpenFactory serves a float32 AI as two
  registers, OpenPLC delivers two adjacent `%IW` words. The IEC program must reassemble them, and the
  program is what decides word order. Only `int_input` is filled, so `%ID100` and up receive nothing
  from slave devices.
- **Timing.** The poll period defaults to 100 ms (`#L86`, `sleepms(polling_period)` at `#L606`). It
  is configurable as "Slave_polling" in the runtime Settings (`webserver.py#L279-L284`). Photo-eye
  pulses shorter than one poll can be missed; scenes should not depend on them.

### OpenFactory as master: OpenFactory polls OpenPLC's own server

The server address map is in `webserver/core/modbus.cpp#L34-L44` and `mapUnusedIO()` (`#L120-L160`):

| OpenPLC location | Modbus table | Addresses |
|------------------|--------------|-----------|
| `%IX0.0`… | Discrete inputs (FC02), read-only | 0–8191 |
| `%QX0.0`… | Coils (FC01/05/15) | 0–8191 |
| `%IW0`… | Input registers (FC04), read-only | 0–1023 |
| `%QW0`… | Holding registers | 0–1023 |
| `%MW0`… | Holding registers | 1024–2047 |
| `%MD0`… (32-bit) | Holding registers, 2 per variable | 2048–4095 |
| `%ML0`… (64-bit) | Holding registers, 4 per variable | 4096–8191 |

- **`%MD` is high word first.** Reading and writing both put the high word at the even (lower)
  address (`modbus.cpp#L370-L392` read, `#L589-L603` write). The glue generator binds any variable
  located at `%MD` through an `IEC_UDINT *` pointer cast
  (`utils/glue_generator_src/glue_generator.cpp#L225-L226`). So a `REAL` located at `%MD` should appear
  over Modbus as its raw IEEE-754 bits in big-endian word order, the same layout as PLAN §6.1's
  default. That is inferred from the cast, not tested on a live runtime.
- **Writable areas.** A master can only write coils and holding registers. `%IX` and `%IW` are
  read-only over Modbus, and any `%QX`/`%QW` the program assigns is overwritten on each scan. In this
  direction, sensor values from OpenFactory belong in `%MW`/`%MD`.

### Consequence for OpenFactory (no decision changed)

PLAN §6.1 already maps AI to input registers (FC04) and AO to holding registers (FC03/06/16). That
matches OpenPLC's "Input Registers (%IW100)" and "Holding Registers – Write (%QW100)" blocks, so no
change is needed. The default float32 word order (high word first) is also the only 32-bit
convention OpenPLC defines itself (`%MD`), so it is the right default.

The catch is that OpenPLC's slave path hands the program raw words. Whether a v3 IEC program can turn
two `WORD`s into a `REAL` bit-for-bit (matiec has no standard bit-cast) is unconfirmed. Until it is,
the OpenPLC example (PLAN 1.14) should expect AI/AO as 16-bit integers. The existing "configurable"
encoding in PLAN §6.1 is the hook for that. Choosing a scaled-int16 option is a PLAN/ADR decision
and is not made here.

> **Resolved 2026-10-25:** `%IW100`… is fed by both input registers (FC04) and holding-read registers
> (FC03), in that order per device; `%QW100`… is written to holding registers with FC16. OpenPLC v3
> applies no float32 decoding or word order on the Slave Devices path. Cited from source at
> `b5d4135` because the v3 doc pages no longer resolve.

## Existing open-source simulators surveyed (not forked)

- **Gazebo / Webots** (Apache-2.0): robotics simulators. Heavy, desktop-first, no PLC protocol layer, scene
  formats are robotics-oriented (SDF, PROTO). Could serve as inspiration for the flow-network and physics
  split, nothing more.
- **Various GitHub "conveyor simulator" repos**: mostly single-scene demos with hard-coded logic. None has
  a declarative multi-component scene schema plus a protocol server. Confirms the gap OpenFactory fills.
- **OpenPLC's own simulator examples**: Modbus demos, no 3D.

## Standards referenced

- IEC 61131-3 (PLC languages) for the example programs.
- ISA-5.1 for tag naming; ISA-101 for viewer colors; ISA-18.2 for any alarm demos.
- Modbus Application Protocol V1.1b3 (public, modbus.org) for register semantics.
- ODVA EtherNet/IP and CIP specifications are not public; rely on pycomm3/libplctag and vendor manuals
  via `ai-memory-vendor-docs`.
- Eclipse Sparkplug B 3.0 specification (public).

## Open questions carried into Milestone 1/2

1. ~~Exact current versions of `@dimforge/rapier3d-compat`, `pymodbus`, `pycomm3`, `asyncua` to pin.~~
   **Resolved 2026-10-25:** 0.21.0, 3.16.1, 1.2.16, and 2.1.0. See the tables above.
2. Logix Emulate connectivity (PLAN §2.4). Open; Milestone 2 spike 2.1.
3. Whether Ignition's Logix driver can reach Emulate through the same bridge; if yes, one bridge serves
   both OpenFactory and SCADA for demos. Open; Milestone 2 spike 2.1.
4. ~~Float encoding convention for Modbus AI/AO across OpenPLC.~~ **Resolved 2026-10-25:** OpenPLC v3
   does no float decoding on Slave Devices. Registers arrive as raw 16-bit words (`%IW100`… from FC04
   and FC03; `%QW100`… written with FC16). OpenPLC's own 32-bit convention (`%MD`) is high word first,
   which matches PLAN §6.1's default. See "OpenPLC Runtime v3 Modbus mapping and float32".
5. **New, from the 2026-10-25 pass:** how an OpenPLC v3 IEC program reassembles two `%IW` words into a
   `REAL` bit-for-bit. matiec has no standard bit-cast. This decides whether the PLAN 1.14 OpenPLC
   example uses float32 or scaled int16 for AI/AO. Open.
6. **New, from the 2026-10-25 pass:** the Rapier photo-eye sensor API (`setSensor(true)`,
   `ActiveEvents.COLLISION_EVENTS`, `drainCollisionEvents`) is confirmed in the 0.21.0 typings only.
   It has not been exercised. Check it in PLAN 1.12.
