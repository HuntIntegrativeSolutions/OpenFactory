# Echo live test: a runbook for the owner (PLAN 2.2, ADR-0002)

This is the first live test of OpenFactory's Logix client (`docs/logix.md`) against
**FactoryTalk Logix Echo**, Rockwell's emulator for ControlLogix 5580 controllers, run on **a
Windows VM or PC separate from the OpenFactory machine** (owner's answer, 2026-11-10). It follows
ADR-0002's order: the bench 5380 (d) is not available yet, so Echo is the first controller
OpenFactory talks to. Passing it does **not** tick PLAN 2.2, whose Done-when is the bench 5380 run.

Every step cites a public source. **[unconfirmed]** marks a step no source we could read
confirms (Rockwell's help and literature sites refuse automated reading); do it as written, then
record what you saw. Sources are listed at the end ([A], [B], ...). Training and demo use only;
not a safety-rated test.

## Before you start

- **Echo host**: a 64-bit Windows 10 Pro/Enterprise (or 2021 LTSC), Windows 11 Pro/Enterprise, or
  Windows Server 2019/2022 machine or VM [A]. Give a VM a **bridged** network adapter, not NAT, so
  the OpenFactory machine can reach the controller's own IP **[unconfirmed: the community report
  in [I] used a bridged VM; NAT is reported to have worked in one case [J]]**.
- **Licences**: Studio 5000 Logix Designer (v33 or later for a 5580 [C, reported]) and FactoryTalk
  Logix Echo, activated through FactoryTalk Activation [A][B].
- **Addresses**: pick one free IPv4 address on the Echo host's network **for the emulated
  controller**, different from the host's own; below it is `192.0.2.50` (a documentation address:
  use yours). The OpenFactory machine must reach it.
- **OpenFactory machine**: this repository with `uv sync --extra logix` (`docs/logix.md`).

## Part 1: the Echo host

1. **Install Studio 5000 Logix Designer first, then Echo.** If Logix Designer is installed after
   Echo, Echo cannot download to its controllers until you run, in an administrator Command Prompt,
   `PowerShell -File "C:\Program Files (x86)\Rockwell Software\FactoryTalk Logix Echo\Scripts\LogixEchoSystemConfigurationUtility.ps1"`
   and restart [A]. The Echo installer also installs the FactoryTalk services it needs, FactoryTalk
   Linx and Activation among them, where they are missing [A][B].
   *See:* both applications start; FactoryTalk Activation Manager shows the Echo licence.
2. **Add the controller's address to the host's network adapter.** Windows: adapter properties,
   IPv4, Advanced, add `192.0.2.50` with its mask. Do this before creating the controller: Echo's
   dashboard offers only addresses the host already has [D, reported].
   *See:* `ipconfig` lists both addresses on the adapter.
3. **Turn off FactoryTalk Linx's "Listen on Ethernet/IP encapsulation ports"** on every driver,
   because Echo itself uses TCP 44818 [A][B]: Start menu, Rockwell Software, **FactoryTalk Linx
   Network Browser**; menu **Configure Drivers**; select each driver in turn, **Edit**, **Advanced**
   tab, clear the check box; then **restart the computer** [A]. Other software that relies on
   FactoryTalk Linx's listener may stop working [A].
   *See:* after the restart, `netstat -ano | findstr 44818` in a Command Prompt; once the controller
   runs (step 5) the process that owns 44818 should be Echo's, not FactoryTalk Linx's
   **[unconfirmed: which process name to expect]**.
4. **Open Windows Firewall for TCP 44818 inbound** on the Echo host **[unconfirmed: Echo's release
   notes name no firewall rule; IA forum users report the firewall had to allow the connection
   [K]]**. pycomm3 uses explicit (Class 3) messaging on TCP 44818 only; UDP 2222 (I/O) is not
   needed.
5. **Create the emulated controller** in the **FactoryTalk Logix Echo Dashboard**: add a chassis,
   then **Add controller**: type 1756-L8x (5580), the firmware matching your Logix Designer
   version, a name, **slot 0**, and the address `192.0.2.50` from the dropdown [D, reported]. Add
   it in the off state, then turn it on when ready (a controller that is off does not hold a
   licence) [A]. Do not use the loopback address or "None" for its Ethernet port: then it cannot be
   reached from another machine [I, reported]. (Echo's notes give a workaround that uses Loopback
   for controllers with 65 or more devices under the front port [A]; this test has none.)
   *See:* the controller running in the dashboard, with its address.

## Part 2: the controller project

6. **Make the tags file on the OpenFactory machine:**

   ```bash
   uv run openfactory export scenes/sorting-by-height.yaml --format l5x \
       --controller EchoTest -o sorter-tags.L5X
   ```

   It holds the sorter's 12 controller-scoped tags (BOOL and REAL, each `ExternalAccess=
   "Read/Write"`), `SIM_HEARTBEAT_DI` included (PLAN 2.3). Copy it to the Echo host.
7. **New project in Logix Designer**: a 1756-L8x controller of the same firmware as the emulated
   one, named as you like. **Import the tags**: *File > Import Component* (or right-click
   *Controller Tags*) and choose `sorter-tags.L5X` **[unconfirmed for v33-v36: the menu path is
   from v37/v38 help seen in search results [M]]**. Import offline, then download.
   *See:* the 12 tags under Controller Tags, each with External Access **Read/Write** [N, reported].
8. **A small test program** in the main routine (ladder), so the controller visibly answers
   OpenFactory: run both conveyors and the box emitter while the simulator's heartbeat keeps
   changing, push a box while the photo eye is blocked.

   | Rung | Logic | Purpose |
   |---|---|---|
   | 0 | `XIC(SIM_HEARTBEAT_DI)TON(HB_HI,?,?)` with `HB_HI.PRE` 2000 | heartbeat stuck high for 2 s |
   | 1 | `XIO(SIM_HEARTBEAT_DI)TON(HB_LO,?,?)` with `HB_LO.PRE` 2000 | heartbeat stuck low for 2 s |
   | 2 | `XIO(HB_HI.DN)XIO(HB_LO.DN)OTE(SIM_OK)` | the simulator is alive |
   | 3 | `XIC(SIM_OK)OTE(CONV_01_RUN_DO)OTE(CONV_02_RUN_DO)OTE(EMT_01_EN_DO)` | run the line |
   | 4 | `XIC(SIM_OK)MOV(SPD_PCT,CONV_01_SPD_AO)MOV(SPD_PCT,CONV_02_SPD_AO)` with `SPD_PCT` 100.0 | speed commands |
   | 5 | `XIC(SIM_OK)XIC(PE_01_DI)OTE(CYL_01_EXT_DO)` | push while the eye is blocked |

   Create `HB_HI`, `HB_LO` (TIMER), `SIM_OK` (BOOL) and `SPD_PCT` (REAL) as controller tags. This is
   a test program, not the sorter's sequence (`examples/openplc/sorting-by-height.st` is that).
9. **Download** to the emulated controller (Who Active, the Echo controller) and go to Run mode
   **[unconfirmed: the path Echo's controller shows in Who Active]**.
   *See:* the controller in Run; `SIM_OK` false (no simulator yet).

## Part 3: the OpenFactory side

10. **Name the connection** in `openfactory.local.toml` (git-ignored) on the OpenFactory machine:

    ```toml
    [connections.echo]
    protocol = "logix"
    path = "192.0.2.50"        # slot 0: the IP alone; for slot N use "192.0.2.50/N" [L]
    on_link_loss = "off"       # the default; repeat step 14 with "hold"
    link_loss_timeout_s = 1.0
    ```

11. **Check the controller answers** (pycomm3 alone, [L]):

    ```bash
    uv run python -c "from pycomm3 import LogixDriver
    with LogixDriver('192.0.2.50') as plc:
        print(plc.info)
        print(plc.write('SIM_HEARTBEAT_DI', True))
        print(plc.read('SIM_HEARTBEAT_DI'))"
    ```

    *See:* the controller's name and revision, then `SIM_HEARTBEAT_DI` written and read back True.
    If it fails, check on the Echo host that 44818 is Echo's (step 3), the firewall (step 4), and
    that the address is not loopback (step 5).
12. **Run the sorter against Echo:**

    ```bash
    uv run openfactory run scenes/sorting-by-height.yaml --logix echo
    ```

    Open the viewer at <http://127.0.0.1:8000/>.
    *See:* "logix: connected" in the log; in Logix Designer, `SIM_HEARTBEAT_DI` toggling every
    0.5 s and `SIM_OK` true; in the viewer, both conveyors running, boxes arriving, the pusher firing
    when a box blocks the eye.
13. **Timing**: in a second terminal, while the run goes on, time 100 batched reads:

    ```bash
    uv run python -c "import time; from pycomm3 import LogixDriver
    tags = ['CONV_01_RUN_DO','CYL_01_EXT_DO','EMT_01_EN_DO','CONV_02_RUN_DO','CONV_01_SPD_AO','CONV_02_SPD_AO']
    with LogixDriver('192.0.2.50') as plc:
        t = time.perf_counter(); [plc.read(*tags) for _ in range(100)]
        print(f'{(time.perf_counter() - t) * 10:.1f} ms per batched read')"
    ```

    OpenFactory polls every 50 ms, so a batched read should take well under that.
14. **A 10 s network drop, with each `on_link_loss` setting.** Disconnect the Echo VM's network
    adapter (or the PC's cable) for 10 s, then reconnect. Do it once with `on_link_loss = "off"`,
    once with `"hold"` (edit the toml, restart step 12).
    *Expect with "off":* about a second after the last good exchange, the log says "every PLC output
    set off"; the conveyors stop in the viewer. On the controller, `SIM_HEARTBEAT_DI` stops
    changing and `SIM_OK` drops after 2 s. After reconnecting: "reconnected", the outputs come back,
    the line runs again, with no restart of either side.
    *Expect with "hold":* the conveyors keep running through the drop; the rest is the same.
    pycomm3 may report the loss only after its 5 s socket timeout (`docs/logix.md`); "off" acts
    after 1 s regardless. How Echo itself treats the client's connection across a drop is not
    publicly documented **[unconfirmed]**; Echo's notes say only that emulated controllers detect
    connection loss [A].
15. **Stop** with Ctrl+C. Leave the controller off in the dashboard to free its licence [A].

## Results (fill in)

| Item | Value |
|---|---|
| Date, tester | |
| Echo host: Windows edition, VM or PC, network mode | |
| Studio 5000 Logix Designer version | |
| FactoryTalk Logix Echo version | |
| FactoryTalk Linx version | |
| Controller: catalog, firmware, slot, address | |
| OpenFactory commit (`git log -1 --format=%h`); pycomm3 1.2.16 | |
| OpenFactory machine (OS; WSL2 or native) | |

| Step | Pass / fail | What you saw (and timings) |
|---|---|---|
| 3: 44818 owned by Echo after the restart | | |
| 11: `plc.info`, write and read back `SIM_HEARTBEAT_DI` | | |
| 12: "connected"; heartbeat toggling; `SIM_OK` true; line runs in the viewer | | |
| 13: ms per batched read of 6 tags | | |
| 14, "off": seconds from unplugging to "outputs set off" | | |
| 14, "off": conveyors stopped; seconds from reconnecting to "reconnected"; line running again | | |
| 14, "hold": conveyors kept running; seconds to "reconnected" | | |
| 14: what pycomm3 logged at the drop (error text) | | |
| Anything else (errors, restarts needed, Echo dashboard messages) | | |

Send the filled tables back; they go under PLAN 2.2 and ADR-0002.

## Sources

Rockwell (release notes read on 2026-11-10; help pages seen only as search excerpts, marked
reported):
- [A] FactoryTalk Logix Echo Single Node 3.00.00 / 3.00.01 release notes (11/2023):
  <https://compatibility.rockwellautomation.com/GeneratedReleaseNote.aspx?v1=62836>. Read directly:
  the supported Windows editions; the FactoryTalk Linx encapsulation setting, its Network
  Browser > Configure Drivers > Edit > Advanced procedure and the restart; installing Logix
  Designer after Echo and `LogixEchoSystemConfigurationUtility.ps1`; the software installed with
  it; adding controllers in the off state; incoming CIP Class 3 accepted; the 65-device Loopback
  workaround; emulated controllers detecting connection loss.
- [B] FactoryTalk Logix Echo 1.00.00 release notes (8/2021):
  <https://compatibility.rockwellautomation.com/GeneratedReleaseNote.aspx?v1=58814>
- [C] Logix Designer help, "Emulated controller" (v38; search excerpt, reported): the 5580 first
  emulated at v33.
- [M] Logix Designer help, "Import and export" (v37; search excerpt, reported).
- [N] Logix Designer help, "About External Access" (v38; search excerpt, reported).

Others (reported):
- [D] DMC, "Getting started with FactoryTalk Logix Echo" (2024-11-08):
  <https://www.dmcinfo.com/blog/15859/getting-started-with-factorytalk-logix-echo/>
- [I] Inductive Automation forum, Logix driver to FactoryTalk Logix Echo on a VM:
  <https://forum.inductiveautomation.com/t/allen-bradley-logix-driver-to-factorytalk-logixecho-on-vm/64411>
- [J] Inductive Automation forum, Echo and iba communication on two VMs:
  <https://forum.inductiveautomation.com/t/factorytalk-logix-echo-and-iba-communication-on-2-virtual-machines/108701>
- [K] Inductive Automation forum, "OPC to Emulate, still looking for answers", page 3:
  <https://forum.inductiveautomation.com/t/opc-to-emulate-still-looking-for-answers/80088?page=3>
- [L] pycomm3, Getting started (the CIP path forms):
  <https://pycomm3.readthedocs.io/en/latest/getting_started.html>

No public source shows pycomm3 itself talking to Echo; this test is the first evidence
(`docs/research/logix-emulate.md`).
