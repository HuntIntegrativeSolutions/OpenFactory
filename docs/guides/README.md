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

Each scene also declares `checks:` (PLAN 2.11): every part reaches its remover, none is lost, and
none takes longer than the scene's `max_cycle_s` from spawn to remover. `openfactory test` grades a
program against them and exits 1 on a failure:

```bash
uv run openfactory test scenes/<scene>.yaml --script examples/pyplc/<scene>.py   # 60 s, fast
uv run openfactory test scenes/<scene>.yaml --plc --bind 0.0.0.0 --analog-encoding int16_scaled
```

With `--plc` the run takes 60 s of real time while your PLC (OpenPLC, as configured above)
controls the plant. What each check means, and why a stopped or jammed line fails, is in
[the scene format reference](../scene-format.md).

`tests/integration/test_shipped_scenes.py` runs every scene for 60 s under its stand-in script and
checks the remover counts each guide states.
