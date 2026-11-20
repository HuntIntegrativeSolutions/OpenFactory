# Layout from topology

A P&ID has no geometry. A takeoff read from P&IDs alone has equipment with no `layout`, `mount`
or `at`, and the pipes `takeoff scaffold` makes from its connection rows have no drawn route.
`openfactory layout` places what no drawing placed, so the scene loads, runs and can be looked
at, and marks every position it gives so nobody takes it for a dimension off a sheet (PLAN 5.5;
the design is in [the design note](research/plants-from-drawings-design.md), section 7).

Training and demo use only. Not a safety-rated simulation. A laid-out plant is a picture of the
topology, not the site's arrangement: its pipe lengths and elevations are layout's, so its flows
are not the drawn plant's (below).

```bash
openfactory layout plant.takeoff.yaml -o plant.yaml   # a takeoff: as takeoff scaffold, laid out
openfactory layout plant.yaml -o plant-2.yaml        # a scene: place again what is marked
openfactory layout plant.yaml --in-place              # the same, written back to plant.yaml
```

On a scene, `layout` writes only when told where: `-o SCENE` (a new file; `--force` to
overwrite one) or `--in-place` (the scene itself). With neither it writes nothing and says which
to give; both together are refused, and so is `--in-place` on a takeoff.

`openfactory takeoff scaffold` lays out the same way; `openfactory layout TAKEOFF` is the same
command under the name of what it does. On a scene, `layout` places again exactly the components
marked `laid_out: true` (and routes the pipes so marked) and changes no other byte of the file:
each change is a splice through the same writer as the editor's.

## What it never moves

- A takeoff row with a `layout`, `mount` or `at` is where the drawing put it. Its body is an
  obstacle that laid-out items keep clear of.
- A scene component without `laid_out: true`.

A general arrangement drawing therefore overrides layout row by row: give a row its `layout`
from the sheet, scaffold again, and that item stops being laid out. A device mounted on a belt
that layout placed (`mount` with `along` from the sheet) follows the belt and is marked laid
out too, since its scene position rests on a laid-out one. When `layout SCENE` runs again, a
marked device standing on a belt keeps its rail and its distance from the belt's infeed, read
back from where it stands, and moves with the belt; it is never spread again.

In the editor, moving or re-typing a laid-out component's position clears its mark: a person
placed it. The mark is a checkbox in the form, and the scene's author may still set it by
hand. The `LAID OUT` comment beside the component stays until someone removes it.

## A plant in several files

On a plant file (one with `include:`, [PLAN 5.4](scene-format.md)), `layout` places only the
plant file's own components marked `laid_out: true`. Every included component is an
obstacle: laid-out items keep clear of it, and it is never moved and its file never written,
marked or not. To lay out an area, run `layout` on the area's own file. Write a plant's result
beside the plant file (`-o` in the same folder, or `--in-place`), since its `include:` paths are
relative to it.

## How it places

It runs in Python, with no browser, and is deterministic: the same takeoff gives the same scene,
byte for byte. Items are taken in row order; nothing is random or iterated in hash order.

| What | Where |
|---|---|
| Process equipment: supplies, valves, tanks, pumps, junctions, drains | One line along +x in flow order along the connections, depth first so each branch stays together (two tanks off one junction are each followed by their own pump): supplies first, drains last; 1.0 m clear between bodies |
| A tank | 0.25 m up, so its bottom nozzle (drawn 0.05 m up) is on the low line |
| A pump, and lines that join a tank's bottom | The low line, 0.3 m up |
| A valve, supply, drain or junction whose connections lead only into one tank's top | That tank's high line, 0.3 m above its top |
| A level transmitter | 0.15 m outside its tank's wall, at half its height (further out if that spot is taken) |
| A flow transmitter | 0.45 m in front of (scene -z) what it measures |
| A heater; a drive | In its tank; 0.8 m behind its load |
| Conveyors | End to end along their connections, heading 0, one row per chain, 2.0 m behind the process line |
| A photo eye, pusher, stopper, sensor or arm with no `mount` | On a belt's left rail, spread evenly in row order; the belt whose tag shares its loop number (PE-201 on CV-201), else the first belt |
| A box emitter; a part remover | At the infeed of a belt nothing feeds; past the discharge of a belt that feeds nothing |
| Anything else (a stand-in, a panel device, scenery) | A row 2.0 m behind the conveyors, on the floor |

With some items placed by a drawing, the laid-out ones start 2.0 m past the drawn ones' largest x.

## It checks what it placed

After placing, layout runs the overlap check (W217, below) over what it placed against
everything, drawn or laid out, and prints every finding. With one or more it **still writes the
scene, says so, and exits 1**, so a script or an assistant cannot miss it. `takeoff scaffold`
and `layout TAKEOFF` check the same way, unless a row is unplaced for a parameter with no
default (the scene cannot load until it is given; the report says the check did not run).

Where it is cheap, layout avoids the overlap first: an instrument or a drive whose spot is
taken steps further out the same way (a flow transmitter further in front, a level transmitter
further from the wall, a drive further behind), 0.2 m at a time, up to 50 times, before the
check. A conveyor that continues a drawn belt cannot step without leaving that belt's
discharge, and the row of other items is placed past every body, so for those the check's
report is the answer: give the row its position from the drawing, or move what is in the way.

## Pipe routes

A valve or pump is drawn as straight runs from its `from` end through its own origin to its `to`
end, so layout puts it level and in line with its neighbours. A pipe from a valve or pump starts
at its own origin, 0.35 m downstream of the link; a pipe from a tank's bottom leaves along the
line through a 0.3 m stub, so the nozzle faces along it. Each pipe gets right-angle `path` points:
the first route, in a fixed order (straight; one axis at a time, x first; over everything; in
front of everything), whose runs pass through no body except those of its own two ends. A pipe no
route clears is drawn straight and reported.

**A pipe's default length is its drawn length** (from its `from` end through its `path` to its
`to` end), and its friction follows its length. So layout changes each routed pipe's length and
friction, and elevation changes static heads. The wash-water sample, with LV-101 at 100 % and
P-101 running, after 120 s:

| Scene | PIPE_101 length | PIPE_102 length | Make-up flow (SUP-101, LV-101) | Transfer flow (P-101) |
|---|---|---|---|---|
| Drawn positions, pipes straight from a midpoint (before PLAN 5.5) | 1.04 m | 0.70 m | 7.99 m³/h | 5.98 m³/h |
| Drawn positions, pipes routed by layout | 0.95 m | 1.05 m | 8.00 m³/h | 5.93 m³/h |
| Every position laid out | 1.65 m | 0.96 m | 7.93 m³/h | 5.86 m³/h |

Give a pipe a `length` to fix its friction whatever its route.

## How it is marked

- In the scene: `laid_out: true` on the component (or composite instance, whose parts take it),
  and a comment `LAID OUT by openfactory layout (PLAN 5.5), not from a drawing: <where>`.
- In scaffold's report: the outcome `laid out`, a positions entry `laid out by openfactory
  layout, not from a drawing (rows)`, and a line for the routed pipes.
- In the [review overlay](review.md) and its table: a laid-out component is drawn dashed
  and listed as laid out.

The mark is not sent to the viewer and is not in a recording.

## The overlap lint

`openfactory lint` warns **W217** for two components whose bodies overlap and for a run of a
link that passes through a body: every component, not only conveyors (W205 stays for two
conveyors). Bodies are the shapes the viewer draws, in three dimensions, so a supply above a
tank's top does not overlap it. Not a finding: a device that works on a conveyor touching it, a
panel device on a body or cabinet, and a component touching what it names (a link's ends, a
transmitter's measured item, a heater's tank, a drive's load). Layout and the lint use the same
bodies (`openfactory.core.footprints`).
