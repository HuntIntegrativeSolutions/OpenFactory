# Scene format reference

The full scene format is specified in `docs/PLAN.md` §4, and the machine-readable form is
`web/public/scene.schema.json` (generated from `src/openfactory/core/schema.py`). Components and
their parameters are in the [component reference](components.md). This page covers the scene-level blocks that need
more explanation than §4 gives.

OpenFactory is a training and demo tool, not a safety-rated simulation.

## `openfactory:`: the format version

Every scene starts with `openfactory: "0.1"`, the scene format version. It is not the
OpenFactory package version: the package is 0.2.0 and scenes are still format `0.1`, because
everything added since (component types, optional keys) is optional and older scenes still mean
the same. The format version changes only when the format breaks, and then `openfactory migrate`
rewrites older scenes (PLAN 4.7). A scene using a key or component type added in a later release
needs that release; an older one rejects the scene and names the key. The whole policy is in
[the versioning policy](versioning.md).

## `range:` and `units:` on an analog binding (PLAN 3.7)

An AI or AO binding may give its tag a span of its own:

```yaml
io:
  speed: {tag: CONV_01_SPD_AO, range: [0, 50]}           # PLC full scale is 50 % speed
  pv: {tag: TK_01_LT_AI, range: [0, 2.0], units: mH2O}   # a label for the I/O list
```

- **`range: [lo, hi]`** is the tag's engineering span, in the port's units: `int16_scaled`
  scales raw 0 to 32767 across it (ADR-0003), and the lockfile, the I/O list CSV, CONTEXT.md and
  the Ignition and L5X exports show it. The component still works in the port's physical units,
  so a speed tag ranged 0 to 50 runs the belt at half speed at full scale. The lockfile records
  the range, so a changed range fails the lockfile check until its line is deleted.
- **`units:`** is a label only (ruled 2026-11-08). It replaces the `units` column of the I/O list
  CSV and the units in CONTEXT.md's tag table, **and nothing else**: it converts no value, and
  the I/O table, the Ignition export's engineering units, the L5X stub's descriptions, the
  OPC UA server and the component all keep the port's own units. Use it to match the units a
  site's I/O list uses for the same quantity (`mH2O` for a level in m, `pct` for %); a value
  in other units needs a different `range`, not a label.
- Not with `window:`, which makes the binding a DI. An `initial` outside the binding's range
  fails at load.

## `checks:`: exercise checks (PLAN 2.11)

A scene may declare what a correct control program achieves. `openfactory test` runs the scene,
grades the run against these checks, prints one PASS or FAIL line per check and a verdict, and exits
1 if any check fails:

```bash
uv run openfactory test scenes/<scene>.yaml --script examples/pyplc/<scene>.py   # a stand-in script, fast
uv run openfactory test scenes/<scene>.yaml --plc --bind 0.0.0.0 --analog-encoding int16_scaled  # a real PLC, real time
```

The block goes after `plc:` and before `components:` in the shipped scenes:

```yaml
checks:
  duration_s: 60                                 # how long `test` runs (default 60)
  parts_reached: {REM_01: short, REM_02: [tall]} # each name reaches only that remover
  none_lost: true                                # no part falls out of the scene
  max_cycle_s: 12                                # spawn to remover, and oldest part left
```

Every key is optional. `--duration` on `test` overrides `duration_s`.

| Key | Passes when |
|---|---|
| `parts_reached` | For each remover and each part name under it: **at least one** part of that name reached that remover, **and no** part of that name reached any other remover. |
| `none_lost` | No part fell out of the scene (`lost` in the run summary is empty). |
| `max_cycle_s` | Every part a remover took spent at most this long from its spawn to its removal, **and** no part still in the scene at the end of the run is older than this. |
| `level_within` | `{TANK: {band: [lo, hi], after_s: T}}`: from `T` to the end of the run the tank's level stays in the band, **and** the run reaches `T` (PLAN 3.3). |
| `never_overflow` | `[TANK, ...]`: the tank never overflows. |
| `never_dry` | `[PUMP, ...]`: the pump never runs while its suction tank is dry. |
| `max_starts` | `{PUMP: N}`: the pump starts at most `N` times in the run, **and** at least once. |
| `max_reversals` | `{VALVE_OR_PUMP: N}`: its command turns back at most `N` times in the run (wear), **and** it moves at least once. The command is a `valve_control`'s position, a `valve_onoff`'s open command (0 or 100 %) or a running `pump`'s speed setpoint (0 while stopped), on its 0 to 100 % span. A reversal is a turn back by **more than 10 %** of the span from the furthest point the command reached in its last direction; the first move sets a direction and is not one. |
| `fractions_within` | `{TANK: {fractions: {INGREDIENT: [lo, hi]}, after_s: T}}`: from `T` to the end of the run each named ingredient's share of the tank's volume, in %, stays in its band, the tank is **never empty**, **and** the run reaches `T`. An ingredient is `water` (what a tank starts with) or one a `supply` in the scene delivers, checked at load. |
| `temperature_within` | `{TANK: {band: [lo, hi], after_s: T}}`: from `T` to the end of the run the tank's temperature, in °C, stays in the band, the tank is **never empty**, **and** the run reaches `T`. |

Two failure rules close the easy passes:

- **A stopped line fails `parts_reached`.** "At least one part reached it" is part of the check, so
  a program that never starts the belts, or a plant that delivers nothing, does not pass merely
  because nothing went the wrong way.
- **A jammed line fails `max_cycle_s`.** The limit applies to the parts still in the scene as well
  as to the parts removed, so a line where nothing is ever removed fails on the parts piling up
  behind the jam instead of passing with no removals to measure.
- **A process that never got going fails too.** `level_within` fails a tank that never reached
  its band by `after_s`, and `max_starts` fails a pump that never started, so a program that
  does nothing does not pass a process scene. Process checks name tanks and pumps, checked at
  load. Set `max_starts` from a 300 s run, which starts a cycling pump about five times as
  often as a 60 s one.
- **An empty tank holds no batch.** `fractions_within` and `temperature_within` fail a tank
  that is empty (level at or below 1 mm) at any tick from `after_s`, so a program that drains
  the batch away, or never makes one, does not pass on the fractions and temperature an empty
  tank keeps. An ingredient no inflow brought is 0 %, so filling with one ingredient fails a
  recipe that names the other.
- **A level band cannot tell control from on/off; `max_reversals` can.** On a slow valve an
  on/off program holds a level band as well as a PI loop does (the stroke rate turns it into
  integral action), so `tank-level-pid` also limits how often the valve reverses. The 10 %
  deadband is fixed: a controller passes transmitter noise times its gain to the valve (on
  `tank-level-pid` its PI's command turns back 7812 times in 300 s counting 1 % turns, 12
  counting 10 % ones), and a count of every wiggle would fail the loop the check exists to
  pass. Set the limit from a 300 s run, like `max_starts`, about 25 to 30 % above the count.
  A count over the run grows with its length, so an on/off program with enough hysteresis can
  stay under a 300 s limit for 60 s (on `tank-level-pid`, ±10 mm turns the valve 6 times in
  60 s and 29 in 300 s, against a limit of 15): the 300 s run is what grades it. That is
  ruled (2026-11-09): the 300 s run is the grading run for `max_reversals`, as PLAN §9 rule 10
  makes it for every check; the 10 % deadband stays fixed, and there is no per-minute limit.

**Live in the viewer (PLAN 4.1i).** While a scene runs (`openfactory run`), the viewer's checks
panel, at the top right, lists every check with its state, updated once a second from
`GET /api/checks`:

| State | Shown as | When |
|---|---|---|
| pending | `—`, grey (ISA-101 stopped) | Not decided yet: the run has not reached `duration_s`, and nothing has happened that makes the check fail whatever comes next |
| failed | `FAIL`, red (ISA-101 alarm) | At once, when running longer cannot make it pass: a part reached the wrong remover, a part was lost, a part took (or has already taken) longer than `max_cycle_s`, a level or batch left its band after `after_s`, a tank overflowed, a pump ran dry or started too often, a command reversed too often. Also when graded |
| passed | `PASS`, the normal text color | When graded |

At `duration_s` of simulated time the run is graded exactly as `openfactory test` grades it,
and the panel keeps that result for the rest of the run (to grade again, start the run
again). Each row also shows the detail line
`test` prints. A passed check is not green: in ISA-101 color marks what is abnormal, so only a
failure is colored. The panel is read-only (its header folds it); the instructor view shows the
same panel. A scene with no `checks:` and a replay show none.

What the scene names is checked when it loads, so a typo fails loudly instead of grading wrong:
a `parts_reached` key must be a `part_remover` in the scene, each part name must be one some
`box_emitter` makes, and a name may appear under only one remover.

Setting `max_cycle_s`: run the scene under its stand-in script, note the longest
spawn-to-remover time and the oldest part left, and set the limit about 25 to 30 % above the
larger. The shipped scenes record the measured value in a comment on the line. Check it with a
300 s run as well (`--duration 300`, PLAN §9 rule 10): a line over capacity backs up, its longest
time keeps growing, and a 60 s run can pass it by stopping early.

Faults can be part of a graded run: `--fault ID=KIND[:key=value,...]` and `--fault-at T` inject one
at simulated time T, so an exercise can ask how a program handles a jammed pusher or a stuck eye.
`run` and `test` also print a statistics block (throughput, utilization, actuator cycles, dwell,
losses) just before the run summary line; `GET /api/stats` serves the same block while a scene
runs.

## `exam: switch`: exam mode (PLAN 2.11)

A DI binding may carry `exam: switch`:

```yaml
- id: PE_01
  type: photo_eye
  io:
    detect: {tag: PE_01_DI, exam: switch}
```

Without `--exam` it changes nothing. With `openfactory run SCENE --exam`, each such tag follows an
operator switch instead of the simulation, starting off. The viewer shows an exam panel (top
right) with one switch per tag, so an instructor can test a program against hand-driven inputs
while trainees watch the plant: the sensor still sees the parts in 3D, but the PLC sees the switch.
A switch takes effect between ticks and the program sees it on its next scan, so hold it for at
least one PLC scan, as you would a real switch. In exam mode state frames carry no fault markers
(no alarm-yellow label, a misaligned eye drawn where it belongs), so trainees have to find an
injected fault from the PLC's side; the instructor still sees active faults in the fault menu and
at `GET /api/fault`.

`GET /api/exam` lists the switches; `POST /api/exam` with `{"tag", "on"}` turns one. Turning a
switch is an instructor action, guarded like `/api/fault`: open on loopback, and beyond loopback
it needs the fault token (`--fault-token` or `[server] fault_token`). Only a DI binding takes
`exam`; `switch` is the only value. The shipped sorter marks `PE_01_DI`.

**Instructor view (PLAN 4.12).** `http://<server>/instructor#token=<fault token>` is the same
viewer for the instructor: fault markers show on the labels even in exam mode, and the exam panel
shows every switch read-only (title "(read-only)", buttons disabled), so the instructor watches
what the trainees cannot see without touching their inputs. The token is in the URL fragment,
which the browser never sends to the server; the page keeps it for the tab, takes it off the
address bar, and sends it as the first message on `/ws/instructor`. With a fault token configured
the instructor view always needs it, loopback included (it shows what exam mode hides); with
none it is open on loopback and refused beyond it. A wrong token gets "INSTRUCTOR VIEW REFUSED"
and no frames. Instructor frames come from the same broadcaster as everyone's (PLAN 4.3), with
the active faults added; `GET /api/status` counts `instructors`.

The exam panel's header collapses and opens it on every page; the browser remembers the choice
per kind of page. The instructor page starts collapsed below 900 px wide, so the panel never
covers the fault labels that page is for.

The switches are read-only *in the instructor page* only. The token holder is the instructor, and
the same token still turns a switch or injects a fault through the API (`POST /api/exam`,
`/api/fault`), by design.

**A classroom binds beyond loopback with a token.** On loopback (`127.0.0.1`, the default) the
fault API is open, so every viewer's fault menu (and `GET /api/fault`) shows the active faults:
that is the single-user case, one person at one machine. For trainees on their own machines, run
`openfactory run SCENE --exam --bind 0.0.0.0 --fault-token <token>` (or `[server] fault_token`
in `openfactory.local.toml`): then the fault API, the exam switches and the instructor view all
need the token, and only the instructor has it. `--bind` opens every server (Modbus too), so do
it on a classroom network only.

## Recordings (PLAN 2.12)

A recording (`run --record FILE.ofrec`) contains the full scene file, comments included, so sharing
a recording shares the scene.

## `<scene>.CONTEXT.md`: generated context (PLAN 2.19)

`openfactory run` and `openfactory export` write `<scene>.CONTEXT.md` beside the scene whenever it
would change: one Markdown file an assistant (or a person) can read instead of the YAML and the
lockfiles. It lists every tag with kind, component and port, description, transform, range, and
its Modbus address in both analog encodings (from each lockfile, or as `run` would first write
it); every component after composite expansion with its parameters, ports and the fault kinds it
supports; the `checks:` block; and the commands to validate, lint, test, run, export and bundle
the scene, run from the folder that holds it (the stand-in script found as `bundle` finds it).
It depends only on the scene, its lockfiles and where its script is, so it is committed with
each shipped scene, and `tests/unit/test_context_md.py` fails when one is stale. Each demo kit
carries its own (paths relative to the kit; `build_kit.py --check` compares it byte for byte).
`test` and `validate` do not write it, and nothing is written for a `.ofz` input.

## Bundles (PLAN 2.18)

`openfactory bundle scenes/<name>.yaml` writes `<name>.ofz` in the current folder (`--out` to
choose): one zip holding the scene, every mesh it names with its `.license.txt` sidecar (a mesh
without one is refused), both Modbus lockfiles, the stand-in PLC script, and `manifest.json`
(format, bundle version, the scene's `openfactory:` version, and each file's role, size and
SHA-256). Composites are declared inside the scene, so they travel with it.

- **Lockfiles.** A lockfile on disk must match the scene; if a tag was added since it was
  written, bundling stops and asks for `openfactory export` first, so the bundle carries the
  addresses you committed. A lockfile not on disk is generated as `run` would write it, digital
  addresses seeded from the other encoding (ADR-0004).
- **Script.** `--script F.py`, else `<name>.py` beside the scene, else `examples/pyplc/<name>.py`
  in the nearest folder above it; `--no-script` carries none (a trainee's copy without the
  answer).
- **Reproducible.** Entries are sorted with fixed timestamps and permissions, so bundling the same
  files twice gives the same bytes.

`run`, `test` and `validate` take a `.ofz` in place of a scene file. The bundle is checked first:
every member listed in the manifest with a matching size and SHA-256, plain relative paths only
(no `..`, absolute paths, backslashes or links), at most 1000 members and 256 MB unpacked. It is
unpacked into a temporary folder removed when the command ends; a lockfile update during the run
goes to that copy, and the `.ofz` is never rewritten. **The script is Python and never runs on
its own:** `openfactory test x.ofz --bundled-script` (or `run ... --bundled-script`) runs it, and
only a bundle you trust should get that flag. Without it, `test` needs `--script` or `--plc` as
for a scene file.

## `mesh:`: a custom housing (PLAN 2.15)

Any component may draw its own model in the viewer in place of its fixed housing:

```yaml
  - id: CYL_01
    type: pusher
    ...
    mesh:
      file: models/pusher-housing.glb   # relative to this scene file's folder
      scale: 1.0                        # or [sx, sy, sz]; each > 0
      offset: [0, 0, 0]                 # meters, local frame, applied after scale and rotation
      rotation_deg: 0                   # extra turn about local y
```

- **Visual only.** The core, the viewer's physics colliders, the sensors, and the parity traces
  all use the component's declared dimensions. The mesh replaces only the fixed housing
  (the [component reference](components.md#conventions), "Custom mesh", lists it per type); moving and status-colored parts stay
  primitives, so ISA-101 status still shows. A scene mesh also replaces the type's default model
  (PLAN 4.2; the [component reference](components.md#conventions), "Default models").
- **`.glb` only** (binary glTF 2.0, one self-contained file). The file is in the component's local
  frame: the same origin and axes as the primitive (the [component reference](components.md) gives each
  type's origin), in meters.
- **Paths.** `file` is relative to the scene file's folder and must stay in it: an absolute path,
  a `..` segment, or a URL fails validation (`openfactory validate` exits 1). The viewer fetches
  the file from `/models/<component id>.glb`; the server serves only the files a scene declares,
  and only from the scene's folder.
- **Missing or broken file.** A file that is not there, leaves the scene's folder through a
  symlink, or is not a sound `.glb` gives one warning line at load, and the viewer draws the
  primitive. `openfactory lint` reports a file that leaves the scene's folder through a symlink
  as an error (E104), so the scene editor's preview names it and its Save refuses it (PLAN 4.1c
  fix 7). The scene's folder lies inside its project root (see "Library composites"), so a mesh
  is always inside the project. A replay (`openfactory replay`) always draws the primitives: a recording does not
  carry mesh files.
- **Size and license.** The 1 MB rule for binaries in git covers meshes, so meshes shipped in
  this repository stay small (the example, `tests/scenes/models/pusher-housing.glb`, made by
  `tools/make_example_mesh.py`, is under 7 kB); larger models belong in your own scene folder.
  Every mesh in the repository has a sidecar `<file>.license.txt` (`Author:`, `License:`,
  `Source:` lines) and a row in `THIRD_PARTY.md` (`tools/third_party.py --models` writes it).
  `openfactory lint` warns (W209) on a scene mesh with neither a sidecar nor a `THIRD_PARTY.md`
  entry naming it, in the mesh's folder or any folder above it up to the project root.
- OBJ or `.gltf` with separate buffers is not read; convert to `.glb` in a 3D tool first.

## `composites:`: new equipment from existing parts (PLAN 2.16)

A scene can declare its own component types from the built-in ones, with no Python:

```yaml
composites:
  - type: gated_conveyor
    description: Belt conveyor with a gate stopper near its discharge and a waiting-box eye
    params: {length: 2.0, gate_setback: 0.2}      # name: default (numbers only)
    parts:                                          # components without io; ids lowercase
      - {id: belt, type: belt_conveyor, description: Belt Conveyor, position: [0, 0, 0],
         length: "{length}", width: 0.5, speed_mps: 0.4}
      - {id: gate, type: stopper, description: Gate Stopper,
         position: ["{length} - {gate_setback}", 0, 0]}
      - {id: eye, type: photo_eye, description: Waiting Box Photo Eye,
         position: ["{length} - {gate_setback} - 0.2", 0.1, 0], beam_length: 0.6}
    io:
      run: {to: belt.run}                           # a command passed to a part
      open: {to: gate.raise, invert: true}          # ... inverted (DO only)
      eye: {from: eye.detect}                       # a DI or AI passed through
      waiting: {from: "gate.raised and eye.detect"} # a DI computed from parts' ports
components:
  - id: GC_01
    type: gated_conveyor
    description: Infeed Gated
    position: [0, 0, 0]
    length: 3.0                                     # a parameter
    io: {run: GC_01_RUN_DO, open: GC_01_OPEN_DO, eye: GC_01_EYE_DI, waiting: GC_01_WAIT_DI}
```

- **Expansion.** At load an instance becomes its parts, ordinary components named
  `<instance id>_<PART>` (`GC_01_BELT`, `GC_01_GATE`, `GC_01_EYE`), placed at the instance's
  position plus the part's `position` turned by the instance's `rotation_deg`, with the
  description `"<instance description> <part description>"`. Everything after loading (Modbus,
  the viewer, exports, faults, lint) sees those parts; `connections` and `checks` name them
  (`{from: GC_01_BELT, to: GC_02_BELT}`). The scene document marks each one with its instance,
  `composite: {id, type}`.
- **Tags** are the instance's own: its `io` bindings move to the parts' ports, so the I/O list
  and lockfiles show the tags you bound, with the part's id in the component column. A binding
  may be a mapping (`{tag, initial, invert, address, ...}`) as for any port.
- **Expressions** (`openfactory.core.expr`, never `eval`): numbers, parameters in braces in a
  part's fields (`"{length} - 0.2"`), `+ - * /`, parentheses, comparisons, and `and`/`or`/`not`;
  in an `io` `from`, `part.port` names a DI or AI port of a built-in part. No function calls.
  A computed DI is evaluated after the components each tick, like a binding transform
  (PLAN 2.17); the instance's binding may add `invert` and delays. The CSV transform column shows
  it, `expr(GC_01_GATE.raised and GC_01_EYE.detect)`.
- **Nesting.** A composite may be a part of another composite one level deep; deeper nesting and
  recursive definitions (`a -> b -> a`) are load errors naming the chain.
- **Required ports.** Every required port of every part must be exposed in `io` (a `to` for a
  command, a `from` for a DI/AI), or loading fails naming the part and port; an instance must
  bind every `io` entry that reaches a required port. Other `io` entries are optional.
- **Lint.** W208 warns on a composite whose `io:` is empty (no PLC can command or read it); W205
  checks the expanded conveyors for overlapping footprints like any others and names their
  instance.
- Example: `scenes/buffer-and-release.yaml` (one `gated_buffer` instance, `BUF`) and
  `tests/scenes/gated-conveyor.yaml` (two `gated_conveyor` instances with a computed DI).

### Logic, commands, connections and library composites (PLAN 2.5b)

A composite can carry its own control, join its own conveyors, and live in a shared file:

```yaml
    io:
      release: {command: DO}             # a PLC command only the logic reads
      zone4_eye: {from: z4_eye.detect}
    connections:                         # between the composite's own conveyor parts
      - {from: z3, to: z4}
    logic:                               # part DO port: boolean expression
      z3.run: "not (z3_eye.detect and z4_eye.detect)"
      z4.run: "release or not z4_eye.detect"
```

- **`logic:`** drives a built-in part's DO port from an expression over parts' DI/AI ports and
  the composite's `command` names (the grammar of a computed DI). It runs every tick before the
  parts step, with the values parts wrote at the end of the previous tick (each port's own
  output, faults included, before any binding transform): one tick of delay. A driven port has
  no tag, so no PLC sees or writes it, and it counts as wired for the required-port rule. A port
  driven by logic cannot also be exposed in `io` (a load error naming both). The scene's
  CONTEXT.md shows each driven port with its expression.
- **`{command: DO}`** is an `io` entry with no part port: the instance binds it to a tag the PLC
  writes, and only `logic:` reads it, by its name. An instance must bind it. The I/O list
  describes it as `"<instance description> — <name> command"`.
- **`connections:`** joins two of the composite's conveyor parts by part id; each instance adds
  `<ID>_<A> -> <ID>_<B>` to the scene's connections.
- **Library composites.** `composites: [{library: accumulation_conveyor}]` loads
  `_library/accumulation_conveyor.yaml` from the scene's folder or the nearest folder above it
  that has one, **no higher than the scene's project root**: the nearest folder at or above the
  scene that holds `openfactory.toml` or `.git`. A scene with no project root (for example one in
  a temporary folder) uses only the `_library/` beside it. So a `_library/` somewhere above your
  project is never picked up by accident. The scene editor's palette lists libraries from the
  same folders (PLAN 4.1c fix 5). The file holds `openfactory: "0.1"` and one `composite:` definition whose type is
  the file's name. A bundle (`openfactory bundle`) carries every library file the scene uses, as
  `_library/<name>.yaml` beside the scene.
- **The first library entry**, `scenes/_library/accumulation_conveyor.yaml`: a zero-pressure
  accumulation conveyor of four zones (parameters `zone_length` 1.0, `width` 0.5, `speed_mps`
  0.5, `eye_setback` 0.15, `eye_height` 0.05), each a belt with an eye near its discharge end.
  A zone holding a box stops while the zone ahead holds one, so boxes wait one per zone without
  touching; `release` (DO) runs the last zone, and each zone behind starts the tick after the
  zone ahead empties. It exposes `release` and `zone1_eye` to `zone4_eye` (DI). Example:
  `scenes/examples/accumulation-demo.yaml` with its script (not a shipped scene; a scene in
  `tests/scenes/` would not find `scenes/_library/`).
