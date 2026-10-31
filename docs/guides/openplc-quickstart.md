# OpenPLC quickstart: sort boxes with a free soft PLC

This guide connects an [OpenPLC v3](https://github.com/thiagoralves/OpenPLC_v3) runtime to the
OpenFactory `sorting-by-height` scene over Modbus TCP. When you finish, OpenPLC runs a Structured
Text program that starts the belts, feeds boxes, and diverts tall boxes onto a second lane, while
OpenFactory simulates the plant.

> **Training and demo tool only.** OpenFactory is not a safety-rated simulation, and the example
> program is not a safety-rated program. Step 4 opens the simulator's Modbus port to your network,
> and Modbus has no authentication. Do this on a lab machine, never on a plant network.

Tested on 2026-10-25 with Windows 11, WSL2 (Ubuntu), and Docker Desktop, OpenPLC v3 at commit
`b5d4135`. Linux with Docker Engine works the same way. Only step 5 (which address the container
uses to reach the simulator) differs.

## What talks to what

```
 OpenPLC container (Modbus master)                 OpenFactory simulator (Modbus slave)
 http://127.0.0.1:8080  web UI                     openfactory run ... --bind 0.0.0.0
 polls every 100 ms  ──────── Modbus TCP ────────►  port 5020, int16_scaled register map
```

OpenPLC is the Modbus *master*: it polls the simulator's discrete inputs and writes its coils and
holding registers. OpenFactory is the *slave* (server).

## 1. Prerequisites

- Docker (Docker Desktop with the WSL2 backend on Windows, or Docker Engine on Linux).
- The OpenFactory repository with Python 3.11+ and [uv](https://docs.astral.sh/uv/). Follow
  `CONTRIBUTING.md` for setup. On a WSL drive without symlink support (for example `/mnt/e`), first
  export `UV_PROJECT_ENVIRONMENT=$HOME/.venvs/openfactory` and `UV_LINK_MODE=copy`.
- Optional, for the 3D viewer: Node.js 20.19+ and `npm ci && npm run build` in `web/`.
- Optional, to configure OpenPLC from a script instead of by hand (step 6b): Playwright's Chromium,
  `uv run playwright install chromium`.

## 2. Build the OpenPLC image (once)

There is no official OpenPLC image on Docker Hub; the images you find there are personal uploads.
Do not use them. Build from the upstream Dockerfile at the pinned commit:

```bash
git clone https://github.com/thiagoralves/OpenPLC_v3
cd OpenPLC_v3
git checkout b5d4135
docker build -t openfactory/openplc:b5d4135 .
```

The build compiles the runtime and takes several minutes.

## 3. Start one OpenPLC container

```bash
docker run -d --name openplc -p 8080:8080 -p 502:502 openfactory/openplc:b5d4135
```

Run just this one container. Open <http://127.0.0.1:8080> and log in as `openplc` / `openplc`
(change the password under Users if the machine is shared). The dashboard should show
"Status: Stopped" and "Program: Blank Program".

## 4. Export the I/O list and start the simulator

OpenPLC's Slave Devices path moves raw 16-bit words and does no float decoding, so the OpenPLC
setup uses the `int16_scaled` analog encoding. In that encoding a value of 0 to 32767 raw counts
means 0 to 100 % of the tag's range (ADR-0003). The scene itself defaults to `float32`.
`--analog-encoding int16_scaled` selects the scene's int16_scaled register map and its lockfile
`scenes/sorting-by-height.int16_scaled.modbus.yaml` (ADR-0004), without touching the float32 one.

From the repository root:

```bash
uv run openfactory export scenes/sorting-by-height.yaml --analog-encoding int16_scaled \
    -o sorting-by-height-int16.csv
uv run openfactory run scenes/sorting-by-height.yaml --bind 0.0.0.0 --analog-encoding int16_scaled
```

The run command prints the register map. It must match this table:

| Tag | Modbus address | OpenPLC location | What it is |
|-----|----------------|------------------|------------|
| `CONV_01_RUN_DI` | DI:0 | `%IX100.0` | Infeed belt running feedback |
| `CYL_01_EXT_DI` | DI:1 | `%IX100.1` | Pusher extended limit |
| `CYL_01_RET_DI` | DI:2 | `%IX100.2` | Pusher retracted limit |
| `PE_01_DI` | DI:3 | `%IX100.3` | Tall-box photo eye, beam blocked |
| `CONV_02_RUN_DI` | DI:4 | `%IX100.4` | Divert lane belt running feedback |
| `SIM_HEARTBEAT_DI` | DI:5 | `%IX100.5` | Simulator heartbeat, 1 Hz (every scene has it; PLAN §6.1) |
| `CONV_01_RUN_DO` | CO:0 | `%QX100.0` | Infeed belt run command |
| `CYL_01_EXT_DO` | CO:1 | `%QX100.1` | Pusher extend solenoid |
| `EMT_01_EN_DO` | CO:2 | `%QX100.2` | Box source enable |
| `CONV_02_RUN_DO` | CO:3 | `%QX100.3` | Divert lane belt run command |
| `CONV_01_SPD_AO` | HR:0 | `%QW100` | Infeed belt speed, 0 to 32767 = 0 to 100 % |
| `CONV_02_SPD_AO` | HR:1 | `%QW101` | Divert lane belt speed, same scaling |

The digital addresses (DI and CO) are the same as in the scene's float32 lockfile: a new
encoding's lockfile copies them from the existing one (ADR-0004 decision 8). The analog addresses
differ, because float32 uses two registers per tag.

Addresses are zero-based. OpenPLC places a Slave Device's points from address 100 onward. The
Slave Devices page says so: "Slave devices are attached to address 100 onward (i.e. %IX100.0,
%IW100, %QX100.0, and %QW100)". So Modbus address N becomes `%IX100.N`, `%QX100.N`, or
`%QW(100+N)`.

`--bind 0.0.0.0` matters. The container has its own loopback, so a simulator listening only on
`127.0.0.1` (the default) is unreachable from it. Add `--headless` if you have not built the viewer.
Otherwise the viewer is at <http://127.0.0.1:8000>, and `--bind` exposes it on your network too.

## 5. Find the address OpenPLC uses to reach the simulator

The container needs an address for the machine running the simulator. With a non-loopback
`--bind`, `openfactory run` prints the candidates right after the register map:

```
... Modbus TCP listening on 0.0.0.0:5020
... reachable from containers or other machines at: 172.18.206.163:5020 (eth0)
```

Start with the first address listed. Two candidates were tried on
the test host. Run both checks yourself, because your addresses will differ.

**Check from inside the container.** Leave the simulator running from step 4. This sends one Modbus
"read discrete inputs" request:

```bash
WSL_IP=$(hostname -I | awk '{print $1}')
for host in host.docker.internal "$WSL_IP"; do
  echo "== $host"
  docker exec openplc python3 -c "
import socket, struct, sys
h = sys.argv[1]
print('resolves to', socket.gethostbyname(h))
s = socket.create_connection((h, 5020), timeout=3)
s.sendall(struct.pack('>HHHBBHH', 1, 0, 6, 1, 2, 0, 5))
print('FC02 reply', s.recv(64).hex())
" "$host"
done
```

Results on the test host:

| Address tried | From the container (TCP) | As OpenPLC Slave Device IP | Use it? |
|---|---|---|---|
| `host.docker.internal` (resolved to `192.168.65.254`) | inconsistent: connected on 2026-10-25, `[Errno 101] Network is unreachable` on 2026-10-26 | **fails**: runtime log repeats `Connection failed on MB device OpenFactory: Invalid argument` | no |
| WSL address from `hostname -I` (`172.18.206.163`) | connects; reply `00010000000401020104` | **works**: coils and registers are written within one poll | **yes** |

In the reply, `0104` means one data byte, `0x04`: DI:2, the pusher's retracted limit, is on, as it
should be at rest.

Even when `host.docker.internal` is reachable, OpenPLC's Modbus master accepts only a numeric IPv4
address in the Slave Device's IP field and does not resolve host names. Use the WSL address, the
first address `hostname -I` prints. On Linux with Docker Engine (not tested here), the host's
address on the `docker0` bridge (often `172.17.0.1`) or its LAN address should work the same way;
run the check above to confirm.

The WSL address can change when WSL or Windows restarts. If OpenPLC stops connecting after a
reboot, run `hostname -I` again and update the Slave Device.

## 6a. Configure OpenPLC by hand

1. **Programs → Upload Program.** Choose `examples/openplc/sorting-by-height.st`, then
   **Upload Program**. On the next page enter a name (for example `sorting-by-height`) and a
   description, then **Upload program**. Wait for "Compilation finished successfully!" in the log
   (about a minute), then **Go to Dashboard**.
2. **Slave Devices → Add new device.** Fill in:

   | Field | Value |
   |---|---|
   | Device Name | `OpenFactory` |
   | Device Type | Generic Modbus TCP Device |
   | Slave ID | `1` (the simulator answers any ID) |
   | IP Address | the address that worked in step 5, for example `172.18.206.163` |
   | IP Port | `5020` |
   | Discrete Inputs (%IX100.0): Start / Size | `0` / `6` |
   | Coils (%QX100.0): Start / Size | `0` / `4` |
   | Input Registers (%IW100): Start / Size | `0` / `0` |
   | Holding Registers - Read (%IW100): Start / Size | `0` / `0` |
   | Holding Registers - Write (%QW100): Start / Size | `0` / `2` |

   Each start is the lowest address in that table's `modbus_address` column of
   `sorting-by-height-int16.csv`. Each size runs from there to the highest address. Then click
   **Save device**. The Slave Devices list should show
   `OpenFactory | TCP | %IX100.0 to %IX100.5 | %QX100.0 to %QX100.3 | - | %QW100 to %QW101`.
3. Click **Start PLC** in the left panel. The dashboard shows "Status: Running".

Settings keeps the default slave polling period of 100 ms and timeout of 1000 ms. The program is
written for them.

## 6b. Or configure it with the script

`tools/openplc_configure.py` does step 6a through the same web pages with Playwright. It stops
the PLC, uploads and compiles the program, replaces any `OpenFactory` device, adds it with the
ranges read from the CSV, prints the Slave Devices table, and starts the PLC:

```bash
uv run python tools/openplc_configure.py --program examples/openplc/sorting-by-height.st \
    --csv sorting-by-height-int16.csv --device-ip "$(hostname -I | awk '{print $1}')"
```

Expected output:

```
Slave Device ranges from the CSV: DI start 0 size 6, CO start 0 size 4, IR start 0 size 0, HR start 0 size 2
uploaded sorting-by-height.st: Compilation finished successfully!
Slave Devices as OpenPLC shows them:
Device Name | Device Type | DI | DO | AI | AO
OpenFactory | TCP | %IX100.0 to %IX100.5 | %QX100.0 to %QX100.3 | - | %QW100 to %QW101
PLC: Running
```

## 7. Watch it sort

With the simulator from step 4 still running, the belts start within a poll of **Start PLC**. Boxes
appear one second later, every 3 s. In the viewer, tall boxes are pushed across onto CONV_02, and
short boxes pass under the photo eye and run to the end of CONV_01.

To check it without the viewer, stop the simulator (Ctrl+C) and run it headless for 60 s with
OpenPLC still running. Do not pass any `--force`, so only the PLC writes outputs:

```bash
uv run openfactory run scenes/sorting-by-height.yaml --headless --duration 60 \
    --bind 0.0.0.0 --analog-encoding int16_scaled
```

The last line is the run summary. From the test host:

```
ran 3000 ticks (60.00 s simulated) in 60.04 s wall; spawned 20, removed {'short': 9, 'tall': 9}, lost {}; by remover {'REM_01': {'short': 9}, 'REM_02': {'tall': 9}}
```

Read it like this:
- **OpenPLC is in control.** `spawned` is above 0 even though no `--force` was given, and only the
  PLC writes the emitter enable.
- **Every tall box went to REM_02** (the divert lane) and every short box went to REM_01.
- **No box fell off a belt** (`lost {}`).
- Parts still on the belts at the end count in `spawned` only.

## The program in brief

`examples/openplc/sorting-by-height.st` (comments in the file explain each line):

1. Run both belts and write 32767 (100 %) to both speed registers.
2. Enable the box source once both belts have reported running for 1 s without a break.
3. On each rising edge of `PE_01_DI`, which only tall boxes trigger because the beam is above
   short ones, start a 1.98 s timer and extend the pusher when it expires. The box needs 2.08 s at
   0.5 m/s to travel from the eye to the plate. OpenPLC's 100 ms polling adds about 0.1 s on
   average, so the timer is shortened by that much.
4. Retract the pusher on its extended limit.

Two details came out of testing:
- **Emitter permissive delay.** The box source spawns a box the moment it is enabled. On the first
  run, the enable came straight from the belts' run feedback. OpenPLC's input image briefly held
  old values when it connected, so the enable chattered on, off, on within 0.3 s and two boxes
  landed on top of each other. The pusher then diverted both, including a short box. The 1 s
  permissive (`BELTS_OK_TMR`) fixed it.
- **Divert timing margin.** Run headless with the same logic, delays from 1.88 s to 2.38 s all
  sort correctly. That leaves room for polling jitter.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Runtime log: `Connection failed on MB device OpenFactory: Invalid argument` | The Slave Device IP is a host name. Use a numeric address (step 5). |
| Runtime log: `Connection failed ...: Connection refused` or timeouts | The simulator is not running, or it listens on 127.0.0.1. Restart it with `--bind 0.0.0.0`. Also check that no firewall blocks port 5020. |
| Belts show running but do not move | The speed registers read 0. The program writes 32767 to `%QW100` and `%QW101`. Check that the Holding Registers - Write size is 2. |
| Tall boxes pass the pusher | The divert timing is off. Check that Settings still has a 100 ms polling period, and that the scene's belt speed and photo-eye position are unchanged. |
| `openfactory run` fails with a lockfile error | Each encoding has its own lockfile (ADR-0004). Do not rename one by hand. A file still named `sorting-by-height.modbus.yaml` gets an error that tells you the `git mv` to run. |
| Port 502 already in use | Another Modbus server (or a second OpenPLC container) is running on the host. Stop it, or map the container to another host port with `-p 1502:502`. |

## Clean up

```bash
docker stop openplc && docker rm openplc
```

Stop the simulator with Ctrl+C. Next time, start again from step 3. The image from step 2 stays
built.
