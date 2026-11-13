# Scene editor

`openfactory edit SCENE` opens a scene in the viewer in **edit mode**: the simulation does not run,
no PLC is connected, and the server listens on loopback only (PLAN 4.1). You change the scene in
the browser; the server applies each change to the YAML text, so every comment, blank line and key
order outside the edited lines stays as it was.

```bash
uv run openfactory edit scenes/sorting-by-height.yaml      # then open http://127.0.0.1:8080/
```

Training and demo use only. Not a safety-rated simulation.

## Selecting and changing values

Click a component to select it; it is outlined in blue and its properties appear in the panel on
the right, in a form built from the scene schema. A part of a composite selects the composite
instance, which is what the file holds. A value the schema refuses (a negative speed, a tag
without its `_DI` suffix) is marked in the form and never sent.

Each accepted change is **previewed**: the server applies all pending changes to the file as it
was when you opened it, runs `openfactory lint` on the result, and the plant is redrawn from it.
The lint's findings are listed under the form. Nothing is written until you press **Save**.

An I/O binding written as a mapping (`speed: {tag: CONV_01_SPD_AO, initial: 100}`) is edited as
one line of YAML.

## Adding, moving, turning and deleting

- **Add:** pick a type in the palette and press **Add**. The palette lists every built-in type,
  the composite types the scene declares, and every library composite in a `_library/` folder
  beside the scene or above it, up to the project root (the rule is in
  [the scene format](scene-format.md), "Library composites"). The new component is placed on the floor at the centre of the
  view and selected.
  - Its id is the type's prefix and the next free number (`PE_02`). Every required I/O port gets
    a proposed tag (`PE_02_DI`, `CONV_03_RUN_DO`), which you can rename in the form.
  - A library composite also adds `{library: name}` to the scene's `composites:` block.
  - Some types name other components (a pipe's `from` and `to`, a heater's `tank`). Where the
    scene has an obvious one it is proposed; otherwise the field is left empty, the preview says
    the scene does not load, and you fill it in.
  - A new component starts with a placeholder description. **Save and Download stay disabled
    until each new component has its own description.**
- **Move and Turn:** with a component selected, **Move** shows arrows that drag it along the
  floor, and **Turn** shows a ring that turns it about the vertical axis. Moves snap to a 0.05 m
  grid and turns to 15°; hold **Shift** while dragging for free placement. A composite instance
  moves as one.
- **Delete** (or the Delete key) removes the selection, with the comment lines directly above it
  in the file. A component that other components name (a connection, a pipe end) leaves the
  scene unloadable; the preview says which reference to fix.

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
and rotation can then be set. Picking "(none)" removes the mesh. See `mesh` in
[the scene format](scene-format.md).

## Save, Revert, Download YAML

- **Save** writes the file. It is refused if the file changed on disk since you opened it (reload
  the page), or if the lint reports an error.
- **Revert** drops every pending change.
- **Download YAML** gives the scene text with the pending changes, without writing the file.

The editor never writes the Modbus lockfiles (`*.modbus.yaml`). New tags get their addresses the
next time you `run` or `export` the scene ([ADR-0004](adr/ADR-0004-modbus-address-lockfile.md)).

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

Scripts and tools that send no `Origin` header (curl, Python) are not a browser on another site
and are served as before. `tests/unit/test_cross_origin.py` checks each of these.

## Not yet

Renaming a component, and deleting the references to a deleted one with it, are not in the
editor yet. Snapping conveyor ends together is PLAN 4.1d.
