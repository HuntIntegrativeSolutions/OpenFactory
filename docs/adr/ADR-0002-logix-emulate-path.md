# ADR-0002: The Rockwell path, a bench controller and an emulated one

**Status:** Accepted, 2026-11-10: decisions 1 to 5 as proposed, and the live tests in the order
(d), Echo (e), Emulate (a). Nothing in it has been tested against a controller yet; the live
tests wait on the owner (PLAN 2.2, open questions).
**Deciders:** HIS Controls (T. Hunt), Claude Opus 5.5
**Appendix:** [Reaching an emulated Logix controller](../research/logix-emulate.md), the research
spike: every option with its sources, what pycomm3 can and cannot reach, what the owner installs,
and the first live test.
**Applies to:** PLAN 2.1 (this record), 2.2 (`protocols/logix.py`), §2.4 and §6.2.

## Context

PLAN §2.4's decision rule: ship (d), a physical CompactLogix 5380, as the documented path, and
whichever of (a) to (c) proves reliable as the Emulate path. The spike adds (e), FactoryTalk
Logix Echo, Rockwell's emulator for 5580 and 5380 firmware v33 and later, and finds three things
the rule did not foresee:

1. **Logix Emulate ends at Logix Designer v37.** It emulates 5570-class controllers up to v35/v36;
   Echo covers v33 and later. Which emulator applies depends on the project's version.
2. **The two cannot share a Windows host.** Emulate is reached when FactoryTalk Linx listens on
   44818; Echo needs that listener off because Echo itself uses 44818 (Echo release notes,
   confirmed).
3. **Echo accepts incoming CIP Class 3 connections** (Echo 3.00.00 release notes, confirmed),
   which is what pycomm3's tag services use, and Ignition's Logix driver is reported working
   against it. No public source shows pycomm3 itself against Echo.

## Decision

1. **(d) the bench CompactLogix 5380 stays the documented reference path**, unchanged: pycomm3
   direct, `LogixDriver('<ip>')`, PLAN 2.2's done-when as written.
2. **(e) Echo is the emulated path for v33 and later**, pycomm3 direct to the emulated controller's
   own non-loopback IP and slot (`<ip>/<slot>`), with FT Linx's encapsulation listener off. It
   needs no new OpenFactory code beyond 2.2, and it is the only emulator that follows Rockwell's
   current controllers. It is promoted to "the Emulate path" only after its first live test
   passes (the appendix, option (e)).
3. **(a) Logix Emulate is the legacy emulated path for v35/v36 and earlier projects** (5570 class):
   pycomm3 to `<host>/<slot>` through FT Linx's encapsulation listener and the RSLinx virtual
   backplane. Documented as community-reported (pycomm3's maintainer on v32, others on v35), on
   a host without Echo, after its first live test.
4. **(b) Linx Gateway OPC UA is the fallback, not built now.** It reaches anything FT Linx sees,
   but it needs a paid Gateway tier and an OPC UA *client* role in OpenFactory (today's
   `protocols/opcua.py` is a server). Build it only if (e) and (a) both fail their tests.
5. **(c) a .NET bridge is not pursued.** No public Emulate SDK exists. The Echo SDK reads and
   writes tags, but its API documentation ships only with the installation and its client is
   32-bit; the Logix Designer SDK is a project-automation tool, not a 50 ms I/O path. Revisit the
   Echo SDK only for co-simulation (pausing or scaling Echo's clock with OpenFactory's), as a
   separate task.

**Order of the live tests:** (d) first, because it is the reference and PLAN 2.2's
done-when; then (e) on a host or VM with Echo; then (a) on a separate host or VM, if a v35-or-earlier
5570 demo is wanted. Each test is the appendix's "First live test", recorded under PLAN 2.1 with
the versions used (Studio 5000, Echo or Emulate, FT Linx, RSLinx Classic, pycomm3 1.2.16).

## Consequences

- PLAN 2.2 is written once, against pycomm3, and serves (d), (e) and (a); the scene's
  `plc.connection` (§6.2) names the CIP path, so switching between them is a line in
  `openfactory.local.toml`.
- The documentation says which emulator matches which Logix Designer version, and that Echo and
  Emulate need opposite FT Linx settings and so separate hosts.
- No OPC UA client is built until (e) and (a) have been tried; if both fail, (b) becomes a new
  PLAN task with its own ruling, and the Gateway licence is the owner's purchase.
- Nothing here is safety-related; an emulated or bench controller running an OpenFactory demo is
  a training setup, not a commissioning or safety test.

## Open for the owner (listed under PLAN 2.2)

- Whether the bench CompactLogix 5380 (d) is available for PLAN 2.2.
- Which host runs Echo (and, if a v35-or-earlier demo is wanted, which separate host or VM runs
  Emulate), with its Studio 5000 version.
