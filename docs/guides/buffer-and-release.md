# Buffer and release

`scenes/buffer-and-release.yaml` accumulates boxes against a stopper at the end of a slow buffer
belt and releases them in counted batches of three onto a faster outfeed belt. It shows the
`stopper` component (PLAN 2.5), zero-pressure-style queueing on a running belt, and the classic
problem of counting parts that travel nose to tail: a photo eye on the buffer belt would never
see a gap between queued boxes, so the count eye sits on the faster outfeed, where each released
box pulls away from the next.

Training and demo use only. Not a safety-rated simulation, and the program is not a safety-rated
program.

## The plant

| Component | Type | What it does |
|---|---|---|
| `CONV_BUF` | belt conveyor | Buffer belt, 4 m at 0.3 m/s |
| `STP_01` | stopper | Blade at x 3.95 m, 5 cm before the transfer; holds the queue while raised |
| `PE_FULL` | photo eye | Under the third queued box (x 3.2 m): blocked for good means a batch is ready |
| `CONV_OUT` | belt conveyor | Outfeed, 3 m at 0.9 m/s, three times the buffer speed |
| `PE_REL` | photo eye | On the outfeed at x 4.16 m; counts released boxes |
| `EMT_01` | box emitter | A 0.3 m × 0.2 m box every 2 s |
| `REM_01` | part remover | End of the outfeed |

## I/O

No analog tags, so the `float32` and `int16_scaled` lockfiles give the same addresses.

| Tag | Modbus | OpenPLC | Meaning |
|---|---|---|---|
| `CONV_BUF_RUN_DI` | DI:0 | `%IX100.0` | Buffer belt running |
| `CONV_OUT_RUN_DI` | DI:1 | `%IX100.1` | Outfeed belt running |
| `PE_FULL_DI` | DI:2 | `%IX100.2` | Batch ready eye blocked |
| `PE_REL_DI` | DI:3 | `%IX100.3` | Release count eye blocked |
| `STP_01_DN_DI` | DI:4 | `%IX100.4` | Stopper lowered limit |
| `STP_01_UP_DI` | DI:5 | `%IX100.5` | Stopper raised limit |
| `SIM_HEARTBEAT_DI` | DI:6 | `%IX100.6` | Simulator heartbeat, 1 Hz (every scene has it; PLAN §6.1) |
| `CONV_BUF_RUN_DO` | CO:0 | `%QX100.0` | Buffer belt run |
| `CONV_OUT_RUN_DO` | CO:1 | `%QX100.1` | Outfeed belt run |
| `EMT_01_EN_DO` | CO:2 | `%QX100.2` | Box source enable |
| `STP_01_RAISE_DO` | CO:3 | `%QX100.3` | Stopper raise solenoid |

## Sequence

1. Run both belts and hold the stopper raised. Boxes ride into the blade and queue nose to tail.
2. Feed boxes once both belts have reported running for 1 s.
3. A batch is ready when `PE_FULL` has been blocked for 1.5 s and the blade has been back up for
   2 s. A moving box blocks `PE_FULL` for only 1 s (0.3 m at 0.3 m/s), so only a standing third
   box satisfies the timer.
4. Lower the blade and count `PE_REL` rising edges. At the third, raise the blade. The fourth box
   is still behind the blade line, so it stops there and starts the next queue; a box already
   over the line when the blade rises passes over it.

Why it works: when the third box of a batch reaches `PE_REL`, the fourth box's front is about
0.10 m behind the blade, 0.33 s at the buffer speed. OpenPLC sees the eye up to 100 ms late and
writes the raise up to 100 ms after that, which the margin covers. Put `PE_REL` further down the
outfeed and the fourth box gets through before the blade rises; try it.

Programs: `examples/openplc/buffer-and-release.st` and `examples/pyplc/buffer-and-release.py`.

## Run it

```bash
uv run openfactory run scenes/buffer-and-release.yaml --script examples/pyplc/buffer-and-release.py
```

With OpenPLC, follow [the quickstart](openplc-quickstart.md) for the container, then export
`scenes/buffer-and-release.yaml`, configure with `examples/openplc/buffer-and-release.st`, and run
with `--bind 0.0.0.0 --analog-encoding int16_scaled` (the commands are in the
[guides index](README.md)). Slave Devices then shows `%IX100.0 to %IX100.6` and
`%QX100.0 to %QX100.3`.

## Expected result

60 s from start, under either program: 30 boxes spawned and 21 taken by `REM_01`, none lost. The
first batch leaves at about 16 s and one leaves every 6 s after that, each exactly three boxes
(23 `PE_REL` edges in 60 s: seven full batches and two boxes of the eighth). The run summary line
reads `spawned 30, removed {'box': 21}, lost {}; by remover {'REM_01': {'box': 21}}`.

## Try this

- Change `BATCH` to 2 or 4. With 4, `PE_FULL` must move to the fourth queued box (x 2.9 m), or the
  release starts with too few boxes queued.
- Inject `jammed` on `STP_01` while a batch is going out: the program counts three and commands
  the blade up, but it stays down, the raised limit never makes, and boxes stream through. Add a
  check that `STP_01_UP_DI` makes within 0.5 s of the raise command, and stop the buffer belt if
  it does not.
- Slow `CONV_OUT` to 0.3 m/s in the scene. The released boxes now leave nose to tail, the count
  eye sees several boxes as one, and each batch comes out as six boxes instead of three (measured
  headless: four batches of six in 60 s).
