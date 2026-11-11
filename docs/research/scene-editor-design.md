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

`docs/components.md` gains each type's ports (the existing test that every registered type has a
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
  checks, the ports in `docs/components.md`.
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
