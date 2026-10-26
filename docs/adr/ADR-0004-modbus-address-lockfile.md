# ADR-0004: Modbus address lockfile per scene, with optional pinned addresses

**Status:** Accepted, 2026-10-25. Amended 2026-10-25: one lockfile per analog encoding (decision 7).
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

1. **Lockfile.** The first run of a scene in an analog encoding writes
   `scenes/<name>.<encoding>.modbus.yaml` (for example `sorting-by-height.float32.modbus.yaml`)
   next to `scenes/<name>.yaml`. It records the encoding and maps every tag to its zero-based
   address, one section per table (`discrete_inputs`, `coils`, `input_registers`,
   `holding_registers`). The lockfile is committed with the scene. (Amended; see decision 7.)
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
   - a lockfile's `encoding` line disagrees with the encoding in its file name (a hand-renamed
     file). Register widths differ (2 for float32, 1 for int16_scaled, ADR-0003), so one encoding's
     analog map can never be read as the other's;
   - a lockfile still has the pre-amendment name `scenes/<name>.modbus.yaml`. The error names the
     `git mv` to its encoding's name; nothing is re-allocated until the user renames it.
6. **One resolver.** The Modbus server, the startup register-map printout, and the I/O list CSV
   export all use `openfactory.protocols.modbus.scene_register_map`, so they cannot disagree.
   `openfactory run` and the CSV export both create or update the lockfile, because the CSV is what
   a PLC program is written against.
7. **One lockfile per analog encoding (amendment, 2026-10-25).** The encoding is part of the
   lockfile name, `scenes/<name>.<encoding>.modbus.yaml`, so a `float32` lockfile and an
   `int16_scaled` lockfile of the same scene coexist. The resolver reads and writes only the
   lockfile of the encoding in use and never touches the other. That encoding is the scene's
   `plc.analog_encoding`, unless `openfactory run` or `openfactory export` is given
   `--analog-encoding float32|int16_scaled`. Each lockfile grows independently under decisions 2
   to 4, and pins apply to both. Why: one scene serves PLCs that need different encodings (OpenPLC
   v3 reads only raw 16-bit words, ADR-0003; a Click or Ignition device reads float32), and a
   committed float32 lockfile must not block, or be rewritten by, an `int16_scaled` run. A newly
   created lockfile allocates from scratch, so its discrete tables can differ from the other
   encoding's when that one has grown by appending. Compare the two lockfiles if both PLCs must
   share coil and discrete-input addresses, and pin the tags where they must match.

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
  must skip the `.modbus.yaml` suffix. `openfactory validate` checks the scene's own encoding and
  every other encoding that has a lockfile.
- **Encodings are independent maps (amended).** Switching encoding, with `plc.analog_encoding` or
  `--analog-encoding`, selects a different lockfile instead of failing. ADR-0003's "addresses
  depend on the encoding" is now visible as two files side by side, and a PLC program names the
  one it was written against. The pre-amendment single lockfile was migrated with `git mv` to
  `sorting-by-height.float32.modbus.yaml`, keeping every address.
- **Other protocols are unaffected.** Logix and OPC UA address by tag name.
