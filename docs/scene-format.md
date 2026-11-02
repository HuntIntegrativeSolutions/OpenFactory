# Scene format reference

The full scene format is specified in `docs/PLAN.md` §4, and the machine-readable form is
`web/public/scene.schema.json` (generated from `src/openfactory/core/schema.py`). Components and
their parameters are in `docs/components.md`. This page covers the scene-level blocks that need
more explanation than §4 gives.

OpenFactory is a training and demo tool, not a safety-rated simulation.

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

Two failure rules close the easy passes:

- **A stopped line fails `parts_reached`.** "At least one part reached it" is part of the check, so
  a program that never starts the belts, or a plant that delivers nothing, does not pass merely
  because nothing went the wrong way.
- **A jammed line fails `max_cycle_s`.** The limit applies to the parts still in the scene as well
  as to the parts removed, so a line where nothing is ever removed fails on the parts piling up
  behind the jam instead of passing with no removals to measure.

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
  (`docs/components.md`, "Custom mesh", lists it per type); moving and status-colored parts stay
  primitives, so ISA-101 status still shows.
- **`.glb` only** (binary glTF 2.0, one self-contained file). The file is in the component's local
  frame: the same origin and axes as the primitive (`docs/components.md` gives each type's
  origin), in meters.
- **Paths.** `file` is relative to the scene file's folder and must stay in it: an absolute path,
  a `..` segment, or a URL fails validation (`openfactory validate` exits 1). The viewer fetches
  the file from `/models/<component id>.glb`; the server serves only the files a scene declares,
  and only from the scene's folder.
- **Missing or broken file.** A file that is not there, leaves the scene's folder through a
  symlink, or is not a sound `.glb` gives one warning line at load, and the viewer draws the
  primitive. A replay (`openfactory replay`) always draws the primitives: a recording does not
  carry mesh files.
- **Size and license.** The 1 MB rule for binaries in git covers meshes, so meshes shipped in
  this repository stay small (the example, `tests/scenes/models/pusher-housing.glb`, made by
  `tools/make_example_mesh.py`, is under 7 kB); larger models belong in your own scene folder.
  Every mesh in the repository has a sidecar `<file>.license.txt` (`Author:`, `License:`,
  `Source:` lines) and a row in `THIRD_PARTY.md` (`tools/third_party.py --models` writes it).
  `openfactory lint` warns (W209) on a scene mesh with neither a sidecar nor a `THIRD_PARTY.md`
  entry naming it, in the mesh's folder or any folder above.
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
