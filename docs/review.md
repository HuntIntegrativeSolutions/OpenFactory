# Review overlay

Before anyone trusts a scene built from drawings, a person checks it against them. `openfactory
review` draws the scene's top view over the layout drawing and lists every takeoff row against
the scene, so whatever was missed, added or laid out shows (PLAN 5.6; the design is in
[the design note](research/plants-from-drawings-design.md), section 8).

Training and demo use only. Not a safety-rated simulation.

```bash
openfactory review wash-skid.takeoff.yaml wash-skid.yaml --drawing L-001.png \
    --datum-px 120,930 --px-per-unit 0.14 --png
```

It writes three files, named after the scene: `wash-skid.review.md` (the table),
`wash-skid.review.html` (the overlay and the table, to open in a browser) and, with `--png`,
`wash-skid.review.png` (the overlay as a picture, drawn by headless Chromium; it needs
Playwright, the development extra).

## Private, like the drawing

**The overlay and its table are as private as the drawing**: the page carries the drawing
itself. They are written beside the takeoff, or to the folder `--out` names, and **never into
the repository by default**: `review` refuses a folder inside the OpenFactory repository unless
`--in-repo` is given. Keep a customer's review with the customer's drawings, in the private
project folder (Milestone 5, rule 1).

## The drawing is loaded as an image only

- **PNG or SVG**, by the file's suffix and its first bytes. Any other type is refused by name
  ("PDF is not a drawing review loads; give a PNG or SVG"); export the sheet to PNG or SVG
  first.
- The page holds the drawing's bytes as a `data:` URL in an image and draws it on a canvas. It is
  **never inlined as markup**, so nothing in a drawing file can run: an SVG's scripts do not run
  in an image.
- **Nothing is fetched.** The drawing's path is the one given on the command line, a URL is
  refused, and the page's content security policy lets it load nothing but its own image.

## Lining the scene up with the sheet

The sheet's `frame` in the takeoff (units, the datum's place in the scene, rotation) turns scene
metres into the sheet's dimensions. Where the datum is in the picture, and how many pixels make
one sheet unit, is not in the frame, so you give both: `--datum-px X,Y` (pixels from the
picture's top-left, y down) and `--px-per-unit S`. Read them off the picture: the datum's mark,
and two dimension lines a known distance apart. The sample sheet L-001 has its datum at
(120, 930) and 0.14 pixels per millimetre. With more than one layout sheet, `--sheet` names it.

On the sample, every item's outline sits on the symbol the sheet draws for it: each centre and
the tank's radius within 1 mm, the belt's ends within 1 mm and its sides 30 mm outside the drawn
belt, which is the belt's rails (the sheet draws the belt alone). `tests/unit/test_review.py`
measures it against the sheet's own symbols.

## What the picture shows

| Drawn | Means |
|---|---|
| Green outline | in the scene and in the takeoff, placed from the drawing |
| Dashed grey outline | laid out by [`openfactory layout`](layout.md), not from a drawing: never read it as a dimension off the sheet |
| Blue outline and its id | in the scene with no takeoff row |
| Dashed red outline, a red ring and `MISSING <tag>` | a takeoff row with no component in the scene, where the takeoff puts it |

Each outline is the body the viewer draws (the same bodies the overlap lint W217 uses), seen
from above; pipes and the runs of valves and pumps are drawn as lines.

## The table

Every takeoff row, in order, then every scene component no row accounts for:

| Status | For |
|---|---|
| `present` | an equipment row whose component is in the scene (a composite instance by its parts); a connection row whose pipe, link end or conveyor connection is there; an I/O row whose scene tag its component carries |
| `missing` | a row with nothing in the scene for it |
| `not made` | a connection scaffold could not make (a conveyor to a tank) |
| `in the scene with no row` | a component no takeoff row names |

A present equipment row says where its position came from: the sheet's dimensions, a rail, `at`
in scene metres, or laid out by `openfactory layout`. For one placed from the drawing, the last
column says how far the scene has it from where the takeoff puts it, in millimetres, so a
component moved in the editor after scaffolding shows too.
