# Scene editor: design proposal (PLAN 4.1)

*Proposal for ruling, 2026-11-10. No code. PLAN 4.1 asks for an in-browser scene editor: drag
components, edit properties with schema-driven forms, export YAML, place and edit composite types
(2.16) and assign meshes (2.15) from day one, with typed snap ports (`load`, `pipe`, `mount`) that
the editor snaps and the lint (2.20) checks. Decisions needed from the owner are listed at the
end; each section says what it proposes and what the alternative is.*

## 1. The smallest useful first version

An engineer who has a scene and wants to change it without hand-editing YAML:

1. `openfactory edit scenes/x.yaml` opens the scene in the viewer in **edit mode** (simulation
   paused, no parts, no PLC).
2. Click a component: its properties appear in a **form** built from the JSON Schema; change a
   value, see the plant redraw.
3. Move it on the floor (drag, or type the position), turn it about the vertical axis.
4. Add a component from a **palette** (every primitive type, and every composite type the scene
   or `scenes/_library/` defines), or delete one.
5. **Save**: the server checks the result (schema, then lint) and writes the file, changing only
   the lines the edits touched: every comment, key order and blank line elsewhere stays. **Download
   YAML** gives the same text without writing.

Not in the first version: editing a composite *type's* definition (its parts and logic), editing
`checks:`, multi-user editing, touch screens, undo across sessions.

## 2. Where the edits happen: on the server, through the 4.7 `Document`

**Proposed:** the browser never writes YAML. It keeps the edits as a list of small operations
(`set /components/3/speed_mps 0.6`, `move CONV_02 [1.2, 0, 0.5] 90`, `add photo_eye {...}`,
`delete PE_03`) and sends them to the server, which applies them to the file's text with the
`Document` from PLAN 4.7, extended with the operations an editor needs. Each is a splice at
positions PyYAML's composer reports, so nothing outside the edited span changes:

| Operation | Splice |
|---|---|
| `set_scalar(path, literal)` | exists (4.7): replaces one scalar |
| `set_node(path, literal)` | replaces any node's span, for a flow value such as `position: [1.2, 0, 0.5]` or `{tag: X_DI}` |
| `add_key(map_path, key, literal)` | inserts `key: literal` after the mapping's last key, at its siblings' indentation |
| `delete_key(path)` | removes the key's lines |
| `append_item(seq_path, block)` | appends a block item (a new component) at its siblings' indentation |
| `delete_item(seq_path, index)` | removes the item's lines **and the comment lines directly above it** (decision 4) |

Values are written by a tiny emitter: numbers as Python writes them, strings quoted only when
YAML needs it, vectors and small mappings in flow style like the shipped scenes. After applying
the operations the server loads the text (`load_scene`), lints it, and returns the findings and the
new `/api/scene` document; the viewer rebuilds from that, so the geometry the editor shows is
always the core's (CLAUDE.md: scene JSON is the only source of truth for geometry).

**Why server-side:** one implementation of validation and lint (Python), one YAML round-trip (the
tested `Document`), and the browser stays a renderer. **Alternative:** edit in the browser with the
npm `yaml` package (ISC; its `Document` API keeps comments) and validate with `ajv` (MIT). That
works offline, but duplicates schema validation in TypeScript, cannot run the lint, and adds two
dependencies. Recommended: server-side.

**Save** carries the hash of the text it was based on; the server refuses (409) if the file changed
on disk since, so an editor never overwrites a hand edit. Lockfiles (ADR-0004) are not touched by
the editor: a new tag gets its address on the next `run` or `export`, as today, and the save
response says so.

## 3. Forms from the JSON Schema

`web/public/scene.schema.json` (generated from `core/schema.py`, 73 definitions) already has what a
form needs: one `$defs` entry per component type (a `oneOf` under `components`, told apart by the
`type` constant), each property's `title`, `description`, `default`, `type`, `enum`/`const`,
`minimum`/`maximum`/`exclusiveMinimum`, `pattern`, `prefixItems` for 3-vectors, and `anyOf` with
`null` for optional values.

**Proposed:** a small form builder in TypeScript (`web/src/editor/forms.ts`) that maps each
construct to a control:

| Schema | Control |
|---|---|
| `string` (with `pattern`, e.g. ids) | text field, pattern checked as you type |
| `number` / `integer` with limits | number field with min/max, units from the description |
| `boolean` | checkbox |
| `enum` / `Literal` | select |
| `prefixItems` of 3 numbers (`position`) | three number fields (x, y, z) |
| `anyOf [X, null]` (`mesh`, optional values) | a "set" toggle, then X's control |
| nested object (`io` bindings, `mesh`) | a fieldset of its properties |
| anything else (rare unions) | a YAML text box for that one key, sent as a `set_node` literal |

The browser checks single fields from the schema's keywords as you type; the server's load and
lint after each change are the authority (no `ajv`). A vitest walks the shipped schema and fails if
any component property falls to the YAML fallback without being listed as expected, so a new
schema construct cannot silently get no form.

## 4. Snap ports and what the lint checks

**Proposed:** each component type declares typed ports, computed from its configuration in Python
and published in `/api/scene` (`snaps: [{name, kind, position, direction, width}]`), so the
viewer, the lint and the docs read one definition:

| Kind | Where | Snaps to |
|---|---|---|
| `load` | each end of a conveyor (belt, roller, turntable, lift deck), at belt-top height, facing along flow | another `load` port facing it: positions equal, headings opposite, tops level |
| `pipe` | a tank's nozzles, a pump's suction and discharge, a valve's and a pipe's ends | another `pipe` port; sets the link's `from`/`to` |
| `mount` | a conveyor's side rails (sensors, pushers, stoppers, diverters) | a device's mounting point: the device takes the rail's height and faces across the belt |

While dragging, the editor looks for a compatible port within 0.15 m (decision 7) and, on release,
snaps position and rotation so the ports coincide; for `load` it offers to add the `connections:`
entry, for `pipe` it fills `from`/`to`. A snapped pair draws a small marker; a near miss draws it
red.

New lint checks on the same port definitions (codes to be assigned in 2.20's list):
- two `load` ports within 2 cm, facing each other and level, with no connection (the snap-port form
  of today's W203, which it would replace);
- a `connections:` entry whose `load` ports are more than 1 cm apart or not level, or whose
  headings differ (a misaligned join);
- a `mount`-type device whose beam or plate does not reach across the belt it stands beside.

[The component reference](../components.md) gains each type's ports (the existing test that every registered type has a
section naming its parameters and ports covers it).

## 5. Where it lives in the viewer

- **Server:** `openfactory edit SCENE` loads the scene and serves the viewer with
  `/api/scene` reporting `mode: "edit"` (as a replay reports `replay`), the simulation not
  stepping, no protocol server, bound to loopback only (it refuses `--bind`), and three endpoints:
  `GET /api/edit/source`, `POST /api/edit/preview` (`{base, ops}` → findings and the scene
  document), `POST /api/edit/save` (`{base, ops}` → written, or 409).
- **Viewer:** `web/src/editor/` with `editmode.ts` (entered when the scene document says `edit`),
  `forms.ts`, `palette.ts`, `snap.ts` (pure snapping maths, unit-tested) and `ops.ts` (the
  operation list, undo and redo). Moving and turning uses three.js's `TransformControls`
  (already in the `three` package, MIT), limited to the floor plane and the vertical axis, on a
  0.05 m and 15° grid that Shift turns off. `main.ts` only calls `enterEditMode()` when told to;
  the run-time viewer is unchanged.

## 6. Tests

- **pytest** (`tests/unit/test_scene_edit.py`): every operation on every shipped scene, then its
  inverse, gives the original bytes; every comment outside a deleted span survives; the edited
  text loads to the expected scene; preview returns lint findings; save refuses a stale base.
- **vitest:** the form builder over the whole shipped schema (no unexpected fallback), the snap
  maths (ports coincide to 1 mm, headings to 0.1°), the operation list's undo and redo.
- **Playwright** (`tests/integration/test_editor.py`, the existing `tools/screenshot.py`
  helpers): `openfactory edit` on a temporary copy of a scene; select a conveyor by its screen
  position, change its speed in the form, drag a second conveyor near its discharge and see it
  snap and the connection offered, add a photo eye and a library composite from the palette,
  delete a component, save; then read the file: it loads, the lint has no new error, every
  original comment is there, and the diff touches only the edited lines. A screenshot of the edit
  mode is looked at, per CLAUDE.md.

## 7. Proposed tasks, each with a Done-when

- **4.1a Edit operations and the edit server.** The `Document` operations above, the value emitter,
  `openfactory edit` with preview and save (hash check, loopback only).
  *Done when:* pytest applies set, move, add, delete and their inverses to every shipped scene and
  gets the original bytes back with every comment kept; preview returns the lint's findings; a
  save against a changed file is refused.
- **4.1b Selection and the property form.** Edit mode in the viewer, click to select, the
  schema-driven form, live preview.
  *Done when:* vitest builds a form for every component type in the shipped schema with no
  unexpected fallback, and a Playwright test changes a conveyor's speed and saves: the file's diff
  is that one line.
- **4.1c Placement.** Palette (primitives and composite types, the scene's and the library's),
  drag and turn on the floor, delete, the `mesh` field, Download YAML.
  *Done when:* a Playwright test adds a photo eye and a library composite instance, moves and turns
  them, sets a mesh, saves; the file validates, the lint shows no new error, and the screenshot is
  looked at.
- **4.1d Snap ports.** Ports declared in Python and published, snapping in the editor, the new lint
  checks, the ports in [the component reference](../components.md).
  *Done when:* a Playwright test drags a conveyor near another's discharge and it snaps (ends within
  1 mm, headings equal), the connection is added, and the lint is clean; each new lint code has a
  test that fails without it.

## 8. Decisions needed from the owner

1. **Where edits are applied:** on the server through the 4.7 `Document` (recommended), or in the
   browser with the npm `yaml` package and `ajv`.
2. **How the editor starts:** a separate `openfactory edit SCENE`, simulation paused, loopback
   only, never with a PLC connected (recommended), or an edit mode inside `run`.
3. **Browser validation:** single-field checks from the schema plus the server as authority
   (recommended), or full schema validation in the browser with `ajv` (MIT, a new dependency).
4. **Comments on delete:** the comment lines directly above a deleted component go with it
   (recommended), or always stay.
5. **New components:** ids generated as the type's prefix and the next free number (`PE_03`), a
   description required before Save, and I/O tags proposed from the id and port (`PE_03_DI`) for
   the user to accept or rename. Or leave tags unbound until the user names them.
6. **Lockfiles:** the editor never writes them; new tags get addresses on the next `run` or
   `export` (ADR-0004), and Save says so. Confirm.
7. **Snap tolerances and the W203 replacement:** capture within 0.15 m; aligned means within 1 mm
   and 0.1° in the editor, 1 to 2 cm in the lint; the snap-port check replaces W203. Confirm or set
   other values.
8. **Composites in the first version:** placing instances of existing composite types only; editing
   a type's parts and logic is a later task. Confirm.
9. **Order:** 4.1a, b, c, d as listed, each its own commit and ruling, or 4.1d (snap ports and
   their lint) first, since the lint checks are useful without the editor.

---

# Pipe and mount snap ports: design proposal (PLAN 4.1g)

*Proposal for ruling, 2026-11-14. No code. PLAN 4.1g was split from 4.1d on 2026-11-12 with its
Done-when left to be set with the owner. It covers `pipe` ports (a tank's nozzles, a pump's
suction and discharge, a valve's and a pipe's ends; snapping fills a link's `from`/`to`), `mount`
ports (a conveyor's side rails: a sensor, pusher, stopper or diverter takes the rail's height and
faces across the belt; item (4) of 4.1h's awkward list, "there is no 'on that belt'"), and a lint
check that a mounted device's beam or plate reaches across its belt. The measurements below were
taken on the scenes as committed at c2fd395. The decisions are numbered at the end, each with a
recommendation.*

## 9. Pipe ports

### 9.1 What the scene says today

A link (`pipe`, `valve_onoff`, `valve_control`, `pump`) names its two ends in `from` and `to`: a
tank's nozzle (`TK_01.bottom`), a supply, drain or junction, or another link (both links then
name each other, and the implicit junction sits at the downstream link's origin). A tank has the
nozzles `bottom` (height 0) and `top` (its height) and any named in `nozzles: {name: height}`.
**A nozzle has only a height.** Where it is drawn is worked out each load
(`Tank.nozzle_point`): `top` at the centre of the top, any other on the wall *facing the link
that joins it* (toward the link's first `path` point, or the other end), a bottom nozzle 0.05 m up.

That has two consequences the format change must respect:

- **One nozzle can serve two links at different bearings.** In `plantpax-tank-level`,
  `TK_01.bottom` is the `from` of PIPE_02 (drawn at bearing 0 degrees, tank-local) and of the pump
  P_01 (drawn at 216.9 degrees). No single fixed position draws both as they are today.
- **Moving a nozzle changes the hydraulics.** A pipe's `length` defaults to its drawn length, and
  the length is in its Darcy-Weisbach resistance. None of the shipped pipes on a side or bottom
  nozzle sets `length` (PIPE_02 in `tank-level-pid` and `plantpax-tank-level`, PIPE_12 and PIPE_02
  in `two-tank-cascade`), so moving their nozzle's drawn point would change flows, levels and the
  `checks:` a PLC is graded on: a breaking change under [the versioning policy](../versioning.md). Valves and pumps
  have no length, so for them a moved nozzle is a drawing change only.

The bearings today's rule picks on the shipped scenes (degrees about +y in the tank's frame, the
same sense as `rotation_deg`, from local +x):

| Scene | Link end | Nozzle | Bearing today |
|---|---|---|---|
| `plantpax-tank-level` | PIPE_02 `from` | TK_01.bottom | 0 |
| `plantpax-tank-level` | P_01 `from` | TK_01.bottom | 216.9 |
| `tank-level-onoff` | P_01 `from` | TK_01.bottom | 0 |
| `tank-level-onoff` | HV_02 `from` | TK_02.bottom | 270 |
| `tank-level-pid` | PIPE_02 `from` | TK_01.bottom | 0 |
| `two-tank-cascade` | PIPE_12 `from` | TK_01.bottom | 0 |
| `two-tank-cascade` | PIPE_02 `from` | TK_02.bottom | 0 |

Every other nozzle end in a shipped scene is a `top`. No shipped scene, library composite or
example has a `nozzles:` key.

### 9.2 The format change: a nozzle may be given a bearing

**Proposed (decision 1):** a value in `nozzles:` is either a height, as today, or a mapping
`{height: m, bearing_deg: deg}`. A nozzle with a bearing is fixed: it is drawn, and its port
published, on the wall at that bearing and height (a cylinder at its radius; a box where the
bearing's ray meets the wall; at 0.05 m for a height of 0, as today). A nozzle given as a plain
height keeps today's rule. `bottom` may be listed in `nozzles:` to give it a bearing
(`bottom: {height: 0, bearing_deg: 90}`; its height must stay 0); `top` cannot be given one, it
stays the centre of the top. Two links may still share a nozzle; with a fixed bearing both are
drawn to the same point.

```yaml
- id: TK_01
  type: tank
  nozzles:
    bottom: {height: 0, bearing_deg: 180}   # fixed: drawn and snapped on the -x wall
    side: 1.2                               # height only: faces whatever joins it, as today
```

**Version and `migrate` (decision 2).** Under [the versioning policy](../versioning.md) this adds an optional form and
changes no existing meaning, so **the format stays `0.1`**, `MIGRATIONS` stays empty, and
`openfactory migrate` leaves every shipped scene byte for byte (all 12, the library composite
and the example scene; `migrate --check scenes` stays clean in CI). Nothing about any shipped
scene's drawing or hydraulics changes.

The alternative is to require a bearing on every side and bottom nozzle (a nozzle is then always
a fixed port). That changes meaning, so it is format **0.2**: `SCHEMA_VERSION = "0.2"`, a
`MIGRATIONS["0.1"]` entry, and every shipped scene copied as it is now into `tests/migrate/0.1/`
(the corpus `tests/unit/test_migrate.py` requires). What the migration would do to the shipped
scenes:

| Files | What `migrate` writes |
|---|---|
| `batch-mix-heat`, `buffer-and-release`, `conveyor-start-stop`, `inline-reject`, `palletizer-lite`, `sorting-by-color`, `sorting-by-height`, `two-lane-merge`, `_library/accumulation_conveyor`, `examples/accumulation-demo` | the `openfactory:` line only (`batch-mix-heat` uses only `top`) |
| `tank-level-pid`, `two-tank-cascade` | the version line, and `nozzles: {bottom: {height: 0, bearing_deg: 0}}` on each tank whose bottom is used: today's drawn points, so no pipe length changes |
| `tank-level-onoff` | the version line; TK_01 bottom at bearing 0, TK_02 bottom at 270 |
| `plantpax-tank-level` | the version line; TK_01 bottom at 0 for PIPE_02, and either P_01 moved to a new nozzle `pump_suction: {height: 0, bearing_deg: 216.9}` with its `from` rewritten, or P_01 drawn to bearing 0 (a drawing change only: a pump has no length) |

A migration may pin `length:` instead, on every pipe whose nozzle moves, to keep its hydraulics;
with bearings taken from today's drawing (above) no pipe moves, so none needs it.

**Recommended:** the additive form, format 0.1, no migration. Fixing a bearing is something the
editor's snap does when you join a pipe to a tank wall (9.4), not something every old scene must
be rewritten for.

The form: since 4.1j (built 2026-11-14, after this proposal was written) `Tank.nozzles` has a
mapping control, a number per nozzle. The value `height | {height, bearing_deg}` would make each
entry a union, which that control does not yet take: 4.1g2 adds it (a number, or a two-field
group, per entry), or `nozzles` falls back to a line of YAML again.

### 9.3 The ports

Published in `/api/scene` as `snaps` on each component, beside 4.1d's `load` ports (recordings
still leave them out). A `SnapPort` keeps its fields (`name`, `kind`, `position`, `heading_deg`,
`width`, `role`); `width` is the pipe's or nozzle's bore for a `pipe` port (0.05 m unless the
link says), and a free nozzle (no bearing) is published with `heading_deg: null` and a `radius`, a
ring round the tank at its height.

| Type | Ports | Role |
|---|---|---|
| `tank` | one per nozzle, named as the nozzle: `top` facing up at the top centre; a fixed nozzle on the wall facing out; a free nozzle as a ring | `both` |
| `supply` | `outlet` at the origin | `out` |
| `drain` | `inlet` at the origin | `in` |
| `junction` | `node` at the origin | `both` |
| `pipe`, `valve_onoff`, `valve_control` | `inlet` and `outlet`, both at the origin (where an implicit junction with it sits) | `in`, `out` |
| `pump` | `suction` and `discharge`, both at the origin | `in`, `out` |

A link's body ports sit at its origin because that is where the core joins links today
(`_process.end_point`); the drawn runs bend to wherever the ends are, and nothing is moved to
make a port line up. The roles keep the two ports at one point apart: a dragged `to` end only
looks for `in` or `both` ports, a dragged `from` end only `out` or `both`.

### 9.4 Snapping in the editor

A selected link shows a handle at each drawn end (its `points[0]` and last point). Drag a handle
and release it within 0.15 m (decision 7 of 2026-11-10) of a port of the right role, and:

- on a tank nozzle, a supply, drain or junction: the end is set (`from: TK_01.bottom`);
- on a free nozzle's ring: the end is set to the nozzle, and nothing else is written (the nozzle
  keeps facing its links);
- on another link's port: both ends are set, as the core requires (pipe `to: P_01` and pump
  `from: PIPE_03`);
- anywhere else: the end goes back. A link end must name something; there is no free end in the
  format.

The end it left keeps naming its old partner. Where that partner was a link that named this one
back (link to link), the partner's end now names a link that does not name it: the preview's load
error says so, and the panel lists it under "Left for you to fix", the 4.1f rule for references a
component cannot work without. Moving a valve, pump or junction body is the Move gizmo as today;
its runs follow. A link's `path` points are not edited by snapping (YAML field, as today).

**No pipe lint check** is proposed: link ends are references, and the loader already rejects an
unknown end or nozzle (E100). The format change adds one load error: `bearing_deg` on `top`, or a
`bottom` whose height is not 0.

## 10. Mount ports

### 10.1 The rails

A `belt_conveyor` or `roller_conveyor` publishes two `mount` ports, its side rails seen looking
along the flow: `rail_right` along local z = +width/2 and `rail_left` along local z = −width/2,
each at belt-top height, from the infeed end for `length` along the flow, facing in across the
belt. `SnapPort` gains a `length` (0 for every other port) for the rail's run.

**Turntables and lifts get no rails in the first version (decision 5).** A turntable's deck turns
under anything mounted beside it and a lift's deck rises away from it; the shipped devices on
decks (two-lane-merge's PE_TTA and PE_TTB, demo-turntable's PE_TT, demo-lift's PE_LIFT) are placed
for one deck position and are deliberately not across the deck at others (10.3).

### 10.2 What a device does when dropped on a rail

While a device is moved, a rail whose line comes within 0.15 m of the device's mounting point (in
the floor plane, between the rail's ends) is marked, as 4.1d marks a `load` port. On release the
device is placed by the rule for its type and the move writes only `position` and `rotation_deg`
(and, for a diverter, `swing_deg`'s sign). Along the rail it stays where it was dropped, on the
0.05 m grid from the infeed end (Shift for free), clamped to the rail.

| Type | Mounting point | Placed |
|---|---|---|
| `photo_eye` | beam midpoint | over the belt's centre line, the beam across the belt (`rotation_deg` the belt's); its height above the belt kept if it is 0.02 to 1 m, else 0.1 m (the height of every shipped eye but the two height sorters' 0.3 m) |
| `distance_sensor` (axis `+z`) | sensor face | 0.05 m outside the rail, facing across; height kept as a photo eye's |
| `pusher` | retracted plate face | 0.05 m outside the rail, extending across (`rotation_deg` the belt's on `rail_left`, plus 180 on `rail_right`); plate bottom 0.05 m above the belt: y = belt top + 0.05 + h/2 with `plate_height` h set, or y = belt top + (0.05 + 0.75 × tallest part) / 2 with it unset, so the loader's sizing gives the same plate. Both shipped pushers (`sorting-by-height`, `inline-reject`: y 0.175, z −0.3, retracted face 0.05 m outside a 0.5 m belt's left rail, 0.4 m tallest box) come out at their committed position. |
| `stopper` | blade centre | on the centre line at belt-top height, `rotation_deg` the belt's (stops parts moving with the flow) |
| `diverter_arm` | pivot | on the rail at belt-top height, at rest along the flow (`rotation_deg` the belt's), `swing_deg` signed to swing over the belt: positive on `rail_left`, negative on `rail_right` (its sign is flipped if it points off the belt; decision 6) |
| `color_sensor`, `distance_sensor` (axis `−y`) | sensor face | over the belt's centre line, looking down; height above the belt kept (`sorting-by-color`'s CS_01 at 0.6 m) |

**Nothing about the mount is stored in the scene (decision 7).** The device's position and
heading are its usual keys; "on CONV_02" is not recorded, so the format does not change. Moving a
belt does not move the devices on it in 4.1g; that is noted as later work.

### 10.3 The lint check: W214

**W214** (warning; codes are not reused, so not W203): a device standing on a belt or roller
conveyor (the highest belt whose footprint, widened by 0.1 m across the flow as W206 widens it,
holds the device's mounting point, with the belt top 0 to 1 m below that point; `sizing.belt_under`
alone will not do, since it wants the point within 1 cm of the belt top and an eye's beam is 0.1 to
0.3 m above it; composites expanded, a part named with its instance as W205 does) whose working
reach does not cross the belt:

- a `photo_eye` whose beam, projected across the belt, does not reach both rails;
- a `stopper` whose blade (`width`, or the belt's width when unset) does not reach both rails;
- a `diverter_arm` whose tip at `swing_deg` does not reach the far rail from its pivot.

**Tolerance: 1 mm** short of a rail (the editor's alignment, decision 7). Each finding says how
far short it is and which rail. Pushers are not in W214: W207 already checks that a stroke
reaches the far edge (or the belt beyond), from the same `push_path`. Turntable and lift decks are
not checked (10.1).

**Shipped scenes that would raise it today:** none. Measured on 33 scenes (the 12 shipped, the
example, and the test scenes in `tests/scenes/`, composites expanded): 44 photo eyes, stoppers
and diverters on belt or roller conveyors, every eye's beam 0.6 m on a 0.5 m belt except `sorting-by-color`'s PE_RED, whose 0.5 m beam equals its
0.5 m belt and passes on the boundary; every stopper's blade unset (the belt's width); both
diverters' tips at z 0.302 past the far rail at 0.25. **If decks were checked**, two shipped eyes
would raise it: `two-lane-merge`'s PE_TTA (a 0.4 m beam on the 0.5 m deck, placed to clear a
box at 90 degrees) and PE_TTB (along the deck at rest, across it only at 90 degrees); both are
deliberate and commented in the scene, which is why decks are left out.

## 11. Proposed tasks, each with a Done-when

- **4.1g1 Mount ports and W214.** The rails published, the drop rules in 10.2 in the editor,
  W214 in the lint, [the component reference](../components.md) and [the editor guide](../editor.md).
  *Done when:* a Playwright test drops a photo eye, a pusher, a stopper and a diverter beside a
  belt in a copy of the sorter and each lands by 10.2's rule (positions within 1 mm, headings
  within 0.1 degrees, the diverter's swing over the belt); the preview has no W214, W206 or W207;
  a unit test places both shipped pushers by the formula and gets their committed positions; each
  W214 case (beam short by 2 mm, stopper narrower than the belt, diverter swinging off the belt, a
  turntable eye not flagged) has a test that fails without the check; the 33 scenes have no
  W214; a screenshot of a marked rail is looked at.
- **4.1g2 Nozzle bearings and pipe ports.** The nozzle form of 9.2, the ports of 9.3 published,
  the drawing using a fixed bearing.
  *Done when:* every shipped scene loads, draws and runs exactly as before (its state frames and
  `checks:` results equal before and after over a 60 s run; `migrate --check` clean); a tank with
  `bottom: {height: 0, bearing_deg: 90}` draws its pipe end on the +90 degree wall within 1 mm and
  the pipe's default length follows; the ports of each process type are in `/api/scene` and not
  in a recording; [the scene format](../scene-format.md) and [the component reference](../components.md) describe both.
- **4.1g3 Pipe snapping in the editor.** The end handles and snapping of 9.4.
  *Done when:* a Playwright test, on a copy of `tank-level-pid`, drags PIPE_02's `from` handle
  from TK_01.bottom to a new nozzle declared with a bearing and its `to` handle onto a new drain,
  and joins a new valve between two pipes (both ends written), saves, and the file loads with
  exactly those `from`/`to` changes; a drop on nothing writes nothing; a re-snap that leaves a
  link partner dangling is listed under "Left for you to fix" before Save; screenshots looked at.

## 12. Decisions needed from the owner

*Ruled by the owner, 2026-11-14 (recorded in PLAN under 4.1g):* 1 to 5, 7 and 8 as recommended;
6 as written, **amended**: the drop rules are implemented in Python beside the snap ports
(`src/openfactory/core/mounts.py`), as a pure function of the device type, its parameters, the
conveyor, the rail and the distance along it, giving `position`, `rotation_deg` and a diverter's
swing sign, unit tested there; the editor chooses only the rail and the distance and asks the
server for the placement, because Milestone 5 places devices from a drawing takeoff with the same
rules and there must be one copy. On 7, a stored `mounted_on` is looked at again in Milestone 5.
9: 4.1g1, then 4.1g2; 4.1g3 (pipe snapping in the editor) is parked until after Milestone 5's
Phase B.


1. **Nozzle positions in the format:** a nozzle value may be a height or
   `{height, bearing_deg}`, and `bottom` may be listed to fix its bearing (recommended); or a
   separate optional key, `nozzle_bearings: {name: deg}`, which keeps `nozzles` a mapping of
   numbers.
2. **Version:** additive, format stays 0.1, `migrate` changes nothing on every shipped file
   (recommended); or bearings required, format 0.2, the migration in 9.2's table with its
   `tests/migrate/0.1/` corpus, including the choice for `plantpax-tank-level`'s shared
   TK_01.bottom (a new `pump_suction` nozzle for P_01, recommended if 0.2 is chosen).
3. **Link ports at the origin:** a link's inlet/outlet (a pump's suction/discharge) at its origin,
   told apart by role, where the core already joins links (recommended); or ports offset along
   the body's heading, which would also change where links are drawn.
4. **A re-snap that strands a link partner:** listed under "Left for you to fix", the 4.1f rule
   (recommended); or the snap refused until the partner is changed first.
5. **Rails on belt and roller conveyors only**, not turntable or lift decks, in the first version
   and in W214 (recommended; checking decks would flag two-lane-merge's PE_TTA and PE_TTB, which
   are deliberate); or decks too, checked at rest and turned with either state passing.
6. **Drop rules:** as 10.2's table, with the 0.05 m offsets that reproduce the shipped pushers, and
   the editor flipping a diverter's `swing_deg` sign so it swings over the belt (recommended); or
   position and heading only, leaving the sign to the user and W214 to report it.
7. **The mount is not stored:** no new key, the format unchanged, belts' devices not following a
   moved belt in 4.1g (recommended); or a `mounted_on: CONV_02` key (optional, format 0.1) so the
   editor can move them with it.
8. **W214:** a warning, 1 mm tolerance, photo eyes, stoppers and diverters only (pushers stay
   W207's), no shipped scene raising it (recommended); or a wider tolerance (W213's 1 cm).
9. **Split and order:** 4.1g1 (mount, the 4.1h item, no format question) first, then 4.1g2 and
   4.1g3, each its own commit and ruling (recommended); or one task.
