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
  (PLAN §6.4).
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

| Port | Kind | Required | Meaning |
|---|---|---|---|
| `run` | DO | yes | Run command |
| `speed` | AO, 0 to 100 % | no | Speed command; unbound means 100 % |
| `running` | DI | no | Running feedback; follows `run` in the same tick |

The surface speed follows the target (`speed_mps` × speed % while `run`, else 0) with a linear ramp
of 0.3 s for a full-scale change. Parts accelerate at the scene friction μ × 9.81 m/s².

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

| Port | Kind | Required | Meaning |
|---|---|---|---|
| `run` | DO | yes | Run command |
| `speed` | AO, 0 to 100 % | no | Speed command |
| `running` | DI | no | Running feedback |

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
- Parts on the deck turn with it about the centre; the deck belt carries them along its current
  heading.
- A turntable may appear in several `connections`, feeding (or fed by) different conveyors. A
  connection to or from the deck hands parts across only while the two conveyors' headings agree
  within 1 degree. A part reaching the end of a conveyor whose connections are all out of line
  waits there, as a box held against a turned deck's side would. The viewer's physics has no wall
  at that end, so a scene stops parts before it (a stopper ahead of the deck, or a stopped
  belt).
- In the viewer, parts on a turning deck follow the core's position.

Faults: `stuck_on`, `stuck_off`, `jammed`, `slow` (jammed stops the deck belt and the turn; stuck
faults act on `running`, `at_0`, `at_90`).

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
straddling the seam.

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

- `grip` on while the pad is fully lowered picks the highest part whose top is within 2 cm of the
  pad and whose footprint overlaps it. The part moves with the pad, keeping its offset. `grip` off
  drops it where it is, and it falls onto whatever is below.
- The pad does not collide with parts; size `stroke` so the lowered pad meets the part's top.
- In the viewer, held parts follow the core's position.

Faults: `stuck_on`, `stuck_off`, `jammed`, `slow` (jammed freezes both axes; a held part stays
held).

## Sensors

### `photo_eye`

A through-beam photo eye. Origin: the beam midpoint; the beam spans local z.

| Parameter | Default | Meaning |
|---|---|---|
| `beam_length` | required | Beam length, meters |

| Port | Kind | Required | Meaning |
|---|---|---|---|
| `detect` | DI | yes | True while a part blocks the beam (20 ms debounce on both edges) |

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

## Parts

### `box_emitter`

Spawns boxes. Origin: the spawn point, the centre of each new part.

| Parameter | Default | Meaning |
|---|---|---|
| `interval_s` | required | Time between spawns, seconds |
| `jitter_s` | 0.0 | Uniform ± jitter on each interval |
| `parts` | required | Part types: `{name, size, weight}`; `weight` is the relative chance |

| Port | Kind | Required | Meaning |
|---|---|---|---|
| `enable` | DO | no | Emitter enable; unbound means always enabled |

A due part waits until its spawn volume is clear, and the next interval starts from that spawn. The
scene holds at most 200 parts; a spawn over the cap is skipped and counted.

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
