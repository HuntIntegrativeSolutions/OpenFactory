# OpenFactory component reference

Every component type a scene can use, with its parameters, its I/O ports, and how it behaves. The
authoritative definitions are the scene schema (`src/openfactory/core/schema.py`, exported to
`web/public/scene.schema.json`) and each type's module docstring in `src/openfactory/components/`;
`tests/unit/test_component_reference.py` checks that every registered type has a section here
naming all of its parameters and ports.

OpenFactory is a training and demo tool. It is not a safety-rated simulation.

## Conventions

- **Common fields.** Every component has `id` (all caps, for example `CONV_01`), `type`,
  `description` (ISA-5.1 style), `position` `[x, y, z]` in meters, and `rotation_deg` (about +y,
  default 0). The frame is right-handed with y up and x along the main flow; seen from above,
  positive `rotation_deg` turns local +x toward world -z. Each section says where the origin is.
- **Ports.** A port is bound to a tag in `io:`. Commands from the PLC are `DO`/`AO`, feedback to
  the PLC is `DI`/`AI`, and the tag's suffix must match. A binding is a tag name or a mapping
  (`{tag, address, initial}`, plus the 2.17 transforms `invert`, `on_delay_s`, `off_delay_s`,
  `combine`/`from`, and `window` on an AI port). Required ports must be bound; optional ones may be
  left out, and an unbound command reads as its default.
- **Limit switches.** Actuators report their ends the same way: a limit DI is true only at its
  end of travel, and false while moving. Commands are held, not pulsed: while the command is true
  the actuator drives to its far end, and when it is released it drives back, reversing at once on
  a change mid-travel. Limits are written from the first tick.
- **Sized defaults.** Some heights default to sizes taken from the scene at load time (the tallest
  part any `box_emitter` can spawn, or the belt a component stands on). `openfactory lint` warns
  (W206) when a plate, blade, or arm top set by hand is below an emitted part's centre of mass.
- **Faults (PLAN 2.6).** Every component has `fault_inject(kind, **params)` and `clear_fault()`;
  each section lists the kinds its type supports. One fault per component at a time: injecting
  replaces the current one. Inject from the viewer (right-click a component, or long-press it on a
  touch screen) or over HTTP (`POST /api/fault` with `{"id", "kind", "params"}`, `GET /api/fault`
  for the active ones and what each component supports, `DELETE /api/fault/{id}`). A fault acts
  from the next tick; the component's label shows it in ISA-101 alarm yellow. On loopback the
  fault API is open; with the viewer bound beyond loopback it needs the token set with
  `--fault-token` or `[server] fault_token` (in `openfactory.local.toml`), sent in the
  `X-OpenFactory-Fault-Token` header (the menu asks for it once), and with no token it is off
  (PLAN §6.4). Headless, `openfactory run` and `openfactory test` take
  `--fault ID=KIND[:key=value,...]` and `--fault-at T` (PLAN 2.11), for example
  `--fault CYL_01=slow:factor=0.25 --fault-at 10`.
- **Comms faults (PLAN 2.13, 2.13b)** belong to the protocol servers, not a component, and
  leave the simulation running. One fault covers every server in the run, Modbus TCP and OPC UA
  alike. `POST /api/fault` with `{"comms": {"drop": true}}` makes the servers ignore every request
  (no reply: the PLC's or the OPC UA client's requests time out, and a write in a dropped request
  is not applied, as on a pulled cable; OPC UA subscriptions get no notifications either);
  `{"comms": {"latency_ms": 300}}` answers every request 300 ms late (and sends each OPC UA
  notification 300 ms late). `DELETE /api/fault/comms` clears it and `GET /api/fault` shows it
  under `comms`, with the servers it covers under `protocols`. In the viewer, right-click (or
  long-press) empty space for the comms menu: drop, a 250 or 1000 ms delay, or clear. Headless:
  `--fault comms=drop` or `--fault comms=latency:ms=300`, with `--fault-at T`. Use it to test a
  program's comms watchdog, how it treats stale inputs, or Ignition's Bad quality. Latency
  assumes the client waits for each reply before its next request, as PLC Modbus masters
  (OpenPLC included) do. On OPC UA, where a client keeps several requests in flight (publish
  requests, a watchdog read), each request is handled the latency after it arrived, as a slow
  network delivers it, so two requests sent together are both answered one latency later, in
  order. A connection's requests are always answered in the order sent: lowering or clearing
  the latency while requests wait lets new ones through only behind them. A client whose own watchdog or request timeout is shorter than the latency sees a dead
  server: asyncua's client probes every `watchdog_intervall` (default 1 s) and disconnects when
  a probe times out. An asyncua client whose requests timed out closes its connection and must
  reconnect once the fault clears; Ignition reconnects by itself.
  - `stuck_on` / `stuck_off` (`port`: one DI port, default all): the device's DI output reads
    true / false. It is the device's output, before any binding transform, so an NC eye stuck on
    reads false at the PLC. That is where a real fault lives: the sensor's output is what sticks,
    and NC wiring (`invert: true` on the binding) still inverts it on the way to the PLC. Not for
    analog outputs.
  - `jammed`: the belt or actuator stops where it is; feedback stays honest.
  - `slow` (`factor`, default 0.5): it moves at that fraction of its speed.
  - `misaligned` (`offset_m`, default 0.05): the sensor sees that far late (or early, negative).
  - `noisy` (`rate` per second, default 0.5; `duration_s`, default 0.2): random dropouts, long
    enough for 100 ms PLC polling to see; a DI drops to false, an AI reads the top of its range.
    Seeded per component, so a run replays and other random draws are not shifted.
  - `drift` (`rate` units per second, default 0.01): an analog reading grows with simulated time.
- **Custom mesh (PLAN 2.15).** Any component may add `mesh: {file, scale, offset, rotation_deg}`
  to draw its own `.glb` model in place of its fixed housing; `docs/scene-format.md` has the
  fields. The mesh is visual only. What it replaces, per type: the cylinder body (`pusher`), the
  actuator housing under the belt (`stopper`), the pivot (`diverter_arm`), the gantry beam,
  portal frames, and legs (`pick_and_place`), the sensor housing (`distance_sensor`,
  `color_sensor`; the taught-color swatch stays), side rails, legs, and end drums
  (`belt_conveyor`), side rails and legs (`roller_conveyor`), the pedestal (`turntable`), and the
  panel plate (`pushbutton`, `pilot_light`; `selector_switch` also its bezel). Moving and
  status-colored parts (plate, rod, blade, arm, carriage, pad, deck, belt, rollers, beams, lamps)
  stay primitives. `photo_eye` (its heads move with a `misaligned` fault), `box_emitter`, and
  `part_remover` (outlines of a zone, not equipment) have no housing: their mesh is drawn beside
  the primitive.
- **Default models (PLAN 4.2).** Without a scene `mesh`, a type that has one draws its housing
  from OpenFactory's own model (`web/public/housings/<model>.glb`, built by
  `tools/make_models.py`, our own CC-BY-4.0 work, credited in `ATTRIBUTION.md`), fitted to the
  component's declared dimensions: rails stretched to its length, legs to its height above the
  floor. A model is not one fixed shape per type: it is a kit of named parts that the type's
  builder places and stretches from the scene, so one file fits every size, and types may share
  a file (`belt_conveyor` and `roller_conveyor` both use `conveyor_frame.glb`). A component's
  copies are merged into one mesh per material, so a model costs a few draw calls. It
  replaces the same parts a scene mesh would and is visual only; a scene `mesh` still wins.
  If a model fails to load, the primitive stays. The ten types with a default model:
  `belt_conveyor` (channel rails, legs on feet, braces, end drums and bearing blocks);
  `roller_conveyor` (the same frame, no drums); `photo_eye` (emitter and receiver heads with a
  face plate, lens and cable gland, on angle brackets; they hang with the beam, so a
  `misaligned` fault moves them, although a scene mesh on a photo eye is still drawn beside);
  `pusher` (a round aluminium barrel and four tie rods stretched to the stroke, square end caps
  with the rod gland and air ports, and a mounting foot under each cap);
  `stopper` (the actuator under the belt: a flange plate, a square cylinder body with a round
  barrel end, and two air ports);
  `turntable` (the pedestal: a slewing bearing under the platform, a round column stretched to
  the floor, and a base plate with a collar; none when the turntable stands under 0.1 m from
  the floor);
  `diverter_arm` (the drive on the pivot: a hub, a right-angle gearbox, and the motor behind
  the pivot, away from the arm at rest);
  `pushbutton` (the enclosure, a legend plate, and an aluminium guard collar around the cap);
  `pilot_light` (the enclosure, a legend plate, and a faceted aluminium bezel ring around the
  lens);
  `selector_switch` (the enclosure, a legend plate, a dark bezel, and index marks at the a,
  center and b positions).
- **Viewer authority (PLAN §3.3).** Parts on conveyors, pushed by a plate, or held by a stopper
  are simulated by the viewer's Rapier physics. Parts held by a gripper, touching a diverter arm,
  or on a turning turntable deck follow the core's position. Sensors are read by the core only.

## Conveyors

Conveyors carry parts. They can be joined with `connections:` (`{from: A, to: B}`), which hand a
part from A's discharge end to B's infeed across any gap or height step.

### `belt_conveyor`

A powered belt. Origin: centre of the infeed end at belt-top height; flow is local +x.

| Parameter | Default | Meaning |
|---|---|---|
| `length` | required | Belt length along local x, meters |
| `width` | required | Belt width, meters |
| `speed_mps` | required | Surface speed at 100 % speed command |
| `bidirectional` | false | Runs both ways; needs `reverse` bound (PLAN 2.5b) |

| Port | Kind | Required | Meaning |
|---|---|---|---|
| `run` | DO | yes | Run command |
| `speed` | AO, 0 to 100 % | no | Speed command; unbound means 100 % |
| `running` | DI | no | Running feedback; follows `run` in the same tick |
| `reverse` | DO | with `bidirectional` | Reverse command: with `run`, flow toward the infeed end |

The surface speed follows the target (`speed_mps` × speed % while `run`, else 0) with a linear ramp
of 0.3 s for a full-scale change. Parts accelerate at the scene friction μ × 9.81 m/s².

**Reversing (PLAN 2.5b).** With `bidirectional: true` and `reverse` on, `run` drives the belt toward
the infeed end at the same speed; `reverse` alone does nothing, and a change of direction ramps
through zero (0.6 s for a full reversal). Parts queue nose to tail in reverse as forward, a raised
stopper blade stops a part coming from its far side, and a part passing the infeed end hands back
to the conveyor feeding this one through an active connection (at a merge, the feeder whose
discharge end is nearest), else it falls off the end; fed only by an out-of-line or out-of-level
deck, it waits at the infeed end. The viewer draws an arrow on the near rail, pointing the way the
belt runs. Example: `tests/scenes/demo-reversing.yaml`.

Faults: `stuck_on`, `stuck_off`, `jammed`, `slow` (jammed stops the belt at once and `running` still
follows `run`; stuck faults act on `running`).

### `roller_conveyor`

Driven rollers. Same origin, ports, and speed behavior as `belt_conveyor`.

| Parameter | Default | Meaning |
|---|---|---|
| `length` | required | Conveyor length, meters |
| `width` | required | Roller width, meters |
| `speed_mps` | required | Roller surface speed at 100 % speed command |
| `roller_pitch` | 0.1 | Roller spacing, meters (viewer only) |
| `friction` | scene friction | Roller-to-part μ; lower values make parts take longer to reach speed |
| `bidirectional` | false | Runs both ways, as `belt_conveyor`; needs `reverse` bound |

| Port | Kind | Required | Meaning |
|---|---|---|---|
| `run` | DO | yes | Run command |
| `speed` | AO, 0 to 100 % | no | Speed command |
| `running` | DI | no | Running feedback |
| `reverse` | DO | with `bidirectional` | Reverse command, as `belt_conveyor` |

Parts on it accelerate at its own `friction` × 9.81 m/s² (9 ticks to 0.5 m/s at μ 0.3, against 4
at μ 0.8), in the headless model and in the viewer alike.

Faults: `stuck_on`, `stuck_off`, `jammed`, `slow` (as `belt_conveyor`).

### `turntable`

A short belt (the deck) on a platform that turns 90 degrees. Origin: the deck centre (the pivot)
at deck-belt top height; at 0 degrees the deck flows along local +x. Unlike `belt_conveyor`, the
origin is not the infeed end.

| Parameter | Default | Meaning |
|---|---|---|
| `length` | required | Deck length, meters |
| `width` | required | Deck width, meters |
| `speed_mps` | required | Deck belt speed while running |
| `turn_deg` | 90 | 90 (counter-clockwise from above) or -90 |
| `turn_time_s` | 1.0 | Time for the full turn, seconds |

| Port | Kind | Required | Meaning |
|---|---|---|---|
| `run` | DO | yes | Deck belt run command |
| `turn` | DO | yes | Turn command |
| `running` | DI | no | Deck belt running feedback |
| `at_0` | DI | no | At 0 degrees |
| `at_90` | DI | no | At `turn_deg` |

- `turn` held true drives the deck to `turn_deg`; `turn` released returns it to 0. This is the
  same convention as the pusher's `extend`. `at_0` and `at_90` are both false while moving.
- Parts on the deck turn with it about the centre, in orientation as well as position (PLAN
  2.5b): a long box leaves a turned deck turned. Collision and sensing use its box rounded to the
  nearest 90 degrees, exact once the deck stops. The deck belt carries parts along its current
  heading.
- A turntable may appear in several `connections`, feeding (or fed by) different conveyors. A
  connection to or from the deck hands parts across only while the two conveyors' headings agree
  within 1 degree. A part reaching the end of a conveyor whose connections are all out of line
  waits there, as a box held against a turned deck's side would. The viewer's physics has no wall
  at that end, so a scene stops parts before it (a stopper ahead of the deck, or a stopped
  belt), as `scenes/two-lane-merge.yaml` does.
- In the viewer, parts on a turning deck follow the core's position.

Faults: `stuck_on`, `stuck_off`, `jammed`, `slow` (jammed stops the deck belt and the turn; stuck
faults act on `running`, `at_0`, `at_90`).

### `lift`

A short belt (the deck) that rises and falls between two heights (PLAN 2.5b). Origin: the centre
of the deck's infeed end at deck-belt top height with the deck at the bottom, as `belt_conveyor`;
flow is local +x and the deck rises straight up.

| Parameter | Default | Meaning |
|---|---|---|
| `length` | required | Deck length, meters |
| `width` | required | Deck width, meters |
| `speed_mps` | required | Deck belt speed while running |
| `lift_height` | required | Rise from the bottom to the top, meters |
| `lift_time_s` | 2.0 | Time for the full rise, seconds |
| `start` | `bottom` | Where the deck starts: `bottom` or `top` |

| Port | Kind | Required | Meaning |
|---|---|---|---|
| `run` | DO | yes | Deck belt run command |
| `raise` | DO | yes | Raise command |
| `running` | DI | no | Deck belt running feedback |
| `at_top` | DI | no | At the top |
| `at_bottom` | DI | no | At the bottom |

- `raise` held true drives the deck to the top; released, it returns to the bottom (the stopper's
  `raise` and the turntable's `turn` work the same way). `at_top` and `at_bottom` are both false
  while moving.
- Parts on the deck move up and down with it; the deck belt carries them along local +x.
- A lift may appear in several `connections`, feeding (or fed by) conveyors at different heights.
  A connection to or from the deck hands parts across only while the two belt tops are level
  within 1 mm, so a lift fed at the bottom hands parts on at the top. A part reaching the end of a
  conveyor whose connections are all out of level waits there. A part sticking out over the
  deck's end when it starts to move goes with the deck: wait for `at_top` or `at_bottom` before
  running the deck belt.
- In the viewer, parts on a moving deck follow the core's position. Example:
  `tests/scenes/demo-lift.yaml` with its stand-in script.

Faults: `stuck_on`, `stuck_off`, `jammed`, `slow` (jammed stops the deck belt and the lift; slow
runs both at half speed; stuck faults act on `running`, `at_top`, `at_bottom`).

## Actuators

### `pusher`

A single-solenoid, spring-return linear actuator. Origin: centre of the retracted push-plate face;
the plate extends along local +z.

| Parameter | Default | Meaning |
|---|---|---|
| `stroke` | required | Plate travel, meters |
| `extend_time_s` | required | Time for the full stroke, seconds |
| `plate_width` | 0.3 | Plate width along local x, meters |
| `plate_height` | sized | Plate height, centred on the origin; unset: the plate top reaches 3/4 of the tallest emitted part above the belt it sweeps (0.15 m without one) |

| Port | Kind | Required | Meaning |
|---|---|---|---|
| `extend` | DO | yes | Extend command |
| `extended` | DI | no | Extended limit switch |
| `retracted` | DI | no | Retracted limit switch |

An advancing plate pushes every part it overlaps along local +z; a retracting plate does not pull.
`openfactory lint` warns when the plate top is below an emitted part's centre of mass (W206) or the
stroke stops short of the far edge of the belt the plate sweeps (W207), which leaves a part
straddling the seam. When a gap separates that belt from the belt beyond, W207 asks for the
stroke that puts the whole part on the belt beyond: its near edge plus half the deepest emitted
part's depth along the stroke ("stroke 0.6 m leaves the part straddling the seam; 0.9 m puts it
fully on CONV_02").

Faults: `stuck_on`, `stuck_off`, `jammed`, `slow` (jammed freezes the plate where it is, with honest
limits; stuck faults act on the limit switches).

### `stopper`

A blade that rises out of the belt to hold parts. Origin: centre of the blade's upstream face at
belt-top height; the blade spans local z and stops flow along local +x.

| Parameter | Default | Meaning |
|---|---|---|
| `width` | belt width | Blade width across the flow; unset: the width of the belt under it |
| `height` | sized | Raised blade top above the belt; unset: the tallest emitted part's centre of mass, at least 0.05 m |
| `raise_time_s` | 0.2 | Time to raise or lower, seconds |

| Port | Kind | Required | Meaning |
|---|---|---|---|
| `raise` | DO | yes | Raise command |
| `raised` | DI | no | Raised limit switch |
| `lowered` | DI | no | Lowered limit switch |

- While the blade is anywhere above fully lowered, a part whose front face reaches the blade stops
  there and the parts behind it queue nose to tail. Lowering releases them once the blade is fully
  down, first in, first out.
- A part already over the blade line when the blade rises passes over it; the next one stops.
- A spring-raised stopper (up when de-energized) is `raise: {tag: X_DO, invert: true}`.
- In the viewer the blade's collider is switched off once it is fully lowered, so a part sliding
  over a blade lying flush with the belt cannot catch on its edge.
- Queue formation against a raised stopper is a parity trace (`parity-stopper`): Rapier and the
  headless model agree within 2 ticks.

Faults: `stuck_on`, `stuck_off`, `jammed`, `slow` (jammed freezes the blade, so a blade jammed
partly up still holds parts).

### `diverter_arm`

A paddle on a pivot at the edge of a belt. Origin: the pivot at belt-top height; at rest the arm
lies along local +x.

| Parameter | Default | Meaning |
|---|---|---|
| `length` | required | Arm length from the pivot, meters |
| `height` | sized | Arm top above the belt; unset: 3/4 of the tallest emitted part (0.15 m without one) |
| `swing_deg` | 45 | Extended angle, -90 to 90 and not 0; positive swings toward local +z |
| `extend_time_s` | 0.5 | Time to swing out or back, seconds |

| Port | Kind | Required | Meaning |
|---|---|---|---|
| `extend` | DO | yes | Extend command |
| `extended` | DI | no | Extended limit switch |
| `retracted` | DI | no | Retracted limit switch |

The arm is a frictionless fence for parts on its belt and on belts level with it, such as the side
belt under its tip, so a part already partly across the seam is still guided. A part carried
into an extended arm slides along it at the belt speed × cos(`swing_deg`) and falls onto the side
belt once its centre is past the belt edge; a swinging arm sweeps the parts it reaches. While a
part touches the arm, the viewer places it at the core's position instead of simulating it, so it
stays square to the world axes as in the headless model; the `parity-diverter` trace agrees within
1 tick.

Faults: `stuck_on`, `stuck_off`, `jammed`, `slow` (jammed freezes the arm at its current angle).

### `pick_and_place`

A two-axis pneumatic gantry with a gripper pad. Origin: bottom centre of the pad, raised, over the
pick point; the traverse runs along local +x and the pad drops along -y.

| Parameter | Default | Meaning |
|---|---|---|
| `travel` | required | Pick to place distance along local +x, meters |
| `stroke` | required | Vertical lowering distance, meters |
| `travel_time_s` | 1.0 | Time for the full traverse, seconds |
| `lower_time_s` | 0.3 | Time to lower or raise, seconds |
| `grip_size` | [0.25, 0.05, 0.25] | Pad size x, y, z, meters |

| Port | Kind | Required | Meaning |
|---|---|---|---|
| `traverse` | DO | yes | Held: travel to the place side; released: back to pick |
| `lower` | DO | yes | Held: lower the pad; released: raise it |
| `grip` | DO | yes | Grip command |
| `at_pick` | DI | no | At the pick side |
| `at_place` | DI | no | At the place side |
| `lowered` | DI | no | Pad fully lowered |
| `holding` | DI | no | Holding a part |
| `contact` | DI | no | Pad touching a part: resting on one, or holding one (PLAN 2.5b) |

- `grip` on while the pad is fully lowered or in contact picks the highest part whose top is within 2 cm of the
  pad and whose footprint overlaps it. The part moves with the pad, keeping its offset. `grip` off
  drops it where it is, and it falls onto whatever is below.
- A lowering pad stops on the top of the highest part under it (PLAN 2.5b): over a part taller than
  the stroke allows, the pad rests on it with `lowered` false (`lowered` is the full-stroke limit,
  the pad's true position) and `contact` true, and `grip` picks it there. Only the pad collides; a
  held part does not stop on what is below it. Size `stroke` so the lowered pad meets the part's
  top for a full-stroke pick.
- In the viewer, held parts follow the core's position.

Faults: `stuck_on`, `stuck_off`, `jammed`, `slow` (jammed freezes both axes; a held part stays
held).

## Sensors

### `photo_eye`

A through-beam photo eye. Origin: the beam midpoint; the beam spans local z.

| Parameter | Default | Meaning |
|---|---|---|
| `beam_length` | required | Beam length, meters |
| `debounce_ms` | 20 | On/off debounce of `detect`, milliseconds, 0 to 1000 (PLAN 2.5b); 20 is one tick at 50 Hz |

| Port | Kind | Required | Meaning |
|---|---|---|---|
| `detect` | DI | yes | True while a part blocks the beam (`debounce_ms` debounce on both edges) |

Faults: `stuck_on`, `stuck_off`, `misaligned`, `noisy` (misaligned moves the beam `offset_m` along
the flow; positive sees late).

### `distance_sensor`

A laser or ultrasonic range sensor. Origin: the sensor face.

| Parameter | Default | Meaning |
|---|---|---|
| `axis` | `+z` | Beam direction: `+z` across the flow (turned with the sensor) or `-y` straight down |
| `beam_length` | 1.0 | Measuring range, meters |

| Port | Kind | Required | Meaning |
|---|---|---|---|
| `distance` | AI, 0 to `beam_length` m | yes | Distance to the nearest part or belt along the beam; `beam_length` when clear |
| `switching` | AI, 0 to `beam_length` m | no | The same distance, for a windowed DI binding |

- The ports' range is set per sensor, 0 to `beam_length`, so `int16_scaled` maps 0 m to raw 0
  and `beam_length` to raw 32767. The int16_scaled lockfile records that range; changing
  `beam_length` fails the lockfile check (and `openfactory lint`, E101) until the PLC's scaling is
  updated and the tag's `ranges:` line is deleted.
- Bind `switching` with a window to get a switching output, true while the distance is inside it:
  `switching: {tag: DS_01_TALL_DI, window: [0.1, 0.3]}`. The I/O list CSV shows the window on
  that row (`window 0.1 to 0.3 m`).
- The beam stops at belts as well as parts (a belt is a thin slab under its top surface), so
  looking down at a belt it reads the belt's distance when no part is under it.

Faults: `misaligned`, `noisy`, `drift` (misaligned adds `offset_m` to the reading; a dropout reads
`beam_length`).

### `color_sensor`

A diffuse color sensor taught one color (added for `scenes/sorting-by-color.yaml`, PLAN 2.7).
Origin: the sensor face.

| Parameter | Default | Meaning |
|---|---|---|
| `teach` | required | The taught color: `red`, `green`, `blue`, `yellow`, `white`, or `black` |
| `axis` | `-y` | Beam direction: `-y` straight down, or `+z` across the flow (turned with the sensor) |
| `beam_length` | 0.5 | Sensing range, meters |
| `debounce_ms` | 0 | On/off debounce of `match` and `detect`, each on its own, milliseconds, 0 to 1000 (PLAN 2.5b); 0 follows the part at the step boundary |

| Port | Kind | Required | Meaning |
|---|---|---|---|
| `match` | DI | yes | The nearest part in range has the taught color |
| `detect` | DI | no | A part is in range, any color |

- The beam stops at the nearest part or belt, as a distance sensor's does, so a sensor looking down
  at a belt sees only the part on it. A plain part (no `color`) never matches.
- No debounce: both outputs change at the step boundary. Place it over a belt where a part is
  seen for at least 200 ms if a PLC polls it every 100 ms.

Faults: `stuck_on`, `stuck_off`, `noisy` (stuck faults act on both outputs unless `port` names
one).

## Parts

### `box_emitter`

Spawns boxes. Origin: the spawn point, the centre of each new part.

| Parameter | Default | Meaning |
|---|---|---|
| `interval_s` | required | Time between spawns, seconds |
| `jitter_s` | 0.0 | Uniform ± jitter on each interval |
| `parts` | required | Part types: `{name, size, weight, color}`; `weight` is the relative chance; `color` (optional) is `red`, `green`, `blue`, `yellow`, `white`, or `black`, for a `color_sensor` to see |

| Port | Kind | Required | Meaning |
|---|---|---|---|
| `enable` | DO | no | Emitter enable; unbound means always enabled |

A due part waits until its spawn volume is clear, and the next interval starts from that spawn. The
scene holds at most 200 parts; a spawn over the cap is skipped and counted. A part without a
`color` is plain cardboard; the viewer draws a colored part in its color.

Faults: `jammed`, `slow` (jammed: nothing is spawned; slow stretches each interval by 1 / `factor`).

### `part_remover`

Deletes parts that enter its volume and counts them by part name. Origin: the volume's centre.

| Parameter | Default | Meaning |
|---|---|---|
| `size` | required | Volume size x, y, z, meters |

It has no ports.

Faults: none.

## Operator devices

### `pushbutton`

Origin: the button face. A viewer click presses it.

| Parameter | Default | Meaning |
|---|---|---|
| `mode` | `momentary` | `momentary` (a 300 ms pulse) or `maintained` (toggles) |
| `color` | `green` | Button color |

| Port | Kind | Required | Meaning |
|---|---|---|---|
| `pressed` | DI | yes | Pushbutton pressed |

Faults: `stuck_on`, `stuck_off`.

### `pilot_light`

Origin: the lens centre. Lit while `on` is true; viewer only.

| Parameter | Default | Meaning |
|---|---|---|
| `color` | `green` | Lens color |

| Port | Kind | Required | Meaning |
|---|---|---|---|
| `on` | DO | yes | Light on command |

Faults: `stuck_off` (a burned-out bulb: the light stays dark whatever `on` says).

### `counter_display`

Origin: the centre of the display face, which looks along local +z. Shows the `value` AO on
seven-segment digits, rounded to `decimals` places and right aligned; a minus sign takes a digit.
A value too large for the digits (or not a number) shows a dash in every digit. Viewer only.

| Parameter | Default | Meaning |
|---|---|---|
| `digits` | 4 | Digits shown, 1 to 6 |
| `decimals` | 0 | Digits after the decimal point, fewer than `digits` |
| `range` | 0 to the largest number shown | `[lo, hi]`: scales `value` in the int16_scaled encoding (raw 0 and 32767); the display does not clamp to it |
| `color` | `red` | Segment color: `red`, `green` or `amber` |

| Port | Kind | Required | Meaning |
|---|---|---|---|
| `value` | AO | yes | Value to display (units `count`, range from `range`) |

Faults: `stuck_off` (a dead display: blank whatever the value).

### `selector_switch`

Origin: the switch face. A viewer click turns it to the next position.

| Parameter | Default | Meaning |
|---|---|---|
| `positions` | 2 | 2 or 3 positions (3 adds `center`) |
| `initial` | `a` | Starting position: `a`, `center`, or `b` |

| Port | Kind | Required | Meaning |
|---|---|---|---|
| `pos_a` | DI | yes | In position A |
| `pos_b` | DI | no | In position B |

Faults: `stuck_on`, `stuck_off`.
