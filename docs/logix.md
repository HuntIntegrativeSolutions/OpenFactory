# Logix controllers: the bench, Echo and Emulate (PLAN 2.2)

OpenFactory can exchange its I/O table with a Rockwell ControlLogix or CompactLogix controller
over EtherNet/IP, physical or emulated, with [pycomm3](https://github.com/ottowayi/pycomm3)
(MIT, the `logix` extra). OpenFactory is the **client**: it polls the controller; the controller
runs your program. Which controllers and why: [ADR-0002](adr/ADR-0002-logix-emulate-path.md) and
[the research behind it](research/logix-emulate.md).

> **Status.** Built and tested against a fake controller only (`tests/unit/test_logix.py`). It has
> not yet talked to a real controller, Echo or Emulate: PLAN 2.2's Done-when is the bench run. Read
> "What is not proven yet" below before relying on it. Training and demo use only; not a
> safety-rated system.

## What happens

Every 50 ms OpenFactory:

1. writes the simulator's tags (DI and AI, and `SIM_HEARTBEAT_DI`) that changed, in one batched
   write; after every (re)connect, all of them;
2. reads every PLC tag (DO and AO) in one batched read, into the I/O table.

The controller must hold one **controller-scoped** tag per scene tag, with the scene's name and
type: `BOOL` for a DI or DO, `REAL` for an AI or AO. `openfactory export SCENE --format l5x` writes
exactly those tags as a Studio 5000 partial import (PLAN 2.3), `SIM_HEARTBEAT_DI` included. A tag
the controller lacks, or has with another type, is logged once and left out; the rest run.

### The plant's own names: `plc_tag` (PLAN 5.3b)

A scene whose bindings carry `plc_tag` ([the scene format](scene-format.md)) is exchanged with
the controller by those names instead: `Program:Sorter.Conv1_Run` (a program-scoped tag),
`Conv1.Running` (a structure's member), `Speeds[2]` (an element), `Sorter_DI_Word.4` (a bit). The
batches stay one write and one read a cycle, and each value comes back under its scene tag. The
heartbeat goes by `plc.heartbeat_plc_tag` when the scene sets it. When any name starts with
`Program:`, the program-scoped tag definitions are uploaded on connect too (pycomm3's
`init_program_tags`), which takes longer on a controller with many programs.

On connect each name is checked against the controller's definitions:

- a DI or DO must be a `BOOL` (a tag, a structure's `BOOL` member, an element of a `BOOL`
  array) or a bit of a `SINT`, `INT` or `DINT`, numbered 0 to 7, 0 to 15 or 0 to 31
  (publication 1756-PM004L-EN-P, chapter 2, "Address tag data" and "Data type");
- an AI or AO must be a `REAL` (an `INT` for an `int16` point), never a bit.

A name the controller does not have, or has with another type or a bit out of range, is logged
once (naming both the scene tag and the plant name) and left out; the rest run. A scene with no
`plc_tag` runs exactly as described above.

If the link drops, OpenFactory logs one line and reconnects by itself (after 0.5, 1, 2, 4, then
every 5 s), with no restart of either side. Your program sees the simulator is gone because
`SIM_HEARTBEAT_DI` stops changing (it toggles every 0.5 s of simulated time): watch it with a
timer and stop the line if it freezes.

### What the plant does when the link is lost: `on_link_loss`

Each connection chooses what happens to the PLC-written tags (DO and AO) in the simulated plant:

| `on_link_loss` | Behaviour |
|---|---|
| `"off"` (default) | Once no exchange with the controller has succeeded for `link_loss_timeout_s` (default 1.0 s), every DO goes false and every AO to 0.0, in one write, logged once per outage. Conveyors stop, valves close to their 0 % command. The controller's values come back with the first read after the reconnect. |
| `"hold"` | The plant keeps the controller's last outputs until the link returns: a running conveyor keeps running. |

**Why "off" is the default:** a real output module goes to its fault state on a communications
fault, and that state is usually "off" (configurable per module in Studio 5000), so a plant that
keeps running with no controller teaches the wrong lesson. `"hold"` is there for demos that must
not stop on a network blip. The timeout counts from the last successful exchange, so it also
covers a controller that stops answering without an error: pycomm3 reports a pulled cable only
after its socket timeout (5 s), and the outputs go off after 1 s regardless.

## Setup

1. Install the extra: `uv sync --extra logix`.
2. Import the scene's tags into the controller project:
   `uv run openfactory export scenes/sorting-by-height.yaml --format l5x -o sorter-tags.L5X`,
   then in Studio 5000 *File > Import Component > Tags* (or a partial import), and download.
   The tags need **External Access: Read/Write**. Your program reads the DIs and AIs and writes the
   DOs and AOs; nothing else may write the DIs and AIs.
3. Name the controller in `openfactory.local.toml` (git-ignored; never in a scene, never
   committed):

   ```toml
   [connections.bench]
   protocol = "logix"
   path = "192.0.2.10"          # see the path for your controller below
   on_link_loss = "off"         # or "hold"; "off" is the default (see above)
   link_loss_timeout_s = 1.0    # the default
   ```

4. Run: `uv run openfactory run scenes/sorting-by-height.yaml --logix bench`, or put
   `plc: {protocol: logix, role: client, connection: bench}` in a copy of the scene.
   `--logix` replaces the Modbus server for that run; `--script` cannot be combined with it.

## The path for each controller

| Controller | `path` | Notes |
|---|---|---|
| **Bench CompactLogix 5380** (the reference, ADR-0002 (d)) | `"<ip>"` | The IP of the embedded port on OpenFactory's subnet (in Dual-IP mode each port has its own). Nothing to install besides the extra. |
| **ControlLogix**, controller not in slot 0 | `"<ip of the EtherNet/IP module>/<slot>"` | A full route also works: `"<ip>/backplane/<slot>"`. |
| **FactoryTalk Logix Echo**, 5580 (ADR-0002 (e), v33 and later) | `"<echo controller ip>/<slot>"` | Give the emulated controller its own **non-loopback** IP and note its slot. FactoryTalk Linx's *Listen on EtherNet/IP encapsulation ports* must be **off** (Echo itself listens on 44818). Echo 3.0+ also emulates a 5380: then `"<ip>"`. |
| **Studio 5000 Logix Emulate**, 5570 (ADR-0002 (a), v35/v36 and earlier) | `"<emulate host ip>/<slot>"`, or `"127.0.0.1/<slot>"` on the host | RSLinx Classic running with the virtual backplane; its Gateway feature and "Accept UDP Messages on Ethernet Port" off; FactoryTalk Linx's encapsulation listener **on**, bound to *Windows Default*. Use the slot the controller occupies in the Emulate chassis. |

Echo and Emulate need opposite FactoryTalk Linx settings, so they cannot share one Windows host.
If a connection is refused, `netstat -ano | findstr 44818` on that host shows which process owns
the port.

**First check** for any of them, before a scene: in Python,

```python
from pycomm3 import LogixDriver

with LogixDriver("<path>") as plc:
    print(plc.info)  # the controller answers
    print(plc.write("SIM_HEARTBEAT_DI", True))  # a tag from the export
    print(plc.read("SIM_HEARTBEAT_DI"))  # reads back True
```

## The first live test: Echo

[The Echo runbook](logix-echo-test.md) is the step-by-step guide for the owner's Echo test on a separate
Windows VM or PC, with a results table to fill in.

## What is not proven yet

The fake controller in the tests stands in for pycomm3's `LogixDriver` (open, close, the tag list,
read, write) with a link that can be dropped. It proves the client's own behaviour: one write and
one read per cycle, only changed inputs written, BOOL and REAL values both ways, missing and
mismatched tags reported and left out, the plant's names (a program-scoped tag, a structure's
`BOOL` and `REAL` members, an element, bits of a `DINT`) exchanged in those same batches and
checked by type and bit range with pycomm3's own name lookup, a 10 s drop survived with the inputs re-sent and no restart,
and a run that starts with the controller unreachable. It **cannot** prove:

- that pycomm3 reaches a real 5380, Echo or Emulate at all with these paths (Forward Open, the tag
  upload, routing through FactoryTalk Linx);
- real timing: how long a batched read of a scene's tags takes, and whether 50 ms holds;
- what a pulled cable looks like to pycomm3: a socket timeout after `socket_timeout` (5 s by
  default) rather than an immediate error, so the outage is noticed later than in the test;
- how a controller answers a write to a tag whose External Access is Read Only, or to a tag a rung
  also writes;
- with plant names: how long the upload takes with program tags on, whether a member of a
  plant's structure accepts an external write, what a bit write does to a word another writer
  also updates, and what many member reads cost against the 50 ms cycle;
- how long the controller keeps a dead connection before it accepts a new one;
- how long a run waits at start when the controller's host is up but does not answer: the tests
  show a refused connection returning at once, but an address that swallows packets holds the
  first attempt until pycomm3's socket timeout (5 s) before the run goes ahead.

Those are the bench run's (PLAN 2.2) and the Echo and Emulate first tests' (ADR-0002) to show.
