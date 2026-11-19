# Looking at the plant

While a PLC program is being written, the person at the viewer and an assistant reading text should
see the same plant at the same moment. Two read-only tools give the text side of that (PLAN 5.14a
and 5.14b). Neither needs a browser.

- **`openfactory look`** prints the plant in words.
- **`GET /api/iolog`** lists every tag change, with its tick, its time and who wrote it.

The picture side starts with [aimed views and a plan view](#aimed-views-and-a-plan-view)
(PLAN 5.14c): an address that opens the viewer on one component, on an exact camera, or straight
down on the whole plant.

OpenFactory is a training and demo tool, not a safety-rated simulation.

## `openfactory look`

`look` writes one Markdown report of the plant at one tick:

1. The scene in one line: its name, the simulated time and tick, its description, and how many
   components and parts it has.
2. **Checks.** Each check the scene declares, in plain words, with what the plant shows of it
   now: each remover's count of each part name, the oldest part's age, or a tank's level.
3. **Active faults.** Each component with an injected fault. In exam mode the section says
   only "Faults are hidden in exam mode.": the report shows a trainee nothing the viewer's state
   hides, such as a misaligned eye's real offset.
4. **Components.** One sentence per component, with units, from its own type, then each bound
   tag and its value. Tags that no component owns, such as the simulator heartbeat, come last.
5. **Parts.** Each part's id and name, the conveyor or deck carrying it, how far it is from that
   carrier's infeed end, and its speed. A part carried by nothing is held by a gripper, falling,
   or at rest, with its position.
6. **Changes in the last 5 s** (set the window with `--since S`). Each DI and DO change is
   listed with its time and tick. Each AI and AO tag gets one line: its first and last value and
   how many times it changed, so a process scene whose analog inputs move every tick still fits.
   A DI or DO that changed more than four times in the window, such as the heartbeat, also gets
   one line. If the window reaches back past what the live change log still holds (below), the
   heading gives the time the listing really starts from and says older changes are no longer
   held.

`--component ID` narrows the report to one component: its sentence and tags, what it is connected
to, the parts it carries, and its own tags' changes. What it is connected to covers conveyor
connections, pipe and valve ends, references such as a transmitter's `measures`, and the devices
standing on a belt.

### From a running scene

```bash
openfactory run scenes/sorting-by-height.yaml --script examples/pyplc/sorting-by-height.py
openfactory look                          # in a second terminal
openfactory look --component CYL_01 --since 2
```

`look` asks the running server for `GET /api/look`. The server is the `[web]` endpoint of
`openfactory.toml`, `http://127.0.0.1:8000` by default; `--url` names another. The endpoint
returns the same report as `text/markdown`, takes `since` and `component` as query parameters,
and answers 404 for an unknown component. Like every OpenFactory endpoint, it listens on
loopback unless the run was started with `--bind`.

### From a recording at a time

```bash
openfactory run scenes/sorting-by-height.yaml --script examples/pyplc/sorting-by-height.py \
    --as-fast-as-possible --duration 60 --record sorter.ofrec
openfactory look sorter.ofrec --at 6.0
```

A recording holds a full state frame every 5th tick (10 Hz at the default 50 Hz), so `--at T`
reports the frame at or before T. Without `--at` it reports the recording's last frame. Each demo
kit's recording works too: `openfactory look kits/sorting-by-height/sorting-by-height.ofrec --at
6.0`.

A live run and its own recording give the same text at the same tick, which a test checks
(`tests/unit/test_look.py`). The report reads no clock and names no path, host or port.

### What the report does not say

- **Whether a check has passed.** That is graded once, at the scene's `duration_s`, by
  `openfactory test` (or by the viewer's checks panel, `GET /api/checks`, live). Grading needs
  the run's removal log, its lost parts and a pump's starts. A snapshot does not hold them, and a
  recording holds no grades. `look` says what each check asks and what the plant shows of it now.
- **Why something happened.** That is `openfactory explain`, a later task (PLAN 5.14g).

### Part of a report

Here is the sorter at 6.0 s, from its kit recording. The whole report is 41 lines and is a test's
golden file (`tests/golden/look-sorting-by-height-6.0s.md`):

```text
# sorting-by-height at t = 6.00 s (tick 300)

Two-lane box sorter. Tall boxes diverted by pusher, short boxes pass through. 7 components, 2 parts in the scene.

## Components

- `CONV_01` (belt_conveyor): running at 0.50 m/s.
  CONV_01_RUN_DO = true, CONV_01_SPD_AO = 100.000, CONV_01_RUN_DI = true
- `PE_01` (photo_eye): beam clear, detect off.
  PE_01_DI = false
- `CYL_01` (pusher): retracted, plate 0.00 m out of 0.60 m.
  CYL_01_EXT_DO = false, CYL_01_EXT_DI = false, CYL_01_RET_DI = true

## Parts

- `P000001` short, on CONV_01, 2.58 m from the infeed end, moving 0.50 m/s
- `P000002` tall, on CONV_01, 1.11 m from the infeed end, moving 0.50 m/s
```

## `GET /api/iolog`

A live run keeps every tag change of its last 600 s of simulated time, up to 20,000 changes,
whichever is fewer. `GET /api/iolog` lists them in the order they happened. Each change has its
tick, its simulated time, the tag, the new value and its writer: `simulation` for a DI or AI,
`plc` for a DO or AO. Two query parameters narrow it:

- `since_tick=N` keeps only the changes after tick N;
- `tags=A,B` keeps only those tags.

The response also has these fields:

- `tick`: the last tick logged;
- `first_tick`: the oldest change still held;
- `complete`: false when the cap has already dropped a change after `since_tick`.

A replay (`openfactory replay`) serves the same endpoint from the recording, up to the moment it
is showing. `openfactory replay FILE.ofrec --io` prints a whole recording's changes as a table.

Each scene's generated `CONTEXT.md` lists the two `look` commands for that scene under
"Looking at the plant".

## Aimed views and a plan view

Three address parameters choose where the viewer looks when it opens (PLAN 5.14c). They work on
any page the viewer serves (`openfactory run`, `replay`, a kit), next to `?quality=`.

- **`?look=CYL_01`** frames one component and what touches it: a component a `connections:`
  entry joins it to, and a pipe, valve or pump whose drawn end is on it (or on whose end it is).
  The view comes from the usual direction, as close as keeps all of them on screen, with 30 %
  more room around them. `?look=TK_201` on the sample plant shows the tank, its make-up valve
  with the line from the supply, and its pump with the line to the drain.
- **`?view=px,py,pz,tx,ty,tz`** puts the camera at (px, py, pz) looking at (tx, ty, tz), in metres
  in the scene's frame.
- **`?view=top`** is a plan: straight down, with no perspective, so 1 m is the same length
  everywhere on the screen. North is up and +x is to the right; north is the scene's -z, the way
  positive `rotation_deg` turns ([the component reference](components.md)). A scale bar at the
  bottom left gives 1, 2 or 5 times a power of ten metres and follows the zoom. Drag to pan and
  scroll to zoom; it does not orbit. The editor keeps its 3D view (`?view=top` is ignored there,
  with a warning).

A `?view=` value is six finite numbers (with the position and the target apart) or the word
`top`. Anything else, and a `?look=` naming no component, opens the default view and writes a
warning to the browser's console; it never stops the page. With both, `?view=` wins.

**Copy view** (bottom left) copies the view on screen as `view=...`: paste it after `?` in the
address, or give it to the screenshot tool. In the plan view it copies `view=top`.

`tools/screenshot.py` takes the same: `--look-at ID`, `--view px,py,pz,tx,ty,tz` and `--view
top`, for example
`uv run python tools/screenshot.py scenes/sorting-by-height.yaml --script examples/pyplc/sorting-by-height.py --view top`.
