# Plants from drawings: design proposal (PLAN 5.1 to 5.4)

*Proposal for ruling, 2026-11-15. No code. This note covers Milestone 5, Phase B: the plant's
own tag names (5.3), generic equipment (5.2), the takeoff file (5.1), and plants in several files
(5.4), in that order. Each section says what exists today, what it proposes, and what the
alternatives are. Proposed tasks and their Done-whens are in section 5. Decisions for the owner
are in section 6, numbered, each with a recommendation. The worked example is a small plant of
our own invention. No customer and no product is named, and the drawings and scenes made from
a customer's drawings stay out of this repository (Milestone 5, rule 1).*

Three facts hold for every proposal below.

- **No shipped scene changes.** Every proposal adds an optional key, a new component type or a
  new command. Under [the versioning policy](../versioning.md) that does not change the scene
  format version (`0.1`): "Adding optional keys or component types does not change it." It is a
  minor package release. No scene, library composite or lockfile in `scenes/` is edited by any
  task here. Three CI gates prove it: PLAN §9 rule 13 (each scene's run, held by its kit
  recording or by `tests/references/`), `tools/build_kit.py --check` (every kit file,
  exports included), and `migrate --check` with the byte-for-byte test in
  `tests/unit/test_nozzles.py`.
- **Generated files of existing scenes stay byte-identical.** That covers the I/O CSV, every
  `CONTEXT.md`, the L5X stub, the SCADA tag export, the 3.4 and 3.8 exports and the kit READMEs.
  A new column or section appears only in a scene that uses the new key.
- **Determinism (PLAN §3.2) holds.** All new state is stepped with `dt`, and nothing reads the
  wall clock.

---

## 1. The plant's tag names (5.3)

### 1.1 What exists today

- A binding's tag must match `^[A-Z][A-Z0-9_]*_(DI|DO|AI|AO)$` and be at most 40 characters
  (`core/schema.py`, `_tag_name`). Any other name is a load error.
- The tag name is the I/O table's key. These all use it:
  - the stand-in scripts (`io["CONV_01_RUN_DO"]`), the checks and the viewer;
  - the Modbus server, whose table follows from the suffix (`protocols/modbus.py`: `kind =
    tag_name.rsplit("_", 1)[-1]`) and whose lockfile maps the name to an address (ADR-0004);
  - the OPC UA server, with nodes `s=<tag>`, and the Sparkplug publisher.
- §6.2 says the controller's tag name equals the scene's tag name by default, with an optional
  `plc_tag:` per binding. That key is not built (2.2). `protocols/logix.py` reads and writes the
  scene's names. It builds the driver with `init_program_tags=False`, so it sees controller-scope
  tags only. Its connect-time type check looks each name up in `driver.tags`, which holds base
  tags only.
- Every export uses the scene's names:
  - the I/O CSV writes one row per bound tag;
  - the L5X stub writes one controller tag per CSV row. `--prefix AREA1` prepends `AREA1_`, and
    each name is checked against `^[A-Z][A-Z0-9_]{0,39}$`;
  - the SCADA tag export (2.4) writes one UDT instance per component in one area folder. Each
    member's item path takes the tag name from an instance parameter `Tag_<Member>`;
  - `CONTEXT.md` lists the scene's tags.

### 1.2 Proposal: the plant's name beside the scene's

A binding in mapping form may carry `plc_tag:`, the name of the point in the plant's own
controller program:

```yaml
io:
  pv: {tag: LT_101_PV_AI, plc_tag: "LT101_PV"}                     # controller scope
  run: {tag: P_101_RUN_DO, plc_tag: "Program:Transfer.P101_Run"}   # program scope
  running: {tag: CV_201_RUN_DI, plc_tag: "CV201.Running"}          # a structure member
  detect: {tag: PE_201_DI, plc_tag: "Line2_DI_Word.4"}             # a bit of an integer
```

**The scene's tag stays the key, and keeps its load rule.** `tag:` keeps the HIS pattern as a
load error, because everything inside OpenFactory keys on it:

- the I/O table, the scripts, the checks and the viewer;
- the Modbus table, chosen by the suffix, and the lockfile;
- the OPC UA node names and the Sparkplug metrics.

`plc_tag:` is used where OpenFactory faces the plant's controller and the plant's documents:

| Consumer | Name it uses |
|---|---|
| I/O table, scripts, checks, viewer, recordings | the scene tag (unchanged) |
| Modbus server and lockfile | the scene tag: **the lockfiles cannot move**, since no key in them changes |
| OPC UA server, Sparkplug publisher | the scene tag: they are OpenFactory's own servers |
| Logix path (2.2) | `plc_tag` when given, else the scene tag (§6.2 as written) |
| I/O CSV | a `plc_tag` column at the end, written **only** when some binding in the scene has one |
| `CONTEXT.md` | the same: a "plant tag" column only when some binding has one |
| L5X stub | see below |
| SCADA tag export | with a device path (a PLC driver in the SCADA), `Tag_<Member>` is the `plc_tag`; with OpenFactory's own OPC UA server as the source, the scene tag, because that is the node name |
| 3.4 and 3.8 exports | unchanged: they write a demo program of OpenFactory's own around the scene's tags, not the plant's program |

**The L5X stub, by the form of the `plc_tag`.** The stub declares tags for a program that does
not exist yet. A plant tag already exists in the plant's program. So:

- **A controller-scope name** (`LT101_PV`): one `Base` tag of that name, as today.
- **A program-scope name** (`Program:Transfer.P101_Run`): one tag in that program's `<Tags>`,
  under `<Programs><Program Name="Transfer" Use="Context">`.
- **A structure member, an array element or a bit** (`CV201.Running`, `Word[3]`,
  `Line2_DI_Word.4`): **not declared**. It is part of a tag the plant's program already
  declares, with a type the stub cannot know. The stub lists each one in an XML comment at the
  top of the file, and `export` prints a count on standard error: "4 plant tags are members,
  elements or bits of the plant's own tags and are not declared here".

The stub's name check becomes the `plc_tag` grammar below for these rows. `--prefix` applies
only to rows with no `plc_tag`, because a plant name is already the plant's full name.

**The `plc_tag` grammar, checked at load.** It accepts an optional `Program:<name>.`, then a
base name, then any number of `.member` or `[i]` / `[i,j]` / `[i,j,k]`, then an optional final
`.<bit>` (0 to 63). Each name is a letter or `_` followed by letters, digits or `_`, at most 40
characters. This is the syntax pycomm3's `_parse_tag_request` splits on (section 1.4). The
controller's own name rules are to be verified against the vendor's published manual before
the grammar is final: whether a name may start with `_`, and whether doubled or trailing
underscores are allowed. Two bindings with the same `plc_tag` are a load error that names both
bindings. Case is kept as written. If the controller's names are not case-sensitive (to be
verified with the other name rules), two `plc_tag`s that differ only in case are also a
duplicate.

**An area per scene.** `scene.area: WASH` (optional, capitals and digits, letter first) is
the default for the L5X stub's `--prefix` and the SCADA export's `--area`. It never changes a
`plc_tag` and never changes a scene tag. Without it, both exports behave as today.

**The heartbeat.** `SIM_HEARTBEAT_DI` is not a binding. `plc.heartbeat_plc_tag:` (optional)
names it in the plant's program; without it, the heartbeat keeps its name.

### 1.3 What the lint becomes

The draft Done-when says "lint flags a name outside the HIS pattern as a warning, not an
error". With the scene tag kept strict, only a `plc_tag` can be outside the pattern, and the
plant's own names usually are. This note reads the Done-when that way, as decision 2 asks the
owner to confirm.

A warning per such tag would bury real findings: a 1,000-tag plant (5.12) would print 1,000
lines, and CLAUDE.md asks every warning in a shipped scene to be fixed or explained.
Recommended:

- **No warning for a well-formed `plc_tag`.** A malformed one is a load error, as is a
  duplicate.
- **W215, a half-mapped scene.** When some bindings have a `plc_tag` and others do not, each
  one without it is flagged: "`P_101_RUN_DI` has no plc_tag while 14 other bindings have one;
  the Logix path will look for `P_101_RUN_DI` in the controller". This catches the row a takeoff
  missed, which is the error that matters when building from drawings.

The alternative is one summary line: "W215: 37 plc_tag names are outside the HIS form; they are
the plant's own names". It is shown as decision 2(b).

### 1.4 How the Logix path reads and writes by that name

These facts come from pycomm3 1.2.16, `pycomm3/logix_driver.py`
(<https://github.com/ottowayi/pycomm3>, documentation <https://pycomm3.readthedocs.io/>), read
in its source:

- **Names.** `LogixDriver.read(*tags)` and `write(*tags_values)` take tag names as strings.
  `_parse_tag_request` splits a name on `.`. A base that starts with `Program:` takes the next
  segment with it (`Program:Transfer.P101_Run` is one base tag). A last segment made only of
  digits is a bit number (`Word.4`). A `{n}` suffix asks for n elements. Members are looked up
  through the uploaded definition (`_get_tag_info`, which descends
  `data_type["internal_tags"]`).
- **Definitions are needed.** The constructor's docstring: "Initialization of tags is required
  for the read and write to work." `init_tags=True` uploads controller-scope definitions.
  `init_program_tags=True`, the default, uploads every program's (`get_tag_list(program="*")`).
  The client passes `False` today.
- **One program at a time replaces the cache.** `get_tag_list(program="X")` with the default
  `cache=True` replaces the whole cached tag dictionary with X's tags. So the client cannot add
  one program's tags to the controller's without reaching into a private attribute. Proposed:
  pass `init_program_tags=True` when any `plc_tag` starts with `Program:`, and `False`
  otherwise, so scenes without one behave exactly as today.
- **Program-scoped names in the list.** `get_tag_list`'s docstring: "For program scoped tags
  the tag['tag_name'] will be `'Program:{program}.{tag_name}'`. This is so the tag list can be
  fed directly into the read function."
- **The type check needs its own split.** `get_tag_info(name)` joins `Program:` with the next
  segment but does **not** strip a trailing bit. On an atomic integer, `get_tag_info("Word.4")`
  tries to descend into members of a non-structure, which ends in its generic `except` and a
  `RequestError`. The client's connect-time check must split a trailing digit segment off
  itself, look up the rest, and accept:
  - for `bool`: `BOOL`, or a bit of `SINT`, `INT`, `DINT` or `LINT`;
  - for `float32`: `REAL`;
  - for `int16`: `INT`.

  A structure's `BOOL` members come back from the definition as bit members (`'bit' in info`,
  `_parse_template_data`). A wrong or missing type stays "missing" or
  "mismatched", logged once and left out of the batches, as today.
- **Batching is unchanged.** One `write()` of the changed DI/AI points by their plant names,
  then one `read()` of the DO/AO points. Results come back in request order, as
  `Tag(tag=<the requested name>, ...)`. The client keeps a plant-name to scene-tag map and puts
  values into the I/O table under the scene tag. A bit write is built by pycomm3 as a bit
  request (`request.set_bit` in `_write_build_requests`).

Only the bench PLC can show these (2.2's open items):

- how long the upload takes with program tags on;
- that a member of a plant's structure accepts an external write (its external-access
  setting);
- what a bit write does to a word another writer also updates;
- the cost of many member reads against the 50 ms cycle.

### 1.5 Alternatives

- **(a) The plant's name instead of the scene's.** `tag:` would take any name, and the kind
  would come from the port instead of the suffix. This is one name per point and nothing to
  keep in step. Rejected as the default because it touches everything that keys on the tag:
  - the Modbus table choice by suffix;
  - every script and check;
  - the OPC UA node names, through dots and `Program:` in node ids;
  - the lockfiles' YAML keys.

  Existing scenes would still load. But every consumer would change behaviour for new ones,
  and a stand-in script written against `io["Program:Transfer.P101_Run"]` is hard to read.
- **(b) A separate tag map file** (`<scene>.plant-tags.yaml`: scene tag to plant tag, beside
  the lockfiles). This keeps the scene untouched and could be made straight from an I/O list.
  Rejected as the default because the editor and the lint would have to read a second file to
  show or check a binding, and a renamed scene tag would leave the map stale. `takeoff
  scaffold` already writes `plc_tag` from the takeoff's I/O rows (section 3), which is where
  the I/O list goes in.
- **(c) A name template** (`plc.tag_template: "Program:{area}.{id}_{port}"`). It suits a plant
  whose program follows one pattern; few do. It could come later as a scaffold option without
  a format change.

### 1.6 Format change and shipped scenes

- Additive: `plc_tag` on every binding mapping form (DI, DO, AI, AO), `scene.area` and
  `plc.heartbeat_plc_tag`.
- The JSON Schema gains the keys. The format stays `0.1`.
- No shipped scene uses them, so none is touched, and every generated file of every shipped
  scene stays as it is (the CSV column and the `CONTEXT.md` column appear only when used).

---

## 2. Generic equipment (5.2)

### 2.1 What exists today

Two probes on a scratch scene, 2026-11-15:

- **The commands work.** A composite whose only part is a `pilot_light` loads. It has a
  `start: {command: DO}` io and `logic: {lamp.on: "start"}`. Its command exports with the part
  as its component (`MX_01_START_DO`, component `MX_01_LAMP`).
- **The feedback cannot read the command.** Adding `running: {from: "start"}` is a load error:
  "composite mixer: io.running: 'start' is not a part.port reference". An io expression may name
  only parts' DI and AI ports. Only `logic:` reads command names.

So today:

- **Start delay:** exists, if the feedback is computed from a part's DI. The instance binding's
  `on_delay_s` (2.17) delays a computed DI.
- **Running follows a command:** only by abusing a part that has a running output, such as a
  belt that drives nothing.
- **Permissive:** only over a part's DI, for example `hoa.pos_a`.
- **No computed AI:** a computed io is a DI.
- **No first-order lag**, and no other state, in a composite. Composites are expanded at load
  into built-in parts and keep no state of their own.
- **No geometry of the instance's own:** no `mesh` key on an instance. It is drawn as its parts,
  and there is no part that is only a body.

### 2.2 Proposal: five small additions

Each addition is sorted by PLAN §5's rule into one of three kinds: an addition to composites,
an addition to the binding transforms (2.17), or a new primitive.

| Behaviour on the drawing | Addition | Kind | Why that kind |
|---|---|---|---|
| Running feedback follows the command | An io `from:` expression may name the composite's own `command` io, read as the tag's value that tick | Composite | Pure expansion: the expression is evaluated where computed DIs already are (the transform stage, after the components) |
| Start delay | Nothing new: the instance binding's `on_delay_s` (and `off_delay_s` for a run-down) | Exists (2.17) | Already a stateful transform stepped with `dt` |
| Permissive | Nothing new beyond the first row: `running: {from: "start and permit"}`, where `permit` is a part's DI (a selector) or a second `command` | Composite | As the first row |
| An analog feedback: speed, a computed value | `command` io may be `AO`; an io `from:` with `kind: AI` takes a numeric expression (the grammar already has `+ - * /`), with the port's `range` and `units` | Composite | The expression is typed at load, as computed DIs are (`composite._probe`) |
| First-order lag on an analog | `lag_s` on any AI binding: `{tag: X_AI, lag_s: 5}` | Binding transform | It is the analog sibling of `on_delay_s`, and it is state stepped with `dt` in `TransformStage`, where the delays already live. It has no geometry and no physics, so it is not equipment, and a primitive for it would be a component with no body |
| The item's own body: a box, or a mesh | The `scenery` primitive of 5.11, brought forward: `size`, `color`, optional `mesh` (2.15), no I/O, no physics | Primitive | A composite has no geometry of its own: expansion replaces an instance with built-in parts, and the viewer draws only built-in types. A body is a part, and no built-in type is only a body |

The lag uses the same discretization as `transmitter` and `valve_control` (forward Euler,
`x += (u - x) * min(dt / lag_s, 1)`). The first value is taken as it is, as `transmitter`
does, so a lag never ramps from zero at start-up. A composite whose only part is a `scenery`
body must own its command and computed io. Today a command's owner is a part id. The build
confirms that a part with no ports may own them; this is the one rule to check.

**The example: two library composites any drawing item can use.** A `command` io must be
bound on every instance (2.5b), so the speed is a second type rather than an unbound port.
`driven_item` has a start command, and a running feedback that needs the hand-off-auto selector
in auto (the permissive). `variable_item` adds a speed command and a speed feedback that
follows it while running:

```yaml
openfactory: "0.1"
composite:
  type: driven_item
  description: >-
    Any driven item from a drawing that has no component of its own: a box (or a mesh) with a
    start command and a running feedback, permitted by a hand-off-auto selector in auto.
  params: {length: 1.0, width: 1.0, height: 1.0}
  parts:
    - {id: body, type: scenery, description: Body,
       position: [0, "{height} / 2", 0], size: ["{length}", "{height}", "{width}"]}
    - {id: hoa, type: selector_switch, description: Hand-Off-Auto Selector,
       position: ["{length} / 2 + 0.05", 1.2, 0]}
  io:
    start: {command: DO}
    running: {from: "start and hoa.pos_b"}            # position b is auto: the permissive
    hoa_auto: {from: hoa.pos_b}
```

`variable_item` is the same with two more io:

```yaml
    speed: {command: AO, range: [0, 100], units: "%"}
    speed_fb: {from: "speed if running else 0", kind: AI, range: [0, 100], units: "%"}
```

An instance from a drawing:

```yaml
- id: AG_101
  type: variable_item
  description: Wash Water Tank Agitator
  position: [0, 2.4, 0]
  height: 0.6
  io:
    start: {tag: AG_101_RUN_DO, plc_tag: "AG101.Run"}
    running: {tag: AG_101_RUN_DI, on_delay_s: 2.0}    # start delay
    hoa_auto: AG_101_AUTO_DI
    speed: AG_101_SPD_AO
    speed_fb: {tag: AG_101_SPD_AI, lag_s: 4.0}        # first-order lag
```

The grammar has no `if ... else` today. Two options:

- **(a)** add a conditional expression to `core/expr.py`'s whitelist (`ast.IfExp`, a boolean
  test and two numbers);
- **(b)** write it as `speed * running`, with a boolean read as 0 or 1 in arithmetic.

Recommended: (a). (b) would weaken the rule that arithmetic takes numbers only, which
`tests/unit/test_expr.py` checks. `running` in `speed_fb` refers to another computed io.
Computed io may then read computed io, in the order they are written, and a cycle is a load
error naming it. That rule is new and part of this task.

### 2.3 Alternatives

- **(a) A `machine` primitive** with `start_delay_s`, `lag_s`, a permissive input and a box.
  Rejected under §5: every one of its behaviours is a composite or a transform away. It would be
  a second way to do what composites do, and it would grow a parameter for every drawing.
- **(b) A `mesh` key on a composite instance** instead of the `scenery` part. Rejected: the
  viewer, parity and exports would have to know composites, which expansion exists to hide.
- **(c) Stateful composites** (a composite keeping its own timer or filter). Rejected: state
  then lives in two places, parts and composites. The transform stage already holds the state
  that is not equipment.

### 2.4 Format change and shipped scenes

All additive:

- a `command` io of kind `AO`;
- `kind: AI` on an expression io, and command names in io expressions;
- the conditional expression;
- `lag_s` on an AI binding;
- the `scenery` type.

The format stays `0.1`. No shipped scene or library composite uses them, so none is touched.
The `accumulation_conveyor` library composite and the two scenes built on it
(`buffer-and-release`, `scenes/examples/accumulation-demo`) do not change.

---

## 3. The takeoff file (5.1)

### 3.1 Why a takeoff

A drawing is read by an assistant, a person, or both, and reading is error-prone. The takeoff
is a table a person checks against the drawing, row by row, before any scene exists:

- equipment;
- connections;
- I/O points;
- devices placed on conveyors.

The scene is then generated from it and never typed from the drawing. Every row says:

- where on which sheet it was read;
- how sure the reader was;
- what is still open.

### 3.2 The file

`<plant>.takeoff.yaml`, YAML like a scene, versioned on its own (`takeoff: "0.1"`), with a
JSON Schema exported beside the scene schema. Unknown keys are errors, as in a scene.

```yaml
takeoff: "0.1"
plant: {name: <scene name>, description: <one line>, author: <who read it>}
drawings:                      # the set it was read from
  - {id: <sheet id>, kind: pid | layout | io_list | other, title: <text>, rev: <text>}
equipment:                     # one row per item
  - row: EQ-<n>                # the row's own id, never reused
    tag: <tag on the drawing>  # e.g. TK-101; the scene id is it with '-' as '_' unless `id:`
    type: <built-in or composite type> | unknown
    description: <ISA-5.1 style, as the scene's>
    params: {<the type's parameters read from the drawing>}
    ref: [{sheet: <id>, grid: <zone, e.g. C3>}]          # every place it appears
    at: [x, y, z]              # metres, from the layout; or
    mount: {on: <conveyor tag>, along: <m from the infeed>, rail: left | right}
    confidence: read | inferred | assumed
    questions: [<text>]        # open questions; empty when none
connections:
  - {row: CN-<n>, from: <tag>[.<port>], to: <tag>[.<port>], line: <line number>,
     ref: [...], confidence: ..., questions: [...]}
io:
  - row: IO-<n>
    equipment: <tag>
    port: <the type's port>     # e.g. pv, run, running
    signal: DI | DO | AI | AO
    scene_tag: <optional; else derived, below>
    plc_tag: <the plant's name, from the I/O list>
    range: [lo, hi]            # analog only
    units: <text>              # analog only
    ref: [{sheet: <I/O list id>, row: <n>}]
    confidence: ..., questions: [...]
```

- **Confidence.**
  - `read`: printed on the drawing.
  - `inferred`: deduced from the drawing. Examples: a pump's suction side from the line
    routing; a range from an instrument data sheet referenced on the drawing.
  - `assumed`: not on the drawing; a default, or something the demo needs that the plant does
    not show.

  A row is never `read` with open questions. The person checking marks the file, not each row:
  `checked: {by: <initials>, date: <date>, drawings: [<sheet>@<rev>, ...]}`. A changed
  revision of any sheet makes the mark stale (decision 6).
- **"Left" and "right"** are `rail_left` and `rail_right` of the conveyor (`core/mounts.py`),
  looking from the infeed toward the discharge. `along` is metres from the infeed end along the
  belt, as `place_on_rail` takes it.
- **Derived scene tags.** `<ID>_<CODE>_<KIND>`, where `CODE` comes from the port, as the
  shipped scenes already name them:

  | Port | CODE |
  |---|---|
  | `run` and `running` | `RUN` |
  | `speed` | `SPD` |
  | `pv` | `PV` |
  | `position` | `POS` |
  | `position_fb` | `ZT` |
  | `hi_sw` | `LSH` |
  | `lo_sw` | `LSL` |
  | `extend` and `extended` | `EXT` |
  | `retracted` | `RET` |
  | `detect` | none: `PE_201_DI` |

  A port not in the table uses its own name in capitals. `scene_tag:` overrides the derived
  name. A derived name over 40 characters is a check error that asks for a `scene_tag`.

### 3.3 `openfactory takeoff check TAKEOFF`

The command writes nothing. Exit 1 on any error, 0 otherwise. It prints a summary: rows by
confidence, open questions by row, and whether the `checked` mark is current.

Errors, each naming the row:

- the schema;
- a duplicate `row`, `tag`, derived scene tag or `plc_tag`;
- a `ref` to a sheet not in `drawings`;
- an unknown `type`. Use `unknown` to mean unknown; it is a warning, since scaffold places a
  stand-in;
- a connection end that is not an equipment tag, or a port the type does not have (pipe ports
  of 4.1g2, `core/snaps.py` `PIPE_PORT_NAMES`);
- an I/O row on a missing item, or on a port the type does not have;
- an I/O row whose `signal` is not the port's kind;
- a `plc_tag` outside the 5.3 grammar;
- a `mount` problem:
  - `on` that is not a belt or roller conveyor in the takeoff;
  - a device type `place_on_rail` does not place;
  - a rail other than `left` or `right`;
  - **`along` outside 0 to the conveyor's length.** `place_on_rail` clamps `along` into the
    belt, so a device given at 3.4 m on a 3 m belt would silently land at the end. Check makes
    that an error instead of a moved device;
- both `at` and `mount` on one row.

`--strict` also fails on open questions and on a missing or stale `checked` mark. It is the gate
before a scaffold is shown to anyone.

### 3.4 `openfactory takeoff scaffold TAKEOFF -o SCENE`

The command refuses a takeoff that fails `check` and writes nothing. It never overwrites an
existing scene without `--force`. Otherwise it writes the scene and prints a report. **Every
row has exactly one outcome (placed, assumed, stand-in, unplaced, not connected), the outcomes
add up to the takeoff's rows, and a test checks that sum, so no row can vanish.** Open
questions are listed apart, since a placed row may still have one.

| Row | What scaffold writes | Reported as |
|---|---|---|
| A known type with what it needs | the component, its description, its params; a comment naming the row (`# EQ-4, P-001 C2`) | placed |
| A known type missing a required parameter, or a row marked `assumed` | the component, any missing parameter at its documented default with `# ASSUMED <param>` | assumed |
| `type: unknown`, or a type not in this release | a stand-in: a local composite type `standin_<id>` in the scene's `composites:` block, built as section 2's `driven_item` is (a `scenery` body sized from `params.length/width/height`, else 1 m), whose io are exactly the row's I/O rows: each DO and AO a `command`, the first DI following the first DO (a running feedback), the first AI following the first AO, any other DI false and any other AI the low end of its range. Its description starts `PLACEHOLDER` | stand-in, and each of its I/O rows |
| No `at` and no `mount` | placed in a holding row past the plant's far edge, 1.5 m apart, until 5.5 lays it out | unplaced |
| A `mount` | the position and heading from `core/mounts.place_on_rail` (a pusher's plate height from the tallest part an emitter in the takeoff makes, else the default) | placed on `<conveyor>` `<rail>` at `<along>` m |
| A connection | the `from`/`to` keys of the link, a `pipe` for a line between two nodes or a node and a link, or a `connections:` entry for conveyors | placed |
| A connection scaffold cannot make (a conveyor to a tank, say) | a YAML comment at the end of the scene, the connection as written | not connected: `<why>` |
| An open question (on any row, whatever its outcome) | a `# QUESTION (<row>): <text>` comment above the component | listed under open questions |

Exit codes:

- 0: every row is placed with no stand-in, no assumption and no question;
- 3: the scene was written with rows to resolve, listed in the report;
- 1: the takeoff is invalid, and nothing was written.

A script cannot mistake 3 for a clean build. The scaffolded scene has no `checks:` and no stand-in
script: those come from the plant's sequence of operations, which the takeoff does not hold.

### 3.5 Devices on a conveyor: the open question of 2026-11-14

The question: does the scene need a stored `mounted_on`, or does the takeoff hold it and the
scene keep plain positions?

**Recommended: the takeoff holds it. The scene keeps plain positions, with no `mounted_on`.**

- **One source of geometry.** The scene stays positions only, so the viewer, parity, lint and
  editor have nothing new to agree on. A stored `mounted_on` would be a second statement of a
  position that could disagree with the first, and something would have to rule which wins.
- **The intent is not lost.** It lives in the reviewed takeoff, next to the sheet reference it
  came from, which is where a checker looks for it. `takeoff check --against SCENE` (part of
  5.6) re-runs `place_on_rail` for every `mount` row and reports any device whose scene position
  differs by more than 1 mm. A hand edit that moved a device off its takeoff position is found.
- **What the scene already checks.** W214 (4.1g1) already flags a device short of its belt or
  beside it, and the editor's drop rule (4.1g1) places a device dragged onto a rail by the same
  function. Neither needs a stored mount.
- **What this gives up.** Moving a conveyor in the editor does not carry its devices with it.
  That is 4.1g3, parked. Re-running `takeoff scaffold` after the takeoff changes re-places them.
  If the owner later wants the editor to carry devices, `mounted_on` can still be added as an
  optional key without a format change. Deciding now not to store it costs nothing later.

### 3.6 Worked example: a wash-water skid and a carton reject belt

A plant of our own invention, drawn on three sheets:

- **P-001**, the P&ID: a make-up supply through a level control valve LV-101 into tank TK-101,
  with level transmitter LT-101, a high-level switch, and an agitator AG-101. Transfer pump
  P-101 feeds the washer through a flow transmitter FT-101.
- **L-001**, the layout: the skid, and a 3 m carton reject belt CV-201 with a photo eye PE-201
  and a reject pusher CYL-201 dimensioned from the belt's infeed.
- **IO-001**, the I/O list, with the plant program's names.

**The takeoff, in full:**

```yaml
takeoff: "0.1"
plant:
  name: wash-skid
  description: Wash-water tank with level control and a transfer pump, and a carton reject belt.
  author: HIS Controls
drawings:
  - {id: P-001, kind: pid, title: Wash Water Skid P&ID, rev: A}
  - {id: L-001, kind: layout, title: Wash Water Skid and Reject Belt Layout, rev: A}
  - {id: IO-001, kind: io_list, title: Wash Water Skid I/O List, rev: A}
checked: {by: XX, date: 2026-11-15, drawings: [P-001@A, L-001@A, IO-001@A]}
equipment:
  - {row: EQ-1, tag: SUP-101, type: supply, description: Wash Water Make-Up Supply,
     params: {pressure_bar: 2.0}, ref: [{sheet: P-001, grid: A1}], at: [-2.0, 2.4, 0],
     confidence: assumed, questions: ["Make-up pressure not shown; 2 bar assumed."]}
  - {row: EQ-2, tag: LV-101, type: valve_control,
     description: Wash Water Tank Level Control Valve, params: {kv: 6},
     ref: [{sheet: P-001, grid: B2}], at: [-1.2, 2.4, 0], confidence: read, questions: []}
  - {row: EQ-3, tag: TK-101, type: tank, description: Wash Water Tank,
     params: {diameter: 1.2, height: 1.8, initial_level: 0.9, hi_level: 1.6},
     ref: [{sheet: P-001, grid: C2}, {sheet: L-001, grid: B2}], at: [0, 0.5, 0],
     confidence: read, questions: []}
  - {row: EQ-4, tag: LT-101, type: transmitter,
     description: Wash Water Tank Level Transmitter,
     params: {measures: TK-101.level, range: [0, 1.8], lag_s: 1.0},
     ref: [{sheet: P-001, grid: C3}], at: [0.7, 1.4, 0], confidence: read, questions: []}
  - {row: EQ-5, tag: AG-101, type: unknown, description: Wash Water Tank Agitator,
     params: {length: 0.3, width: 0.3, height: 0.6},
     ref: [{sheet: P-001, grid: C1}], at: [0, 2.4, 0], confidence: read,
     questions: ["No agitator type yet (5.9); a stand-in until then."]}
  - {row: EQ-6, tag: P-101, type: pump, description: Wash Water Transfer Pump,
     params: {design_flow_m3h: 3.0, design_head_m: 6.0},
     ref: [{sheet: P-001, grid: D2}, {sheet: L-001, grid: C2}], at: [1.2, 0.3, 0],
     confidence: read, questions: []}
  - {row: EQ-7, tag: FT-101, type: transmitter,
     description: Wash Water Transfer Flow Transmitter,
     params: {measures: P-101.flow, range: [0, 5.0], lag_s: 0.5},
     ref: [{sheet: P-001, grid: D3}], at: [1.8, 0.6, 0], confidence: read, questions: []}
  - {row: EQ-8, tag: DR-101, type: drain, description: Wash Water To Washer,
     ref: [{sheet: P-001, grid: E2}], at: [2.6, 0.3, 0], confidence: assumed,
     questions: ["The washer is off this drawing; an open drain stands for it."]}
  - {row: EQ-9, tag: CV-201, type: belt_conveyor, description: Carton Reject Belt Conveyor,
     params: {length: 3.0, width: 0.5, speed_mps: 0.4},
     ref: [{sheet: L-001, grid: B4}], at: [0, 0, 2.5], confidence: read, questions: []}
  - {row: EQ-10, tag: PE-201, type: photo_eye, description: Carton Reject Belt Photo Eye,
     params: {beam_length: 0.6}, ref: [{sheet: L-001, grid: B4}],
     mount: {on: CV-201, along: 1.2, rail: left}, confidence: read, questions: []}
  - {row: EQ-11, tag: CYL-201, type: pusher, description: Carton Reject Pusher,
     params: {stroke: 0.6, extend_time_s: 0.4}, ref: [{sheet: L-001, grid: C4}],
     mount: {on: CV-201, along: 2.0, rail: left}, confidence: read,
     questions: []}
  - {row: EQ-12, tag: EMT-201, type: box_emitter, description: Carton Reject Belt Carton Source,
     params: {interval_s: 4.0, parts: [{name: carton, size: [0.3, 0.3, 0.3], weight: 1.0}]},
     ref: [{sheet: L-001, grid: A4}], at: [0.3, 0.155, 2.5], confidence: assumed,
     questions: ["Not on the drawing: a parts source for the demo."]}
  - {row: EQ-13, tag: REM-201, type: part_remover, description: Carton Reject Belt Discharge,
     params: {size: [0.3, 1.0, 1.0]}, ref: [{sheet: L-001, grid: D4}], at: [3.15, 0, 2.5],
     confidence: assumed, questions: ["Not on the drawing: the belt's discharge."]}
connections:
  - {row: CN-1, from: SUP-101, to: LV-101, line: 25-W-100, ref: [{sheet: P-001, grid: A2}],
     confidence: read, questions: []}
  - {row: CN-2, from: LV-101, to: TK-101.top, line: 25-W-101, ref: [{sheet: P-001, grid: B2}],
     confidence: read, questions: []}
  - {row: CN-3, from: TK-101.bottom, to: P-101.suction, line: 25-W-102,
     ref: [{sheet: P-001, grid: D2}], confidence: read, questions: []}
  - {row: CN-4, from: P-101.discharge, to: DR-101, line: 25-W-103,
     ref: [{sheet: P-001, grid: E2}], confidence: read, questions: []}
io:
  - {row: IO-1, equipment: LV-101, port: position, signal: AO, plc_tag: LV101_CV,
     ref: [{sheet: IO-001, row: 1}], confidence: read, questions: []}
  - {row: IO-2, equipment: LV-101, port: position_fb, signal: AI, plc_tag: LV101_POS,
     ref: [{sheet: IO-001, row: 2}], confidence: read, questions: []}
  - {row: IO-3, equipment: TK-101, port: hi_sw, signal: DI, plc_tag: LSH101,
     ref: [{sheet: IO-001, row: 3}], confidence: read, questions: []}
  - {row: IO-4, equipment: LT-101, port: pv, signal: AI, plc_tag: LT101_PV,
     range: [0, 1.8], units: m, ref: [{sheet: IO-001, row: 4}], confidence: read,
     questions: []}
  - {row: IO-5, equipment: AG-101, port: run, signal: DO, plc_tag: AG101.Run,
     ref: [{sheet: IO-001, row: 5}], confidence: read, questions: []}
  - {row: IO-6, equipment: AG-101, port: running, signal: DI, plc_tag: AG101.Running,
     ref: [{sheet: IO-001, row: 6}], confidence: read, questions: []}
  - {row: IO-7, equipment: P-101, port: run, signal: DO, plc_tag: "Program:Transfer.P101_Run",
     ref: [{sheet: IO-001, row: 7}], confidence: read, questions: []}
  - {row: IO-8, equipment: P-101, port: running, signal: DI,
     plc_tag: "Program:Transfer.P101_Running", ref: [{sheet: IO-001, row: 8}],
     confidence: read, questions: []}
  - {row: IO-9, equipment: FT-101, port: pv, signal: AI, plc_tag: FT101_PV,
     range: [0, 5.0], units: m3/h, ref: [{sheet: IO-001, row: 9}], confidence: read,
     questions: []}
  - {row: IO-10, equipment: CV-201, port: run, signal: DO, plc_tag: CV201.Run,
     ref: [{sheet: IO-001, row: 10}], confidence: read, questions: []}
  - {row: IO-11, equipment: CV-201, port: running, signal: DI, plc_tag: CV201.Running,
     ref: [{sheet: IO-001, row: 11}], confidence: read, questions: []}
  - {row: IO-12, equipment: PE-201, port: detect, signal: DI, plc_tag: Line2_DI_Word.4,
     ref: [{sheet: IO-001, row: 12}], confidence: read, questions: []}
  - {row: IO-13, equipment: CYL-201, port: extend, signal: DO, plc_tag: CYL201.Ext_Cmd,
     ref: [{sheet: IO-001, row: 13}], confidence: read, questions: []}
  - {row: IO-14, equipment: CYL-201, port: extended, signal: DI, plc_tag: CYL201.Ext_LS,
     ref: [{sheet: IO-001, row: 14}], confidence: read, questions: []}
  - {row: IO-15, equipment: CYL-201, port: retracted, signal: DI, plc_tag: CYL201.Ret_LS,
     ref: [{sheet: IO-001, row: 15}], confidence: inferred,
     questions: ["IO-001 row 15 reads 'CYL201 RET'; the member name is inferred."]}
```

In 4.1g2's process types, a pipe is a component of its own. So scaffold writes a `pipe` for each
connection between a valve, pump or tank. `PIPE_101` is line 25-W-101 and `PIPE_102` is line
25-W-103, each with a diameter read from the line number's size (25 mm). A connection into a
valve or pump (CN-1, CN-3) is that link's `from`.

**The scene scaffold writes, without the agitator.** This text loads and lints today: it was
checked with `openfactory validate` and `openfactory lint` on 2026-11-15. The only lint output
is W201 and W202, "no PLC side", which is expected for a scaffold with no program yet. The
pusher's and the photo eye's positions are `place_on_rail`'s output for EQ-10 and EQ-11,
computed with the real function: (1.2, 0.1, 2.5) and (2.0, 0.1375, 2.2).

```yaml
# Written by `openfactory takeoff scaffold wash-skid.takeoff.yaml` (PLAN 5.1, proposed).
# Every component names its takeoff row; positions come from the layout L-001 or from
# core/mounts.place_on_rail. Training and demo use only. Not a safety-rated simulation.
openfactory: "0.1"
scene:
  name: wash-skid
  description: Wash-water tank with level control and a transfer pump, and a carton reject belt.
  author: HIS Controls
  seed: 1
plc:
  protocol: modbus_tcp
components:
  - id: SUP_101            # takeoff equipment SUP-101 (P-001 A1, assumed)
    type: supply
    description: Wash Water Make-Up Supply
    position: [-2.0, 2.4, 0]
    pressure_bar: 2.0
  - id: LV_101             # takeoff equipment LV-101 (P-001 B2)
    type: valve_control
    description: Wash Water Tank Level Control Valve
    position: [-1.2, 2.4, 0]
    from: SUP_101
    to: PIPE_101
    kv: 6
    io:
      position: LV_101_POS_AO
      position_fb: LV_101_ZT_AI
  - id: PIPE_101           # takeoff connection CN-2 (line 25-W-101)
    type: pipe
    description: Wash Water Tank Make-Up Line
    position: [-0.6, 2.4, 0]
    from: LV_101
    to: TK_101.top
    diameter: 0.025
  - id: TK_101             # takeoff equipment TK-101 (P-001 C2; L-001 B2)
    type: tank
    description: Wash Water Tank
    position: [0, 0.5, 0]
    diameter: 1.2
    height: 1.8
    initial_level: 0.9
    hi_level: 1.6
    io:
      hi_sw: TK_101_LSH_DI
  - id: LT_101             # takeoff equipment LT-101 (P-001 C3)
    type: transmitter
    description: Wash Water Tank Level Transmitter
    position: [0.7, 1.4, 0]
    measures: TK_101.level
    range: [0, 1.8]
    lag_s: 1.0
    io:
      pv: LT_101_PV_AI
  - id: P_101              # takeoff equipment P-101 (P-001 D2; L-001 C2)
    type: pump
    description: Wash Water Transfer Pump
    position: [1.2, 0.3, 0]
    from: TK_101.bottom
    to: PIPE_102
    design_flow_m3h: 3.0
    design_head_m: 6.0
    io:
      run: P_101_RUN_DO
      running: P_101_RUN_DI
  - id: FT_101             # takeoff equipment FT-101 (P-001 D3)
    type: transmitter
    description: Wash Water Transfer Flow Transmitter
    position: [1.8, 0.6, 0]
    measures: P_101.flow
    range: [0, 5.0]
    lag_s: 0.5
    io:
      pv: FT_101_PV_AI
  - id: PIPE_102           # takeoff connection CN-4 (line 25-W-103)
    type: pipe
    description: Wash Water Transfer Line
    position: [1.8, 0.3, 0]
    from: P_101
    to: DR_101
    diameter: 0.025
  - id: DR_101             # takeoff equipment DR-101 (P-001 E2, assumed)
    type: drain
    description: Wash Water To Washer
    position: [2.6, 0.3, 0]
  - id: CV_201             # takeoff equipment CV-201 (L-001 B4)
    type: belt_conveyor
    description: Carton Reject Belt Conveyor
    position: [0, 0, 2.5]
    length: 3.0
    width: 0.5
    speed_mps: 0.4
    io:
      run: CV_201_RUN_DO
      running: CV_201_RUN_DI
  - id: PE_201             # takeoff equipment PE-201: on CV-201, 1.2 m from the infeed, left rail
    type: photo_eye
    description: Carton Reject Belt Photo Eye
    position: [1.2, 0.1, 2.5]
    beam_length: 0.6
    io:
      detect: PE_201_DI
  - id: CYL_201            # takeoff equipment CYL-201: on CV-201, 2.0 m from the infeed, left rail
    type: pusher
    description: Carton Reject Pusher
    position: [2.0, 0.1375, 2.2]
    stroke: 0.6
    extend_time_s: 0.4
    io:
      extend: CYL_201_EXT_DO
      extended: CYL_201_EXT_DI
      retracted: CYL_201_RET_DI
  - id: EMT_201            # takeoff equipment EMT-201 (assumed: a parts source, not drawn)
    type: box_emitter
    description: Carton Reject Belt Carton Source
    position: [0.3, 0.155, 2.5]
    interval_s: 4.0
    parts:
      - {name: carton, size: [0.3, 0.3, 0.3], weight: 1.0}
  - id: REM_201            # takeoff equipment REM-201 (assumed: the belt's discharge)
    type: part_remover
    description: Carton Reject Belt Discharge
    position: [3.15, 0, 2.5]
    size: [0.3, 1.0, 1.0]
```

**What 5.3 and 5.2 add to that text** (marked: it does not load today):

```yaml
# 5.3: every binding gets its plc_tag from the I/O row, for example
      pv: {tag: LT_101_PV_AI, plc_tag: LT101_PV}
      run: {tag: P_101_RUN_DO, plc_tag: "Program:Transfer.P101_Run"}
      detect: {tag: PE_201_DI, plc_tag: Line2_DI_Word.4}
# 5.2: the agitator row (EQ-5, type unknown) becomes a stand-in type with exactly its I/O
composites:
  - type: standin_ag_101
    description: PLACEHOLDER for takeoff EQ-5 AG-101 (type unknown), with its I/O rows
    params: {length: 1.0, width: 1.0, height: 1.0}
    parts:
      - {id: body, type: scenery, description: Body,
         position: [0, "{height} / 2", 0], size: ["{length}", "{height}", "{width}"]}
    io:
      run: {command: DO}                  # IO-5
      running: {from: "run"}              # IO-6: the first DI follows the first DO
# ...and in components:
  - id: AG_101             # PLACEHOLDER: takeoff EQ-5 AG-101 (P-001 C1), type unknown
    # QUESTION (EQ-5): No agitator type yet (5.9); a stand-in until then.
    type: standin_ag_101
    description: PLACEHOLDER Wash Water Tank Agitator
    position: [0, 2.4, 0]
    length: 0.3
    width: 0.3
    height: 0.6
    io:
      run: {tag: AG_101_RUN_DO, plc_tag: AG101.Run}
      running: {tag: AG_101_RUN_DI, plc_tag: AG101.Running}
```

**The report scaffold prints for it** (exit 3: written, with rows to resolve):

```text
wash-skid.takeoff.yaml -> wash-skid.yaml: 32 rows (13 equipment, 4 connections, 15 I/O)
  placed          25
  assumed          4   EQ-1 SUP-101, EQ-8 DR-101, EQ-12 EMT-201, EQ-13 REM-201
  stand-in         3   EQ-5 AG-101 (type unknown: standin_ag_101); IO-5 -> run; IO-6 -> running
  unplaced         0
  not connected    0
  25 + 4 + 3 + 0 + 0 = 32: every row accounted for
open questions, written as # QUESTION comments (6): EQ-1, EQ-5, EQ-8, EQ-12, EQ-13, IO-15
```

Each row has exactly one outcome, so the outcomes add up to the rows. Open questions are listed
apart, since a placed row may still have one (IO-15).

### 3.7 The sample drawing set for tests

The repository needs one drawing set of its own for 5.1's Done-when, 5.5's layout and 5.6's
overlay. It is drawn by us and released under CC-BY-4.0 like the other docs and models. **It is
not made yet; this section describes it.**

- **Contents**, the plant of section 3.6 under `docs/samples/wash-skid/`:
  - `P-001.svg`: the P&ID. Instrument bubbles are our own circles with tag letters inside.
    Lines carry line numbers. A zone grid runs A to E across and 1 to 4 down.
  - `L-001.svg`: the layout, in plan view, dimensioned in millimetres from a datum, with the
    belt's devices dimensioned from the infeed.
  - `IO-001.csv`: the I/O list, in the column layout of the I/O CSV (1.8) plus a `plc_tag`
    column. CSV, because an I/O list usually arrives as a table.
  - `wash-skid.takeoff.yaml`: the takeoff of section 3.6, which tests check and scaffold.
- **Traps on purpose**, so the tests and a reader practise on what real drawings have:
  - an item with no component type (the agitator);
  - an abbreviated I/O-list entry (IO-15);
  - a dimension on the layout that runs past the end of the belt in one variant of the
    takeoff, which `check` must refuse.
- **How it is made.** SVG written as text, by hand or by a small script in `tools/` that draws
  only primitives: lines, circles, rectangles, text. No symbol library and no template from any
  drawing package are used. Each file stays under 100 KB, far under the 1 MB rule.
- **What tests use it for.** `takeoff check` passes. `--strict` fails on the open questions.
  `scaffold` writes a scene that validates. The report accounts for every row, with AG-101 as
  a stand-in. Changing EQ-11's `along` to 3.4 fails `check`, not `scaffold`. A test also reads
  the CSV's `plc_tag` column and checks it against the takeoff's I/O rows, so the two cannot
  drift. Nothing in a test reads the SVGs: reading a drawing is the assistant's and the
  person's job. The SVGs are for 5.5 and 5.6, and for practice.

### 3.8 Alternatives

- **(a) No takeoff: the assistant writes the scene from the drawing.** Rejected by
  Milestone 5's own reason: a person cannot check a scene against a drawing row by row.
- **(b) A spreadsheet takeoff** (CSV per table). It suits people who work in spreadsheets, but
  it cannot nest `mount` or `params` and has no schema check. `takeoff import --csv` could
  come later and write the YAML.
- **(c) The takeoff inside the scene** (a `takeoff:` block). Rejected: a scene would carry
  review state and drawing references into every export and recording. A customer's takeoff
  is as private as the drawing, and the scene made from it already is.

### 3.9 Format change and shipped scenes

The takeoff is a new file type with its own version. The scene format does not change for 5.1,
beyond what 5.2 and 5.3 add. No shipped scene is touched. The two new commands are a minor
release.

---

## 4. Plants in several files (5.4)

**Is anything needed beyond composites and bundles? Yes, a little.**

- Composites share types, not instances.
- A bundle carries one scene.
- A lockfile and a lint run each cover one scene file.

So two areas in two files cannot run as one plant with one address map today. What is needed
is one key, `include:`, plus plant-wide handling in four places that already exist: load, lint,
lockfile, and `--area`. Nothing else is new.

### 4.1 What exists today

- A scene is one file, and its model refuses unknown keys, so `include:` is a load error.
- Composite types are shared by `composites: [{library: name}]`, found in `_library/`
  (2.16, 2.5b).
- The address map is one lockfile per scene file and encoding (ADR-0004).
- `openfactory lint scenes` lints each file alone.
- A bundle (2.18) carries one scene with its library composites and meshes.
- `validate`, `lint` and `migrate --check` take a folder's `*.yaml` (`cli._scene_files`), not
  its subfolders.

### 4.2 Proposal

**A plant file and its area files.**

```text
scenes/plants/wash-plant/
  wash-plant.yaml                    # the plant: scene, plc, checks, include, cross-area links
  wash-plant.float32.modbus.yaml     # the one address map for the whole plant
  areas/skid.yaml                    # an area: an ordinary scene, loadable alone
  areas/reject.yaml
```

```yaml
# wash-plant.yaml
openfactory: "0.1"
scene: {name: wash-plant, description: ..., author: HIS Controls, seed: 1}
plc: {protocol: modbus_tcp}
include:
  - {area: SKID, file: areas/skid.yaml}
  - {area: REJECT, file: areas/reject.yaml}
connections:                         # links that cross areas, in the plant file only
  - {from: CV_101, to: CV_201}
checks: {duration_s: 60, none_lost: true}
```

- **Load.** The plant's components are each included file's components and connections, plus
  the plant file's own. The plant file's `scene`, `plc` and `checks` apply. An included file's
  own `scene`, `plc` and `checks` are kept for running it alone and ignored in the plant, which
  `lint` says once per file (info, not a warning). `composites` and `library` entries are
  merged by type name, and two different definitions of one type are a load error naming both
  files. `include` is one level deep: an area cannot include, which keeps the address rule
  below simple. A path may not leave the plant's folder (`../` is refused, as for meshes, 2.15).
- **Ids and tags across areas.** Ids and tags stay global and must be unique across the plant,
  and a clash is a load error naming both files. They are not prefixed automatically. A prefix
  would rename every tag a script or PLC program uses, and the lockfile key would depend on
  where a file is included. A cross-area connection names the two ids, as any connection does.
  An include's `area:` name is what `--area` selects, and the default `scene.area` (5.3) for
  that area's exports. Recommended naming: an area's ids and tags share a prefix, as the HIS
  form `AREA_EQUIP_NNN` already gives, so a clash is rare by design.
- **One address map.** The lockfile belongs to the plant file: `wash-plant.<encoding>.modbus.yaml`,
  with ADR-0004's rules unchanged (existing addresses kept, new tags appended in their table).
  The area files have no lockfiles of their own in a plant folder.
- **One area alone.** `openfactory run wash-plant.yaml --area SKID` (also `test`, `export`,
  `edit`) loads the plant's lockfile and only that area's components, and serves the area's
  tags at the **plant's** addresses. The address map is the same either way, which is 5.4's
  Done-when.
  - The other areas' tags are absent, and their addresses are holes in the map. The Modbus
    server already treats a hole as it treats one a pin or a retired lockfile entry leaves
    (`protocols/modbus.py`): a read inside a table gives 0 or false, a write to it is refused
    with ILLEGAL ADDRESS, and a read past a table's last address is refused too.
  - A cross-area connection with one end outside the area is dropped, and the run says so
    once.
  - The area's own `checks:` grade the run.

  Running `areas/skid.yaml` directly still works as a plain scene with its own lockfile. That
  is a different address map, and `lint` warns when a file that a plant includes has a
  lockfile beside it (W216).
- **Lint across areas.** `openfactory lint wash-plant.yaml` lints the plant as one scene: the
  unwired-port checks, W205 overlap across area boundaries, and W214. `lint scenes/plants/`
  lints each plant file and skips the files they include, so an area is not linted twice.
- **Bundles.** A bundle of a plant carries the plant file, every included file, the plant
  lockfile, and the library composites and meshes any of them uses. The manifest's file list
  grows. The bundle version stays 1, because a bundle of a one-file scene is unchanged.
- **PLAN §9 rule 13.** A shipped plant is held like any scene. The plant runs under its stand-in
  script, with a kit or a `tests/references/<plant>.run`. `run_reference.shipped()` and CI's
  scene loops learn the `scenes/plants/*/` folder, and the included area files are not scenes
  of their own for that rule.

### 4.3 What the editor does with an included file

**It opens one file at a time.**

- `openfactory edit wash-plant.yaml` shows the whole plant, and only the plant file's own
  components and cross-area connections are editable. The included areas are drawn dimmed, and
  selecting one says "in areas/skid.yaml: open that file to edit it".
- `openfactory edit wash-plant.yaml --area SKID` edits `areas/skid.yaml`, with the other areas
  drawn dimmed around it for context, and saves only that file. Save runs the plant-wide lint,
  so a clash with another area is refused, as any lint error is.

Editing several files in one session (one save writing several files) is not proposed. It
would make the 4.7 `Document`'s one-file, minimal-diff save into a transaction across files.

### 4.4 Alternatives

- **(a) Status quo: one file per plant.** A 300-component file (5.12) works but is hard to
  review and to share between people. Kept as what a small plant does anyway.
- **(b) A merge tool** (`openfactory merge plant.yaml -o merged.yaml`) that writes one scene
  from several. No format change. But the merged file is generated and must not be edited, and
  every run and lint is of a generated file. Rejected.
- **(c) Areas as composites.** A composite instance per area. Rejected: composites are types
  with parameters and a fixed set of io, and an area is one particular piece of a plant.
- **(d) Automatic id and tag prefixes per area.** Rejected above, because addresses and tags
  would depend on the include.

### 4.5 Format change and shipped scenes

Additive: `include:` (a plant file), `area:` on an include entry, and `--area` on the commands.
The format stays `0.1`. A scene without `include:` is unchanged. No shipped scene is touched;
the first plant (5.4's two-area sample) is new, under `scenes/plants/`.

---

## 5. Proposed tasks, each with a Done-when

In this order, so each builds on the last. 5.3 and 5.2 are independent of each other. 5.1 needs
both for its stand-ins and `plc_tag`s. 5.4 is independent and can run beside them.

- **5.3a `plc_tag` in the scene and the exports.** The key, its grammar, duplicate and
  case-duplicate errors (once the name rules are verified), `scene.area`,
  `plc.heartbeat_plc_tag`, the CSV and `CONTEXT.md` column, the L5X stub rules by form, the
  SCADA export parameter, and W215.
  *Done when:* a copy of `sorting-by-height` with every binding given a `plc_tag` of each form
  (controller, program, member, bit) exports:
  - a CSV whose `plc_tag` column equals the scene's;
  - an L5X with one controller tag, one program tag and the member and bit rows listed in its
    comment, not declared;
  - a SCADA export whose device-path parameters are the plant names.

  Removing one binding's `plc_tag` raises W215 for that binding only. Every shipped scene's
  exports, lockfiles, kits and run references are byte-identical (CI's gates). A test
  enumerates the 5.3 grammar's accepted and refused names.
- **5.3b The Logix path by plant names.** The client reads and writes `plc_tag`s, maps results
  back to scene tags, sets `init_program_tags` when a `Program:` name is present, and runs the
  connect-time type check with the bit split.
  *Done when:* `tests/unit/test_logix.py`'s fake controller holds:
  - a program-scoped tag;
  - a structure with a `BOOL` member and a `REAL` member;
  - a `DINT` whose bit 4 is a DI.

  The sorter copy exchanges all of them in one write and one read per cycle. A wrong-type
  member is "mismatched" and left out. A bit on a `REAL` is refused at connect. The run on the
  bench PLC stays 2.2's Done-when.
- **5.2a Composite additions.** Command names in io expressions, `command: AO`, `kind: AI`
  numeric io, the conditional expression, computed io reading computed io (cycles refused).
  *Done when:* a scene's composite reports `running` one tick after `start` with no part
  having a running output. `on_delay_s: 2.0` on that binding makes it 100 ticks at 50 Hz. A
  `speed_fb` AI follows `speed` while running and reads 0 when stopped. A cycle
  `a <- b <- a` is a load error naming it. `test_expr.py`'s generated expressions still raise
  only `ExprError`.
- **5.2b `lag_s` on an AI binding.** In `TransformStage`, the transmitter's discretization.
  *Done when:* a step from 0 to 100 through `lag_s: 4` reads within 0.5 of 63.2 at 4 s and
  within 0.5 of 98.2 at 16 s at dt 0.02. The run is identical twice. The CSV's transform column
  says `lag 4 s`.
- **5.2c The `scenery` primitive and the library composites.** The 5.11 type brought forward:
  `size`, `color`, `mesh`, no I/O, no physics. The `driven_item` and `variable_item` library
  composites of section 2.2.
  *Done when:* PLAN 5.2's own Done-when holds: a scene holds a `variable_item` drawn as a box and
  then with a mesh, with its start, running, speed and speed feedback ports and a 2 s start
  delay, and its tags export like any other. The screenshot is looked at. A unit test, a
  docstring spec and a section in [the component reference](../components.md) cover
  `scenery`.
- **5.1a The takeoff file and `takeoff check`.** The schema, its JSON Schema export,
  and `--strict` (`--against` is 5.6).
  *Done when:* the sample takeoff of section 3.6 passes `check`, and `--strict` fails it on its
  six open questions. One test per error in section 3.3 fails with that row named, including
  `along: 3.4` on the 3 m belt.
- **5.1b `takeoff scaffold`.**
  *Done when:* the sample takeoff scaffolds to a scene that validates, its report accounts for
  all 32 rows (25 placed, 4 assumed, 3 stand-in), and AG-101 is a stand-in type carrying
  IO-5 and IO-6. The exit code is
  3. PE-201 and CYL-201 sit where `place_on_rail` puts them. Removing a row's `type` (making it
  `unknown`) adds one stand-in and never removes a row. Running the scaffold twice writes the
  same bytes.
- **5.1c The sample drawing set.** Section 3.7's files, drawn by us.
  *Done when:* the files are in `docs/samples/wash-skid/` with their license line, each is
  under 100 KB, and the test that cross-checks the I/O CSV against the takeoff passes. Each
  SVG has been opened and looked at.
- **5.4 `include:`, the plant lockfile and `--area`.**
  *Done when:* a two-area sample plant runs as one plant and passes its checks at 60 s and
  300 s. Each area runs alone with `--area` and passes its own checks. The address of every
  tag is the same in all three runs (read from the Modbus server). A duplicate id across areas
  is a load error naming both files. The editor opens the plant with the other areas dimmed
  and saves only the file it edits. The plant is held by PLAN §9 rule 13.

---

## 6. Decisions for the owner

1. **5.3: the plant's name beside the scene's (`plc_tag:`), or instead of it?** Recommended:
   beside. The scene tag stays the key for everything inside OpenFactory, so the lockfiles
   cannot move and scripts stay readable. The plant name is used only by the Logix path and
   the exports. (Section 1.2; alternatives in 1.5.)
2. **5.3: what the lint says about a plant name.** The draft Done-when says "a name outside the
   HIS pattern is a warning, not an error". With decision 1, that can only mean a `plc_tag`.
   The options:
   - **(a)** no warning for a well-formed `plc_tag`, and W215 for a binding with no `plc_tag`
     in a scene where others have one;
   - **(b)** one summary warning per scene counting the names outside the pattern.

   A malformed or duplicate `plc_tag` is a load error either way. Recommended: (a), and the
   Done-when reworded to "a plant name outside the HIS pattern loads and exports with no
   error; a binding left without a plant name in a mapped scene is a warning". (Section 1.3.)
3. **5.3: the L5X stub and members of the plant's own tags.** Recommended: declare controller
   and program tags, and list members, elements and bits in a comment without declaring them,
   because the plant's program already declares them. The alternative is to refuse to export
   a scene that has any. (Section 1.2.)
4. **5.3: may the Logix path upload every program's tags when a `Program:` name is present?**
   Recommended: yes, `init_program_tags=True` only then. pycomm3 1.2.16 replaces its tag cache
   when one program is uploaded alone, so the per-program option would mean using a private
   attribute. The upload time on a large program is to be measured on the bench PLC.
   (Section 1.4.)
5. **5.2: the five additions and their kinds.** Recommended: the composite additions (command
   names and `AO` commands in io expressions, numeric `kind: AI` io, a conditional expression),
   `lag_s` as a binding transform, and the `scenery` primitive brought forward from 5.11. A
   `machine` primitive is not added. (Section 2.2; alternatives in 2.3.)
6. **5.1: how a takeoff records review.** Recommended: one `checked` mark for the file, naming
   who, when, and each sheet with its revision. It goes stale when a revision changes, and
   `--strict` requires it current. The alternative is a mark per row, which is finer but
   rarely kept up by hand. (Section 3.2.)
7. **5.1: what scaffold does with a row it cannot place.** Recommended:
   - an unknown type becomes a local stand-in type carrying exactly the row's I/O;
   - an unplaced item goes into a holding row;
   - a connection it cannot make becomes a comment;
   - every one is in the report, with exit code 3;
   - a test checks that the report and the placed rows account for every row.

   (Section 3.4.)
8. **5.1: the question left open on 2026-11-14, a stored `mounted_on`.** Recommended: not
   stored. The takeoff holds the mount, and the scene keeps plain positions placed by
   `core/mounts.place_on_rail`. `takeoff check --against SCENE` reports drift, and W214 and the
   editor's drop rule work as they do. It can still be added later as an optional key if the
   editor must carry devices with a moved belt (4.1g3). (Section 3.5.)
9. **5.4: `include:` with global ids and tags, one plant lockfile, and `--area`.**
   Recommended: yes. There is no automatic prefixing, and a clash across areas is a load error
   naming both files. The plant file owns the lockfile, and `--area`
   serves one area at the plant's addresses. (Section 4.2.)
10. **5.4: the editor and included files.** Recommended: one file at a time. Open the plant to
    edit the plant file, with the areas dimmed, or `--area` to edit one area in context. There
    is no save across files. (Section 4.3.)
11. **5.1: the sample drawing set's form.** Recommended: SVG drawn as text by us (by hand or a
    primitives-only script), an I/O list as CSV, CC-BY-4.0, under `docs/samples/wash-skid/`,
    with the deliberate traps of section 3.7. (Section 3.7.)
