# ADR-0004: Modbus address lockfile per scene, with optional pinned addresses

**Status:** Accepted, 2026-10-25
**Deciders:** HIS Controls (T. Hunt), Claude Opus 5.5
**Resolves:** the conflict noted under PLAN task 1.7, where §6.1 promised that adding a tag never
renumbers existing ones and allowed pinning addresses, but allocation (sorted by kind, then tag name,
packed from 0) could deliver neither.

## Context

A PLC program is written against Modbus addresses: OpenPLC Slave Devices start addresses, a Click or
Productivity Modbus map, an Ignition device. The register map is generated from the scene (PLAN §6.1,
CLAUDE.md), so it is deterministic. Pure sorted allocation still moves every later tag in a table
when a new tag sorts before them. Adding `PE_00_DI` to the sorter scene would move `PE_01_DI` from
DI:3 to DI:4 and silently break a working PLC program. Scene `io` bindings were plain tag strings,
so there was also nowhere to pin an address.

## Decision

1. **Lockfile.** The first run of a scene writes `scenes/<name>.modbus.yaml` next to
   `scenes/<name>.yaml`. It records the analog encoding and maps every tag to its zero-based address,
   one section per table (`discrete_inputs`, `coils`, `input_registers`, `holding_registers`). The
   lockfile is committed with the scene.
2. **Later runs honor it.** A tag in the lockfile keeps its address. Tags that are not in it are
   appended in tag-name order after the highest address already used in their table. Allocation
   never fills holes. The lockfile is rewritten only when its content changes, so a normal run does
   not touch it.
3. **Pinned addresses.** A binding may be written `{tag: CONV_01_RUN_DO, address: 12}` instead of
   the bare tag name. A pin overrides the lockfile and allocation, and the pinned address is then
   recorded in the lockfile like any other. For a float32 analog tag the pin is the first of its
   two registers.
4. **Removed tags keep their address reserved.** A tag dropped from the scene stays in the lockfile,
   marked `# not in scene`. Its address is never handed to another tag, and the tag gets it back if
   it is re-added. Deleting the line frees the address.
5. **Errors, not guesses.** Loading fails, naming the tags involved, when:
   - two pins, or a pin and a locked tag, overlap;
   - an address would pass 65535;
   - a pin names a tag that is not in the scene;
   - the lockfile was written for the other analog encoding. Register widths differ (2 for float32,
     1 for int16_scaled, ADR-0003), so the analog map cannot be kept. The user sets
     `plc.analog_encoding` back, or deletes the lockfile and updates the PLC program.
6. **One resolver.** The Modbus server, the startup register-map printout, and the I/O list CSV
   export all use `openfactory.protocols.modbus.scene_register_map`, so they cannot disagree.
   `openfactory run` and the CSV export both create or update the lockfile, because the CSV is what
   a PLC program is written against.

## Consequences

- **Address stability is now real.** Adding a tag never renumbers existing ones. Removing a tag
  leaves a hole that reads 0/False; writes to a hole return ILLEGAL ADDRESS.
- **Lockfile churn shows in review.** A pull request that adds a tag also adds a line to the
  lockfile, which is the reviewer's signal that the PLC map grew.
- **First-run order is still sorted.** A brand-new scene with no lockfile and no pins gets the same
  packed allocation as before (kind, then tag name, from 0). The shipped `sorting-by-height` lockfile
  matches the 1.7 and 1.8 evidence.
- **Pins high in a table push new tags above them,** because new tags append after the highest used
  address. Pin into a range above the rest, or pin everything in that table.
- **`scenes/*.modbus.yaml` is not a scene.** Tools that glob `scenes/*.yaml` (the validator, CI)
  must skip the `.modbus.yaml` suffix.
- **Encoding change is a deliberate act.** Switching `analog_encoding` on a scene with a lockfile
  fails until the lockfile is deleted, which surfaces ADR-0003's "addresses depend on the encoding"
  instead of hiding it.
- **Other protocols are unaffected.** Logix and OPC UA address by tag name.
