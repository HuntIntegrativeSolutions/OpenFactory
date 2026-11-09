# Scene guides

One guide per shipped scene in `scenes/` (PLAN 2.7). Each scene comes with both Modbus address
lockfiles (`float32` and `int16_scaled`, ADR-0004), an OpenPLC program in `examples/openplc/`, and
a stand-in Python PLC in `examples/pyplc/` that runs the same sequence with no PLC installed
(PLAN 2.10). Start with the OpenPLC quickstart: it sets up the container once, and every other
guide refers back to it.

OpenFactory is a training and demo tool. It is not a safety-rated simulation, and none of these
programs is a safety-rated program.

| Scene | Guide | What it shows | Components |
|---|---|---|---|
| `sorting-by-height` | [OpenPLC quickstart](openplc-quickstart.md) | A photo eye above short boxes picks out tall ones; a timed pusher diverts them | belt conveyor, photo eye, pusher, emitter, removers |
| `sorting-by-color` | [Sorting by color](sorting-by-color.md) | A color sensor taught red and a diverter arm that slides red boxes onto a side lane, counting boxes in and out of the arm's section | belt conveyors, color sensor, diverter arm, photo eye |
| `conveyor-start-stop` | [Conveyor start/stop](conveyor-start-stop.md) | Hello world: a seal-in start/stop circuit with an NC stop button and a running light | belt conveyor, selector switch, pushbutton, pilot light |
| `buffer-and-release` | [Buffer and release](buffer-and-release.md) | Boxes queue against a stopper and leave in counted batches of three; a faster outfeed opens the gaps the count eye needs | belt conveyors, stopper, photo eyes |
| `two-lane-merge` | [Two-lane merge](two-lane-merge.md) | Two lanes merged through a turntable that turns to take side-lane boxes, alternating lanes; spring-raised stoppers | belt conveyors, turntable, stoppers, photo eyes |
| `palletizer-lite` | [Palletizer lite](palletizer-lite.md) | A gantry forms single layers of four on a layer-forming belt; a nine-step pick sequence and a placement count | belt conveyors, pick and place, stoppers, photo eyes |
| `tank-level-onoff` | [Tank level, on/off](tank-level-onoff.md) | A pump started at a low level switch and stopped at a high one keeps a head tank filled; water circulates through a hand valve | tanks, pump, pipes, hand valve |
| `tank-level-pid` | [Tank level, PID](tank-level-pid.md) | A PI loop through OpenPLC's PID block holds a tank's level through a control valve across a load step | supply, control valve, tank, level transmitter, consumer |
| `two-tank-cascade` | [Two tanks in series, cascade](two-tank-cascade.md) | Cascade level control: an outer PI on the lower tank sets an inner PI on the upper tank, across a load step | supply, control valve, two tanks, gravity line, transmitters, consumer |
| `batch-mix-heat` | [Batch mix and heat](batch-mix-heat.md) | A batch sequence: water and a warm syrup filled to a level each, heated to 50 °C and held, graded on the recipe and the temperature | two supplies, on/off valves, tank, level and temperature transmitters, heater, pilot light |

Every scene runs the same three ways:

```bash
# stand-in PLC, no PLC needed (add --as-fast-as-possible --duration 60 for a headless check)
uv run openfactory run scenes/<scene>.yaml --script examples/pyplc/<scene>.py

# OpenPLC: export the I/O list, configure OpenPLC (quickstart, step 6b), then run
uv run openfactory export scenes/<scene>.yaml --analog-encoding int16_scaled -o <scene>-int16.csv
uv run python tools/openplc_configure.py --program examples/openplc/<scene>.st \
    --csv <scene>-int16.csv --device-ip <WSL host IP>
uv run openfactory run scenes/<scene>.yaml --bind 0.0.0.0 --analog-encoding int16_scaled

# any other Modbus TCP PLC: the I/O list CSV has every address (float32 or int16_scaled)
uv run openfactory export scenes/<scene>.yaml -o <scene>.csv
```

Each scene also declares `checks:` (PLAN 2.11). In the conveyor scenes, every part reaches its
remover, none is lost, and none takes longer than the scene's `max_cycle_s` from spawn to
remover. In the process scenes (PLAN 3.3) a tank's level stays in its band once the loop has
settled, no tank overflows, no pump runs dry, a pump does not short-cycle, a control valve does
not hunt, and a batch holds its recipe and temperature. `openfactory test`
grades a program against them and exits 1 on a failure:

```bash
uv run openfactory test scenes/<scene>.yaml --script examples/pyplc/<scene>.py   # 60 s, fast
uv run openfactory test scenes/<scene>.yaml --plc --bind 0.0.0.0 --analog-encoding int16_scaled
```

With `--plc` the run takes 60 s of real time while your PLC (OpenPLC, as configured above)
controls the plant. What each check means, and why a stopped or jammed line fails, is in
[the scene format reference](../scene-format.md).

`tests/integration/test_shipped_scenes.py` runs every scene for 60 s under its stand-in script and
checks the remover counts each guide states.

## Send a scene as one file

A scene with meshes, lockfiles and a script is several files; a bundle is one (PLAN 2.18):

```bash
uv run openfactory bundle scenes/<scene>.yaml           # writes <scene>.ofz here
uv run openfactory validate <scene>.ofz
uv run openfactory test <scene>.ofz --bundled-script    # grade it with the script it carries
uv run openfactory run <scene>.ofz                      # a PLC drives it; the script stays off
```

The bundled script is Python: `--bundled-script` runs it, so give that flag only to a bundle you
trust. `bundle --no-script` leaves the script out. Details: `docs/scene-format.md`, "Bundles".

## Record a run, replay it anywhere

`--record` on `run` or `test` writes the whole run, scene included, to one file; anyone can replay
it in the viewer with no scene file and no PLC (PLAN 2.12). Attach it to a bug report, or use it for
a screenshot that comes out the same every time:

```bash
uv run openfactory test scenes/<scene>.yaml --plc --record run.ofrec   # or run ... --record
uv run openfactory replay run.ofrec                 # open http://127.0.0.1:8000/ (REPLAY on the HUD)
uv run openfactory replay run.ofrec --speed 4 --loop
uv run openfactory replay run.ofrec --io            # every I/O change with its time and tick
```

A recording holds simulated time only and nothing about your machine (no paths, host names or
tokens), so two recordings of the same scripted run are identical byte for byte. A 60 s sorter
run is about 40 KB. Recordings are not committed to the repository (CLAUDE.md, 1 MB rule),
except the one in each demo kit (`kits/`, PLAN 2.14).

## Studio 5000 tags and Ignition

Every scene exports controller tag stubs for Studio 5000 and Ignition 8.3 tags, and `run --opcua`
serves the I/O table over OPC UA for an Ignition HMI (PLAN 2.3, 2.4; details in
[docs/ignition.md](../ignition.md)). Both files are for training and demos and need PE review
before any client use.

```bash
uv run openfactory export scenes/<scene>.yaml --format l5x -o <scene>-tags.L5X
uv run openfactory export scenes/<scene>.yaml --format ignition -o <scene>-tags.json
uv run openfactory run scenes/<scene>.yaml --opcua      # opc.tcp://127.0.0.1:4840/openfactory/
```
