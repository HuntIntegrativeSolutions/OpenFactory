# ADR-0003: Per-scene analog encoding on Modbus (`float32` default, `int16_scaled` for OpenPLC)

**Status:** Accepted, 2026-10-25
**Deciders:** HIS Controls (T. Hunt), Claude Opus 5.5
**Records:** the decision already written in `docs/PLAN.md` §6.1. ADR-0002 is reserved for the
Milestone 2 Logix Emulate spike.

## Context

PLAN §6.1 maps AI tags to input registers (FC04) and AO tags to holding registers (FC03/06/16), with
float32 as two registers, high word first. Research on 2026-10-25 (`docs/research/libraries.md`,
"OpenPLC Runtime v3 Modbus mapping and float32") found that OpenPLC Runtime v3, the first supported
controller, performs no float decoding on its Slave Devices path. Each polled register lands as one raw
16-bit word in `%IW100`… or is written from `%QW100`… (source: `webserver/core/modbus_master.cpp` at
OpenPLC_v3 commit `b5d4135`). Whether a v3 IEC program can reinterpret two `WORD`s as a `REAL`
bit-for-bit is unconfirmed. Commercial PLCs and Ignition read float32 register pairs without trouble.

## Decision

1. The scene's `plc` block takes `analog_encoding: float32 | int16_scaled`. It applies to every AI and AO
   tag in the scene.
2. **`float32`** (default): one IEEE-754 value in two consecutive registers, high word at the lower
   address (ABCD). This is also OpenPLC's own `%MD` word order.
3. **`int16_scaled`**: one register per analog tag. Raw counts are 0–32767 for 0–100 % of the tag's
   range, the same raw-count convention as a 1756-IF8 4–20 mA channel.
   - Engineering value = `lo + raw / 32767 × (hi − lo)`.
   - Raw = `round((value − lo) / (hi − lo) × 32767)`.
   - Values outside the range are clamped to 0–32767 in both directions. A PLC write above 32767 (for
     example from an unsigned OpenPLC `%QW`) reads as 100 %.
4. **Where `range` comes from:** in Milestone 1 each analog port declares its range in the scene schema
   (for example `belt_conveyor.speed` is 0–100 %). A per-binding range override is not part of this
   decision; it is a suggested PLAN change for Milestone 3 transmitters and tanks.
5. Shipped OpenPLC example scenes use `int16_scaled`. Every other example uses the default.

## Consequences

- **Addresses depend on the encoding.** Changing `analog_encoding` re-allocates analog addresses (2
  registers per tag versus 1). The exported I/O CSV `modbus_address` column and the startup register map
  are always generated for the scene's encoding. A PLC program written for one encoding does not run
  against the other.
- **Resolution.** `int16_scaled` quantizes to 1/32767 of span, which is fine for training, and it
  saturates at the range limits, so overrange cannot be shown on the wire.
- **Scope.** The encoding is per scene, not per tag. Mixed encodings would need a new ADR.
- **No effect on other protocols.** Logix and OPC UA carry REAL natively and ignore this setting.
- **Testing.** The Modbus roundtrip test (PLAN 1.7) must cover both encodings.
