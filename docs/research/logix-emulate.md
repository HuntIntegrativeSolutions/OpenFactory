# Reaching an emulated Logix controller from OpenFactory (PLAN 2.1 research spike)

*Document only, 2026-11-10. Public sources only (vendor release notes and product pages, library
documentation and source, community forums), per the clean-room rule; no installer, binary or
installation media was opened, and nothing here has been run against a controller. The proposal
for ruling is [ADR-0002](../adr/ADR-0002-logix-emulate-path.md). PLAN 2.1 stays open until the
owner rules and the first live tests below have run.*

## The question

PLAN 2.2 (`protocols/logix.py`) uses pycomm3, which speaks CIP explicit messaging over EtherNet/IP
on TCP 44818 to a ControlLogix or CompactLogix. A physical controller answers on its own IP. An
*emulated* controller lives on a Windows host and is reached through Rockwell's communication
software, so the question is which emulator, configured how, lets pycomm3 (or another Python
client) read and write its controller tags. PLAN §2.4 lists four options; this note adds a fifth,
FactoryTalk Logix Echo, Rockwell's newer emulator.

How sure each statement is: **confirmed** means a primary source (a Rockwell release note, a
library's own documentation or maintainer) says it and the page was read; **reported** means a
community post says it; **unknown** means no public source was found. Rockwell's documentation
site and literature library refused automated fetches (HTTP 403), as during 3.4; their release
notes on `compatibility.rockwellautomation.com` were readable.

## One port, two emulators

Both emulators depend on who owns TCP 44818 on the Windows host, in opposite ways:

- **Emulate** is reached when FactoryTalk Linx's *Listen on EtherNet/IP encapsulation ports* is
  **on** (pylogix's documentation, confirmed for pylogix; a working recipe on the Inductive
  Automation forum, reported).
- **Echo** needs that setting **off**: "Both FactoryTalk Logix Echo and the Listen on
  Ethernet/IP encapsulation ports setting, enabled by default in FactoryTalk Linx, use port 44818"
  (Echo release notes 1.00.00, 8/2021, and 3.00.00, 11/2023; confirmed).

So one Windows host serves one of the two at a time; test them on separate hosts or VMs.
`netstat -ano | findstr 44818` names the process that owns the port, the first thing to check
when a connection is refused.

## (a) Studio 5000 Logix Emulate through the RSLinx virtual backplane

**What it is.** The Windows virtual-chassis emulator for 5570-class (L7x) controllers. Logix
Designer 37 and later do not support emulated 5560 and 5570 controllers (Logix Designer 37.00
help, seen as a search snippet; the page refused fetching); a third-party copy of Rockwell's
Studio 5000 ordering guide lists Emulate as included up to V36. So this path covers projects up
to v35/v36 and ends at v37. L8x projects cannot be emulated here (reported).

**How a client reaches it.** The Emulate process does not listen on 44818 itself (reported, IA
forum, 2023-10). The recipe reported to work with Emulate v32 and v35 (FT Linx up to 6.31, RSLinx
Classic up to 4.31): RSLinx Classic runs with the virtual backplane configured; its Gateway feature
and its "Accept UDP Messages on Ethernet Port" are off; FactoryTalk Linx's encapsulation listener
is on, so FT Linx owns 44818 and routes CIP into the virtual backplane. FT Linx listens only on
the interface chosen in its driver options; "Windows Default" binds 0.0.0.0, which also makes
127.0.0.1 work (reported). RSLinx Classic 4.10 and later turn the UDP option off by default "to
permit FactoryTalk Linx to allocate the CIP port (44818) for CIP Security" (RSLinx Classic Lite
4.21.00 release notes, 3/2021; confirmed).

**pycomm3 and pylogix.**
- pycomm3's maintainer (Discussion #255, 2022-12-19): "it works on my machine with v32 …
  both [the PC's IP] and localhost work for connecting", and earlier Emulate versions needed
  "some secret Rockwell way" (confirmed as the maintainer's report).
- The same thread: `127.0.0.1/{slot}` reaches a local emulator; a 2025-11 post reads an
  Emulate v35 VM from another machine with `LogixDriver('<vm ip>/2')`, while
  `CIPDriver.discover()` finds nothing (reported).
- pylogix's `docs/Emulate.md` says pylogix must run on the Emulate PC and the encapsulation
  checkbox must be on (confirmed for pylogix). The remote read above contradicts "must run on
  the same PC"; the interface binding is the likely difference (unknown).

**Licensing.** Emulate is licensed. Whether RSLinx Classic Lite suffices is unknown; the
reported recipe turns the Classic Gateway feature off, so it does not rely on a Gateway
activation.

| | |
|---|---|
| pycomm3 reaches | the emulated controller's tags at `<host>/<slot>` once FT Linx owns 44818 |
| pycomm3 cannot | discover it; reach it with FT Linx's listener off or bound to another interface |
| Owner installs | Studio 5000 Logix Designer up to v36, Logix Emulate (licensed), RSLinx Classic with the virtual backplane driver, FactoryTalk Linx with the encapsulation listener on, bound to Windows Default |

**First live test.** On the Emulate host: `netstat` shows FT Linx on 0.0.0.0:44818. Then, in
Python, `LogixDriver('127.0.0.1/<slot>')` with the slot the controller occupies in the Emulate
chassis (do not assume one): `plc.info` names the controller; `plc.tags` lists the
generated `export --format l5x` tags; write the controller-scoped BOOL `SIM_HEARTBEAT_DI`
(External Access Read/Write, written by no rung) and read it back. Repeat from a second machine
with the host's IP.

## (b) FactoryTalk Linx Gateway's OPC UA server, with OpenFactory as an OPC UA client

**What it is.** FactoryTalk Linx Gateway serves the tags FactoryTalk Linx can reach over OPC DA
and, since v6.00, OPC UA (product page, search snippet). Release notes 6.21.00 (4/2021;
confirmed) list:
- an OPC UA tag list with access rights;
- the default namespace index 2;
- self-signed certificates that need manual approval;
- no access to Logix extended tag attributes or alarm members.

The OPC Foundation's listing for v6.50.00 (2024-09; confirmed) gives the server security
policies Basic256Sha256, Aes128-Sha256-RsaOaep and Aes256-Sha256-RsaPss (with the deprecated
Basic128Rsa15 and Basic256), "None" for the client role only, and Anonymous and
username/password user tokens.

From Rockwell help pages seen only as search snippets:
- the default UA port is 4990;
- writes need the FactoryTalk Security permission "OPC UA Client Write Access";
- 6.60 allows 100 OPC UA client connections;
- the Gateway comes in tiers by tag count (Basic 1K, Standard 5K, Extended 15K, FTALK-SP013).
  Which release added Extended is inconsistent (6.10 in the getting-results guide, 6.21 in the
  release notes).

The default write permission and whether "None" security can be enabled for a test are unknown.

**As a path for emulators.** It serves whatever FT Linx sees, so Emulate, Echo and hardware the
same way. Echo tags over the Linx Gateway are reported (DMC, 2024-11), and so is Emulate v34 with
FT Linx Gateway 6.2 (IA forum).

| | |
|---|---|
| pycomm3 reaches | nothing: this is OPC UA, so `asyncua` (already a dependency, extra `opcua`) as a *client* |
| Cannot | extended tag attributes, alarm members; anything without the write permission |
| Owner installs | FactoryTalk Linx Gateway with a paid activation sized by tag count, its UA endpoint configured, the client certificate trusted, write access granted |
| Code it needs | an OPC UA *client* role in OpenFactory (today `protocols/opcua.py` is a server); not PLAN 2.2's pycomm3 |

**First live test.** `asyncua` client to `opc.tcp://<gateway host>:4990`: accept the server
certificate and trust the client's in the Gateway; browse namespace 2 down to the controller's
shortcut; read `SIM_HEARTBEAT_DI`, write it, read it back; then time 100 reads of the scene's
tags.

## (c) A .NET or C# bridge

- **A Logix Emulate SDK:** no public SDK was found (unknown; probably none).
- **FactoryTalk Logix Echo SDK** (Echo 1.00.00 release notes; confirmed): "Read and write
  controller tag data through the API", pause and speed up or slow down execution (for
  co-simulation), and "the API client is a 32-bit (x86) library". Echo's product page (snippet)
  lists Python support and C# and Python samples. The full API reference ships with the
  installation only (reported), so its calls are unknown here.
- **Logix Designer SDK** (public description, LDSDK-GR001 and a Rockwell blog, snippets): Python
  and C# clients for projects v31 and later, works with Echo; it drives Logix Designer (open,
  download, change mode, read and write named tag values). It is a project-automation tool, not
  a 50 ms I/O path, and needs a Logix Designer licence (reported).
- **Studio 5000 Simulation Interface** (product page, snippet): connects physical or emulated
  controllers to modelling tools through FMI and MATLAB/Simulink, not a general tag API.
- **FactoryTalk Linx SDK** (LNXSDK-RM001, v6.40, 11/2023, snippet): a C API in the style of the
  RSLinx Classic C SDK. **FactoryTalk Linx CommDTM** is an FDT component for device
  configuration, not a tag API.

| | |
|---|---|
| pycomm3 reaches | not involved: a bridge process would expose tags to OpenFactory some other way (its own socket, OPC UA, Modbus) |
| Cannot | n/a; the Echo SDK's tag API is the only public runtime tag interface found, and its documentation is not public |
| Owner installs | Echo with its SDK, Visual Studio or a .NET runtime on the Windows host; we would write and maintain the bridge |

**First live test, if ever chosen.** Only after (e) fails: a console program on the Echo host
that reads and writes `SIM_HEARTBEAT_DI` through the Echo SDK, timed over 1000 cycles.

## (d) A physical CompactLogix 5380 on the bench

pycomm3's own documentation (confirmed):
- A bare IP means a controller in slot 0 of a backplane; `IP/slot` and a full route
  (`IP/backplane/2/enet/...`) are also accepted.
- LogixDriver tries an Extended Forward Open first and falls back to the standard one.
- LogixDriver covers ControlLogix, CompactLogix and Micro800.

pycomm3 publishes no list of tested controllers. Its README says it is no longer actively
developed, yet 1.2.15 and 1.2.16 were released on 2025-12-22 (PyPI; confirmed).

On the 5380, each embedded port has its own IP in Dual-IP mode (Logix Designer help and
5069-TD002, mirror/snippet), so OpenFactory connects to the port on its subnet. Its CIP
connection limits are unknown here; pycomm3 uses one connection per driver.

| | |
|---|---|
| pycomm3 reaches | the controller directly, `LogixDriver('<5380 ip>')` |
| Cannot | n/a beyond the controller's connection and external-access settings |
| Owner installs | the controller, a power supply, an Ethernet link; Studio 5000 for the project; nothing on the OpenFactory side beyond the `logix` extra |

**First live test.** `LogixDriver('<5380 ip>')`: `plc.info`; write and read back
`SIM_HEARTBEAT_DI`; a timed loop of 100 batched reads of the scene's DO/AO tags against PLAN
6.2's 50 ms poll; then PLAN 2.2's done-when (the sorter under the bench controller, the cable
pulled for 10 s).

## (e) FactoryTalk Logix Echo

**What it is.** Rockwell's emulator for 5580 controllers on firmware v33 and later.

| Release | Date | What it added | Source |
|---|---|---|---|
| 1.00.00 | 8/2021 | 5580 only; up to 17 controllers; 64-bit Windows 10 / Server 2016; installs FT Linx 6.21 and FactoryTalk Activation | release notes, confirmed |
| v2 | 2022 | multiple chassis; GuardLogix 5580 | reported |
| 3.00.00 | 11/2023 | CompactLogix and Compact GuardLogix 5380 | release notes, confirmed |

A 2024 integrator post lists 5580 v33-36, GuardLogix v35-36 and 5380 v36 (reported). Licences
are per controller; add controllers in the off state "to avoid tying up a controller license"
(3.00.00 notes, confirmed).

**How a client reaches it.** The 3.00.00 notes say Echo does not support outgoing CIP Class 3,
nor Class 0/1 with physical devices, but "the application accepts incoming CIP Class 3
connections" (confirmed). Class 3 is what pycomm3's tag reads and writes use. Each emulated
controller gets an IP on one of the host's adapters and appears in Linx as a 9310-WED300ENT
(reported). Ignition's Logix driver reads Echo when the controller is bound to a non-loopback IP
and the slot matches; loopback failed for those users (IA forum, reported).

No public source shows pycomm3, pylogix or libplctag against Echo (unknown). That they work is an
inference from "accepts incoming CIP Class 3" and from Ignition's Logix driver working.

| | |
|---|---|
| pycomm3 reaches | expected: the controller at `<echo controller ip>/<slot>` (5580) or `<ip>` (5380); **unconfirmed** |
| Cannot | Echo's outgoing messaging or I/O connections (not needed: OpenFactory is the client) |
| Owner installs | FactoryTalk Logix Echo (licensed per controller), FactoryTalk Linx with the encapsulation listener **off**, Studio 5000 v33 or later to download, and a non-loopback IP for the controller |

**First live test.** On the Echo host:
1. Create a 5580 controller in slot 1 on a dedicated non-loopback IP, download the sorter's
   generated tags plus an empty main routine, and confirm with `netstat` that Echo owns 44818.
2. From Python (on the host, then from another machine): `LogixDriver('<ip>/1')`, check
   `plc.info` and `plc.tags`, and write and read back `SIM_HEARTBEAT_DI`.
3. Time 100 batched reads.

## Summary

| Option | pycomm3? | Status of the evidence | Studio 5000 range | Extra licences | New code |
|---|---|---|---|---|---|
| (a) Emulate via FT Linx listener | yes, `<host>/<slot>` | maintainer reports v32; community v35; no Rockwell document | up to v35/v36; ends at v37 | Emulate | none beyond 2.2 |
| (b) Linx Gateway OPC UA | no (asyncua client) | Rockwell release notes, OPC Foundation listing | any FT Linx reaches | Linx Gateway tier | an OPC UA client role |
| (c) .NET bridge | no | Echo SDK confirmed to read and write tags; API not public | Echo's | Echo + SDK | a Windows bridge we own |
| (d) bench 5380 | yes, `<ip>` | pycomm3 documentation | any | none | none beyond 2.2 |
| (e) Echo | expected, `<ip>/<slot>` | Rockwell: accepts incoming Class 3; community: Ignition's Logix driver works; pycomm3 untested | v33 and later | Echo (per controller) | none beyond 2.2 |

## Sources (retrieved 2026-11-10)

Rockwell (release notes read; other pages seen as search snippets because the sites refused
fetching):
- FactoryTalk Logix Echo 1.00.00 release notes (8/2021):
  <https://compatibility.rockwellautomation.com/GeneratedReleaseNote.aspx?v1=58814>
- FactoryTalk Logix Echo Single Node 3.00.00 release notes (11/2023):
  <https://compatibility.rockwellautomation.com/GeneratedReleaseNote.aspx?v1=62836>
- RSLinx Classic Lite 4.21.00 release notes (3/2021):
  <https://compatibility.rockwellautomation.com/GeneratedReleaseNote.aspx?v1=59962>
- FactoryTalk Linx Gateway 6.21.00 release notes (4/2021):
  <https://compatibility.rockwellautomation.com/GeneratedReleaseNote.aspx?v1=60152>
- Logix Designer 37.00 help, "Emulated controller"; FactoryTalk Linx Gateway 6.60 help, "UA
  server endpoints"; FactoryTalk Linx Gateway product page; FTALK-SP013, FTLG-GR001,
  LDSDK-GR001, LNXSDK-RM001 (snippets only).

Others:
- OPC Foundation product listing, FactoryTalk Linx Gateway 6.50.00:
  <https://opcfoundation.org/products/view/factorytalk-linx-gateway>
- pycomm3: <https://pycomm3.readthedocs.io/en/latest/getting_started.html>,
  <https://github.com/ottowayi/pycomm3>, Discussion #255
  <https://github.com/ottowayi/pycomm3/discussions/255>, PyPI release history.
- pylogix, `docs/Emulate.md`: <https://github.com/dmroeder/pylogix/blob/master/docs/Emulate.md>
- Inductive Automation forum:
  - "OPC to Emulate, still looking for answers" (2023, pages 1-3):
    <https://forum.inductiveautomation.com/t/opc-to-emulate-still-looking-for-answers/80088>
  - Echo threads: <https://forum.inductiveautomation.com/t/allen-bradley-logix-driver-to-factorytalk-logixecho-on-vm/64411>,
    <https://forum.inductiveautomation.com/t/ft-echo-and-ignition/93611>,
    <https://forum.inductiveautomation.com/t/echo-emulation-and-tag-browsing-in-ignition-designer/70259>
- DMC, "Getting started with FactoryTalk Logix Echo" (2024-11-08):
  <https://www.dmcinfo.com/blog/15859/getting-started-with-factorytalk-logix-echo/>
- RealPars, "Logix Designer SDK": <https://www.realpars.com/blog/logix-designer-sdk>
- A third-party copy of Rockwell's Studio 5000 ordering guide (Emulate "included up to V36";
  snippet).
