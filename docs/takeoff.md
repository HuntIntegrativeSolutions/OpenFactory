# The takeoff: from drawings to a scene

A plant's drawings (P&IDs, a layout, an I/O list) are read into a **takeoff**: a table a person
checks against the drawings, row by row, before any scene exists (PLAN 5.1). The scene is then
made from the takeoff, never typed from the drawings. Reading a drawing is error-prone. A table
with a sheet reference, a confidence mark and the open questions on every row is something a
person can check.

OpenFactory is a training and demo tool, not a safety-rated simulation. A customer's drawings,
and the takeoff and scene made from them, never enter this repository; the sample here is a
plant of our own invention, `docs/samples/wash-skid/`.

## The file

`<plant>.takeoff.yaml` is YAML with its own version, `takeoff: "0.1"`, and a JSON Schema,
`web/public/takeoff.schema.json`. Unknown keys are errors at every level.

```yaml
takeoff: "0.1"
plant: {name: wash-skid, description: Wash-water tank and a carton reject belt., author: HIS Controls}
drawings:
  - {id: P-001, kind: pid, title: Wash Water Skid P&ID, rev: A}
  - {id: L-001, kind: layout, title: Wash Water Skid and Reject Belt Layout, rev: A,
     frame: {units: mm, datum: [-3.0, 0, 4.0], rotation_deg: 0}}
  - {id: IO-001, kind: io_list, title: Wash Water Skid I/O List, rev: A}
checked: {by: XX, date: 2026-11-15, drawings: [P-001@A, L-001@A, IO-001@A], rows: 2c9c61b4ae244f3a}
equipment:
  - {row: EQ-3, tag: TK-101, type: tank, description: Wash Water Tank,
     params: {diameter: 1.2, height: 1.8, initial_level: 0.9, hi_level: 1.6},
     ref: [{sheet: P-001, grid: C2}, {sheet: L-001, grid: B2}],
     layout: {sheet: L-001, x: 3000, y: 4000, elevation: 500}, confidence: read, questions: []}
  - {row: EQ-10, tag: PE-201, type: photo_eye, description: Carton Reject Belt Photo Eye,
     params: {beam_length: 0.6}, ref: [{sheet: L-001, grid: B4}],
     mount: {conveyor: CV-201, sheet: L-001, along: 1200, rail: left}, confidence: read, questions: []}
connections:
  - {row: CN-3, from: TK-101.bottom, to: P-101.suction, line: 25-W-102,
     ref: [{sheet: P-001, grid: D2}], confidence: read, questions: []}
io:
  - {row: IO-4, equipment: LT-101, port: pv, signal: AI, plc_tag: LT101_PV,
     range: [0, 1.8], units: m, ref: [{sheet: IO-001, row: 4}], confidence: read, questions: []}
```

The whole sample is `docs/samples/wash-skid/wash-skid.takeoff.yaml`.

- **Rows.** `equipment`, `connections` and `io`, each with its own `row` id (`EQ-3`, `CN-3`,
  `IO-4`), never reused. An equipment row has the tag on the drawing, a built-in `type` (or
  `unknown`), an ISA-5.1 style `description` and the type's `params` as read. Its scene id is
  the tag with `-` as `_` unless `id:` says otherwise.
- **Where it was read.** `ref` lists every place the row appears: a sheet and its grid zone, or
  a list and its row. An `assumed` row with no `ref` is an item the drawing does not show.
- **Confidence.** `read` (printed on the drawing), `inferred` (deduced from it) or `assumed`
  (not on it).
- **Open questions.** `questions` per row; empty when none.
- **I/O.** Each row names an equipment tag, the type's port and its signal. The scene tag is
  `<ID>_<CODE>_<KIND>` (`LT_101_PV_AI`; `run` and `running` are `RUN`, `detect` adds no code,
  the design note's table gives the rest) unless `scene_tag:` gives one. `plc_tag` is the
  plant's own name ([the scene format](scene-format.md), `plc_tag:`).
- **Nothing in a takeoff is evaluated.** It is read with the safe YAML loader, at most 2 MB, and
  each parameter is a literal value handed to the scene as written.

### Positions as the drawing gives them

A layout sheet carries a `frame`: the `units` its dimensions are printed in (`mm`, `m`, `in`,
`ft`), where its datum is in the scene (`datum`, metres) and how its axes are turned
(`rotation_deg`). A row drawn on it gives its position as printed:

- **`layout: {sheet, x, y, elevation}`**: from the datum, `x` to the right on the sheet, `y` up
  the sheet, `elevation` above the datum, in the sheet's units, for the point the type's
  `position` names in the [component reference](components.md). The tool converts it:
  `scene = datum + rotate_y((x·u, elevation·u, -y·u), rotation_deg)`. At `rotation_deg: 0`
  the sheet's x is the scene's +x and up the sheet is the scene's -z. An optional `heading_deg`
  gives the item's own direction on the sheet (a belt drawn up a turned sheet); its scene
  heading is the frame's rotation plus it.
- **`mount: {conveyor, sheet, along, rail}`**: a device on a belt or roller conveyor's rail,
  `left` or `right` looking from the infeed, `along` from the infeed end in the sheet's units
  (metres with no `sheet`). The scene position is what the editor's drop rule gives
  (`core/mounts.place_on_rail`). An `along` outside the belt is an error, never clamped.
- **`at: [x, y, z]`** in scene metres, for a row on no layout sheet.

### The checked mark

A person checks the takeoff against the drawings and records it with one mark for the file:
`checked: {by, date, drawings: [<sheet>@<rev>, ...], rows: <digest>}`. `rows` is a digest of the
`drawings`, `equipment`, `connections` and `io` lists as YAML loads them, so comments, key order
and flow or block style do not change it, and any changed value does. The mark is **stale** when
a sheet's revision is not the one it names, or when a row changed after checking. Nobody writes
it by hand: `takeoff check --mark INITIALS` does.

## `openfactory takeoff check TAKEOFF`

```bash
openfactory takeoff check docs/samples/wash-skid/wash-skid.takeoff.yaml
openfactory takeoff check docs/samples/wash-skid/wash-skid.takeoff.yaml --strict
openfactory takeoff check my-plant.takeoff.yaml --mark JH
```

It prints the rows by confidence, every finding with its row and its sheet, the open questions
by row, and whether the mark is current. It writes nothing, except with `--mark`. Exit 1 on any
error.

**Errors:** the schema (an unknown key, a wrong value); a duplicate row id, tag, scene id, scene
tag or `plc_tag`; a `ref` to a sheet not in `drawings`; an unknown type (write `unknown` for an
item with no type yet); a connection end that is not an equipment tag, or a port its type does
not have (a tank's `bottom`, `top` and nozzles, a link's pipe ports); an I/O row on a missing
item or port, or whose `signal` is not the port's kind; a `plc_tag` outside the grammar; a
scene tag that is not a valid tag (give `scene_tag:`); `range` on a discrete signal; more than one
of `layout`, `mount` and `at`; a `layout` or `mount` on a sheet not in `drawings` or with no
`frame`; a frame with another unit or a datum that is not three numbers; a `mount` on something
that is not a belt or roller conveyor, of a type not placed on a rail, on a rail other than
`left` or `right`, or with `along` outside the belt.

**Warnings:** `type: unknown` (the scaffold places a stand-in carrying its I/O); an `at:` row
whose `ref` names a layout sheet (the drawing could have given its position).

**`--strict`** also fails on any open question and on a missing or stale mark, saying why:

```text
checked: stale, rows changed since checking
FAIL (--strict): checked: stale, rows changed since checking
```

or `checked: stale, L-001 is rev B, checked at rev A`. It is the gate before a scaffold is shown
to anyone.

**`--mark INITIALS`** writes the mark when there is no error: the initials, today's date, each
sheet with its revision, and the rows digest. It replaces only the `checked:` key (or adds it at
the end), so no other byte of the file changes. It does not need `--strict` to pass: marking is
how a person records that the open questions were read.
