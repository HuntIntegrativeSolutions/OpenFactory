# Scene editor

`openfactory edit SCENE` opens a scene in the viewer in **edit mode**: the simulation does not run,
no PLC is connected, and the server listens on loopback only (PLAN 4.1). You change the scene in
the browser; the server applies each change to the YAML text, so every comment, blank line and key
order outside the edited lines stays as it was.

```bash
uv run openfactory edit scenes/sorting-by-height.yaml      # then open http://127.0.0.1:8080/
```

Training and demo use only. Not a safety-rated simulation.

## Starting a new scene

```bash
uv run openfactory new scenes/my-line.yaml                 # writes it, then opens the editor
uv run openfactory new scenes/my-line.yaml --no-edit       # only writes it
```

`new` writes a scene named after the file (lower case letters, digits and hyphens) with the
usual header, a Modbus TCP server `plc:` block and one belt conveyor, `CONV_01`, at the origin
(`--first TYPE` starts with another type). A scene needs at least one component to load. The
scene's description and the conveyor's are placeholders (`New scene`, `New belt conveyor`), and
Save stays disabled until each has its own. `new` never overwrites a file.

`scenes/inline-reject.yaml` was built this way, without the file ever being opened
([its guide](guides/inline-reject.md)).

## Selecting and changing values

Click a component to select it; it is outlined in blue and its properties appear in the panel on
the right, in a form built from the scene schema. A part of a composite selects the composite
instance, which is what the file holds. A value the schema refuses (a negative speed, a tag
without its `_DI` suffix) is marked in the form and never sent.

Each accepted change is **previewed**: the server applies all pending changes to the file as it
was when you opened it, runs `openfactory lint` on the result, and the plant is redrawn from it.
The lint's findings are listed under the form. Nothing is written until you press **Save**.

A field commits when you leave it (Tab, or a click elsewhere). If what you typed is the value the
field already has (`3` in a length of 3, or `0.50` for 0.5), the field says **✓ Kept: that is
already its value** and nothing is sent (PLAN 4.1j).

**Lists and mappings** have their own controls, built from the schema (PLAN 4.1j):

- A list (an emitter's `parts`, a pipe's `path`, a drain's `schedule`, a check's `never_dry`)
  shows each item numbered, with its own fields (a part's `name`, `size`, `weight` and `color`),
  a **×** to remove it, and **Add item** at the end. A new item starts empty and is sent once
  every field it needs is filled; until then the list is marked **Not sent yet** with what is
  missing, and the item stays in the form while you change other values, until you finish it,
  remove it or press Revert.
- A mapping (a check's `parts_reached`, a tank's `nozzles`, `level_within`, `max_starts`) shows
  each entry's key above its value, a **×** to remove it, and a **new key** box with **Add** at
  the end. Renaming a key keeps the entry's place. A key another entry already has, or an empty
  one, is refused, for a rename as for Add: the box goes back to its name and says why (two
  entries with one key would be written as one).
- A value that may be one item or several (a `parts_reached` remover's part names) is a list
  that writes a single item bare: `{REM_01: oversize}`, or `{REM_01: [good, tall]}` with two.
- A value that may be one of a few kinds (a tank's nozzle: a height, or a height and a bearing,
  PLAN 4.1g2; a mesh's `scale`: one number or three) has a choice above its control; changing it
  keeps what it can (a nozzle's height stays when a bearing is added).

The whole list or mapping is written as one change: in flow style, or, where the file writes it
as a block (one item per line), as a block again with each item on its own line in flow style
(comment lines between its old items go with them). An I/O binding
written as a mapping (`speed: {tag: CONV_01_SPD_AO, initial: 100}`) is still edited as one line of
YAML, and so is the one value the schema does not describe item by item: a composite instance's
`io`.

Every I/O port is listed, bound or not. An optional port that is not bound (a conveyor's
`running` feedback, a pusher's `extended` limit, an emitter's `enable`) has a **Use TAG** button
with the tag the palette would propose (`CONV_01_RUNNING_DI`; `ID_KIND` for a type with one
port, `EMT_01_DO`): one click binds it. Type another name in the field instead to use that. The
first binding on a component with no `io:` adds `io: {port: TAG}`.

## The scene's own settings and checks

With nothing selected, the panel shows the scene's settings (`scene:` name, description, author,
seed, friction) and its [`checks:`](scene-format.md) (`parts_reached`, `none_lost`,
`max_cycle_s`, the process checks), as forms like a component's. A check set in a scene with no
`checks:` block adds one just above `components:`.

## Adding, moving, turning and deleting

- **Add:** pick a type in the palette and press **Add**. The palette lists every built-in type,
  the composite types the scene declares, and every library composite in a `_library/` folder
  beside the scene or above it, up to the project root (the rule is in
  [the scene format](scene-format.md), "Library composites"). The new component is dropped on the floor at the centre of the
  view, drawn, then placed (PLAN 4.1e):
  - Where its drawn footprint, with 5 cm around it, overlaps nothing, it stays there. Otherwise
    it moves to the nearest spot on the floor (searched in 0.1 m steps up to 15 m away, never
    past the floor's 20 m edge) where it overlaps nothing. If that puts any of it outside the
    view, the view refits to the whole plant, as **Fit view** does (PLAN 4.1j).
  - A conveyor whose end lands within **1 m** of an open end of another conveyor (an end no
    other conveyor's end meets) snaps to it as a drag does (see "Snapping conveyor ends
    together" below): the ends meet and face, the new conveyor takes that end's height, and the
    connection is added. If that place is blocked by anything but the conveyor it joins, it is
    placed clear instead.
  - A component that cannot be drawn yet (a pipe without its ends, a heater without a tank) is
    placed when a later change first lets it be drawn. A pipe is never moved: it runs between
    its ends.
  - Its id is the type's prefix and the next free number (`PE_02`). Every required I/O port gets
    a proposed tag (`PE_02_DI`, `CONV_03_RUN_DO`), which you can rename in the form.
  - A library composite also adds `{library: name}` to the scene's `composites:` block.
  - Some types name other components (a pipe's `from` and `to`, a heater's `tank`). Where the
    scene has an obvious one it is proposed; otherwise the field is left empty, the preview says
    the scene does not load, and you fill it in.
  - A new component starts with a placeholder description. **Save and Download stay disabled
    until each new component has its own description.**
- **Fit view** (beside Add) frames the whole plant in the part of the window the panel does not
  cover. Otherwise the view stays where you put it: changing a value never moves the camera.
- **Move and Turn:** with a component selected, **Move** shows arrows that drag it along the
  floor, and **Turn** shows a ring that turns it about the vertical axis. Moves snap to a 0.05 m
  grid and turns to 15°; hold **Shift** while dragging for free placement. A composite instance
  moves as one.
- **Delete** (or the Delete key) removes the selection, with the comment lines directly above it
  in the file (PLAN 4.1f). When anything else in the scene names it, the panel first lists what
  goes with it and what is left to fix, and nothing changes until you press **Delete ID** (or
  **Cancel**):
  - **Removed with it:** each `connections:` entry naming it, and each `checks:` entry naming
    it (a `parts_reached` remover, a tank's `level_within`, a pump in `never_dry`...). A check
    left empty goes too, and so does `checks:` or `connections:` when nothing is left in it. A
    composite instance takes the references to its parts (`BUF_BELT`) with it.
  - **Left for you to fix:** references a component cannot work without: a pipe's, valve's or
    pump's `from`/`to`, a heater's `tank`, a transmitter's `measures`, a VFD's `drives`, and a
    `from` entry of an I/O binding that combines its port. They stay as written; the preview
    says the scene does not load and names each one, and Save stays refused until you change it.

### Gizmo colors

The move and turn handles use colors outside the ISA-101 status table, so a handle never reads
as running, stopped, an alarm or the selection highlight. Each differs from every color in that
table by more than 40 levels (of 255) in at least one channel; `web/src/editor/gizmo.test.ts`
checks every handle against every status color.

| Handle | Color |
|---|---|
| Move along the floor's x axis | `#D946EF` (fuchsia) |
| Turn about the vertical axis | `#A78BFA` (violet) |
| Move along the floor's z axis | `#22D3EE` (cyan) |
| A handle under the pointer or being dragged | `#F0ABFC` (light pink) |
| Neutral handles (centre, free move) | `#C4B5FD` (lavender) |
| The turn gizmo's background disc | `#7C6F9F` (grey violet) |

## Meshes

The **mesh** box in the form lists the `.glb` files in the scene's folder and up to two folders
below it. Picking one sets `mesh.file`, and the custom housing is drawn at once; its scale, offset
and rotation can then be set. Picking "(none)" removes the mesh. A mesh must be a file inside
the scene's folder: a path with `..` or an absolute path does not load (E100), and a name that is
a symlink to a file outside is refused (E104). Either way the preview says why, Save stays
refused, and the file is never served. See `mesh` in [the scene format](scene-format.md).

## Save, Revert, Download YAML

- **Save** writes the file. It is refused if the file changed on disk since you opened it (reload
  the page), or if the lint reports an error.
- **Revert** drops every pending change.
- **Download YAML** gives the scene text with the pending changes, without writing the file.

The editor never writes the Modbus lockfiles (`*.modbus.yaml`). New tags get their addresses the
next time you `run` or `export` the scene ([ADR-0004](adr/ADR-0004-modbus-address-lockfile.md)).

## A plant in several files

A plant file (`include:`, see [the scene format](scene-format.md)) is edited one file at a time,
with the whole plant shown around it (PLAN 5.4e):

```bash
openfactory edit scenes/plants/wash-plant/wash-plant.yaml --area WASH   # edits areas/wash.yaml
openfactory edit scenes/plants/wash-plant/wash-plant.yaml               # edits the plant file
```

- `--area NAME` chooses the file: that area's, as the plant's `include:` names it. Without it, the
  plant file itself is edited: its own components and its connections across areas. An unknown
  area is an error naming the plant's areas.
- Every other file's components are drawn **dimmed**. Selecting one shows its file and the command
  that edits it, "in areas/load.yaml: edit it with `openfactory edit wash-plant.yaml --area LOAD`"
  (the plant file's own components: "in wash-plant.yaml: edit it with `openfactory edit
  wash-plant.yaml`"), instead of its form: it cannot be moved, turned, deleted or changed
  from here, and a conveyor you move does not snap to it or drop devices on its rails (a
  connection across areas is written in the plant file's `connections:`).
- The preview is the plant with your unsaved text in your file's place, and the lint runs on the
  **whole plant**. An id, a tag or a `plc_tag` another file already has is an error naming that
  file, and Save stays refused until it is changed. A save that would make the plant fail to load
  (a component the plant's cross-area connection needs, deleted) is refused with the loader's
  message, and nothing is written.
- **Save writes only the file you are editing.** Nothing else is written: not the plant's other
  files, not its lockfile, not its `CONTEXT.md`. A component added from the palette is numbered
  past every id in the plant.

## Other web pages cannot change your scene

The editor server writes only the one scene file it was opened on, refuses a save when the file
changed on disk since you opened it, and listens on loopback only. A web page from another site
open in the same browser cannot use it to change the scene (PLAN 4.1c fix 6):

- No response carries CORS headers, so a page from another origin cannot read any answer.
- Every request that changes something (`POST /api/edit/preview`, `/save`, `/new`, `/palette`,
  and in a run `POST` or `DELETE /api/fault` and `POST /api/exam`) is refused with **403** when
  its `Origin` header is not the server's own (the scheme, host and port the browser addressed).
- A `POST` whose body is not `application/json` is refused with **415**. A page on another site
  can send a form or a `text/plain` body without the browser asking first, but not JSON.

- The viewer's event socket (`/ws/events`, buttons and sensor events in a run) is refused when its
  `Origin` is not the server's own.
- A server bound to loopback answers only when the browser addressed it by a loopback name or
  address (`localhost`, `127.0.0.1`, `[::1]`): a request or socket with any other `Host` gets
  **421**. This stops DNS rebinding, where another site re-points its own name at 127.0.0.1.

Scripts and tools that send no `Origin` header (curl, Python) are not a browser on another site
and are served as before. `tests/unit/test_cross_origin.py` checks each of these.

## Snapping conveyor ends together

Each conveyor (`belt_conveyor`, `roller_conveyor`, `turntable`, `lift`) has two **snap ports**,
its ends, listed in [the component reference](components.md). While you **Move** a conveyor, an
end that comes within **0.15 m** of another conveyor's end it can join (a discharge to an infeed,
at the same height) is marked: a ring on the end it will meet and a dot on your conveyor's end,
in the gizmo's light pink, never a status color. Release there and the conveyor is placed so the
two ends meet within 1 mm and face each other within 0.1 degrees (it is turned if it was a
little off), and the connection `{from: A, to: B}` is added to the scene's `connections:`. The
preview shows both, and Save writes them. Away from any end, a move stays on the 0.05 m grid.

The lint checks the same ports: **W212** warns when two ends meet (within 2 cm, at any heading)
and no connection joins them, and **W213** when a connection's ends are more than 1 cm apart, out
of level, or more than 1 degree from facing. A scene snapped in the editor has neither.

## Dropping a device on a rail

A belt or roller conveyor has two side rails, `rail_left` and `rail_right` seen looking along the
flow ([the component reference](components.md)); a turntable's or a lift's deck has none. While
you **Move** a photo eye, distance or color sensor, pusher, stopper or diverter arm over a belt or
within **0.15 m** beside it, the nearer rail is marked (a ring where it will go, in the gizmo's
light pink). Release there and it is placed on that rail, where you dropped it along the belt (on
the 0.05 m grid from the infeed end; **Shift** for free), by one rule per type (PLAN 4.1g1):

| Type | Placed |
|---|---|
| `photo_eye` | the beam's midpoint over the centre line, the beam across the belt; its height above the belt kept if 0.02 to 1 m, else 0.1 m |
| `distance_sensor` (looking across) | its face 0.05 m outside the rail, facing across; height kept as an eye's |
| `color_sensor`, `distance_sensor` (looking down) | over the centre line, height kept as an eye's |
| `pusher` | its retracted face 0.05 m outside the rail, extending across; the plate's bottom 0.05 m above the belt (with `plate_height` unset, at the height that makes the sized plate run from 0.05 m to 3/4 of the tallest part) |
| `stopper` | on the centre line at belt-top height, stopping parts that move with the flow |
| `diverter_arm` | its pivot on the rail at belt-top height, at rest along the flow; `swing_deg` changes sign if needed so it swings over the belt |

The editor chooses only the rail and the distance along it; the placement comes from the server
(`POST /api/edit/mount`), whose rules are the same ones a drawing takeoff will use. The move
writes `position`, `rotation_deg` and, for a diverter, `swing_deg`; nothing about the mount is
stored, and moving the belt later does not move its devices. A device dropped away from every
belt stays where it was dropped, on the grid.

The lint checks the result: **W214** warns when a photo eye's beam or a stopper's blade on a belt
or roller conveyor stops more than 1 mm short of either rail, or a diverter's tip short of the
rail across from its pivot. A pusher's stroke is W207's.

## Not yet

Renaming a component is not in the editor yet. Snapping a pipe's ends to a tank's nozzles and to
other links (PLAN 4.1g3) is parked.
