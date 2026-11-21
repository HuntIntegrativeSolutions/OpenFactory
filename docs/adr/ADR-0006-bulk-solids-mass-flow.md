# ADR-0006: Bulk solids as a mass flow, not as parts

**Status:** Proposed, 2026-11-20. Not ruled; nothing here is built. PLAN 5.7 ("ADR first") waits
on the owner's answers to the questions at the end.
**Deciders:** HIS Controls (owner), Claude Opus 5.5 (proposal)
**Applies to:** PLAN 5.7 (bulk solids): hopper and silo (level, load cell), screw conveyor,
bucket elevator, rotary airlock, a belt carrying bulk.

OpenFactory is a training and demo tool. **It is not a safety-rated simulation.** A simulated
silo, hopper or conveyor of bulk material is a picture for training, demos and testing PLC
programs; it models no dust explosion, no structural load and no safety function.

## Context

Plants read from drawings (Milestone 5) carry bulk solids: grain, powders, pellets, aggregate.
They are stored in silos and hoppers, fed by rotary airlocks and screw conveyors, lifted by
bucket elevators and carried on belts. A PLC controls them by speed and run commands and reads
levels, weights, running feedback and zero-speed switches. OpenFactory has two worlds today, and
neither fits:

- **The parts world** (Milestones 1 and 2): each box is an object the core moves on conveyors,
  tick by tick, with sensors that see it. It is right for cartons and totes. A stream of 10 t/h
  of grain is millions of grains, or, lumped, thousands of objects a minute; the per-part cost
  measured on `load-200` (PLAN 2.9) grows with every part in the scene.
- **The flow network** (ADR-0005): an incompressible liquid moved by heads, solved each tick.
  Bulk solids are not moved by a head. Out of a hopper the mass flow through an outlet does not
  depend on the height of material above it (Beverloo, Leniger and van de Velde, "The flow of
  granular solids through orifices", *Chemical Engineering Science* 15, 1961, pp. 260 to 268),
  unlike a liquid's, and in a screw, an airlock or on a belt the flow is set by the machine's
  speed and its fill, not by a pressure difference.

## Decision (proposed)

1. **The model: mass, not parts.** Bulk material is a quantity, never an object. Each vessel
   holds a mass of material (kg); each feeder moves a mass flow (kg/s). Mass is the conserved
   quantity, because load cells read mass and PLCs batch by weight; volume and level follow
   from it through the material's **bulk density** (kg/m³).
2. **Materials.** A scene names its materials once (`materials:`, each a name and a bulk density;
   the angle of repose only for drawing the heap). A vessel holds one material, or a well-mixed
   blend by mass fractions, as a tank holds ingredients by volume today (ADR-0005, decision 8).
3. **A separate solids network.** Vessels and their sources and sinks are nodes; feeders are
   directed links from a vessel's outlet to the next vessel or feeder. There is no head and no
   iteration: each tick, links are taken in topological order of the network (ties in the
   scene's order) and each moves

   `min(capacity × speed fraction, mass available upstream this tick, room downstream)`

   where `capacity` is the link's rated mass flow at full speed and full fill. A link into a
   full vessel moves nothing (the feeder is blocked); a vessel never holds more than its volume
   × bulk density. A loop (recirculation) is broken by taking its back link one tick late, so
   the order is fixed and the result deterministic (PLAN §3.2). Mass balances exactly each
   tick, as the flow network's volume does.
4. **Transport delay.** A screw, an elevator and a belt carry material for a time: length over
   speed. Each keeps a ring of the mass that entered it per tick and delivers each slot when it
   has travelled (plug flow). Stopping the feeder stops the ring; restarting delivers what was
   on it. An airlock delivers in the tick it turns. This gives a PLC the dead time real
   conveyors have, which a level loop or a batch cut-off must allow for.
5. **Levels.** A vessel's level is the height of its contents from the volume (mass / bulk
   density) through its shape: a cylinder or box over a cone or wedge (a frustum's volume).
   The surface is drawn and measured flat; the heap's slope and a hopper's rat-hole are not
   geometry (see 9).
6. **Where it meets the flow network: nowhere, in the first types.** A vessel is a liquid tank or
   a bulk vessel, never both; no link joins the two networks. Dosing a powder into a liquid
   tank is a question for the owner (Q2).
7. **Where it meets the parts world: nowhere, in the first types.** A belt carrying bulk is its
   own type, not a `belt_conveyor` with parts, so the kinematic model and its parity with the
   browser physics (PLAN 2.9) are untouched. Bagging (bulk into parts) is a question (Q4).
8. **The viewer** draws contents, not grains: a vessel's fill as a solid of its level, a belt's
   or screw's load as a ridge whose height follows the mass in each slot of its ring. No
   particles in the browser, no physics there; the core is authoritative, as for liquids.
9. **What it will not model:** particle mechanics (no discrete element method), segregation,
   consolidation over time, moisture and caking, flow patterns (mass and funnel flow) beyond a
   bridging fault, the heap's shape, pneumatic conveying (pressures, dilute and dense phase),
   temperature (that is 5.8's), dust, explosion and its protection, and structural loads on a
   silo. A load cell reads the contents' mass plus a tare, with no dynamics of the structure.
10. **Faults** (the PLAN 2.6 fault model): a **bridge** in a vessel (its outlet delivers nothing
    while it holds mass, until cleared), a **plugged chute** (a link's downstream end blocked:
    it stops, and its plug switch reads true), a **motor trip** (as `vfd_motor`), a **slipping
    elevator belt** (running feedback true, zero-speed switch false). Each is training, not a
    model of the failure.

### The first types and their ports (proposed)

Names follow the existing types' style. `capacity_kg_s` is the rated mass flow at 100 % speed.
I/O kinds as in the component reference: DO and AO written by the PLC, DI and AI read by it.

| Type | What it is | Parameters | I/O ports | Network ends |
|---|---|---|---|---|
| `bulk_supply` | material arriving from off the drawing (a truck, a line) | `material`, `capacity_kg_s` (unlimited when unset) | `enable` DO | `outlet` |
| `bulk_discharge` | material leaving the drawing (a mill, a bagger) | `demand_kg_s` (takes all when unset) | | `inlet` |
| `bin` | a silo or hopper: a cylinder or box over a cone or wedge | `shape`, `diameter` or `length` and `width`, `height` (straight wall), `cone_height`, `outlet_diameter`, `material`, `initial_mass_kg`, `tare_kg`, `hi_level`, `lo_level` | `level` AI (m), `weight` AI (kg, from load cells), `hi_sw` DI, `lo_sw` DI | `inlet`, `outlet` |
| `rotary_airlock` | a rotary valve under a bin's outlet | `capacity_kg_s`, `from`, `to` | `run` DO, `speed` AO, `running` DI | `inlet`, `outlet` |
| `screw_conveyor` | a screw feeder or conveyor | `length`, `incline_deg`, `capacity_kg_s`, `travel_s` (or from `length` and a speed), `from`, `to` | `run` DO, `speed` AO, `running` DI, `motion` DI (zero-speed switch) | `inlet`, `outlet` |
| `bucket_elevator` | a bucket elevator | `height`, `capacity_kg_s`, `travel_s`, `from`, `to` | `run` DO, `running` DI, `motion` DI, `plugged` DI | `inlet`, `outlet` |
| `bulk_belt` | a belt conveyor carrying bulk | `length`, `width`, `speed_mps`, `capacity_kg_s`, `from`, `to` | `run` DO, `speed` AO, `running` DI, `rate` AI (kg/s, a belt scale, optional) | `inlet`, `outlet` |

`hopper` and `silo` in PLAN 5.7 are both `bin` here, differing only in size and shape; whether
they should be two types is Q1. A `transmitter` may read `<BIN>.level` and `<BIN>.weight` and a
link's `<LINK>.rate`, as it reads a tank's level and a pump's flow today. Each type, when built,
has the usual Done-when (PLAN 5.7 to 5.11): a unit test, a docstring spec, a section in
[the component reference](../components.md), and a shipped scene that passes its checks at 60 s and 300 s.

## Options rejected, and why

- **Bulk as parts** (each lump a part on the existing conveyors). Real rates need thousands of
  objects a minute; the per-part tick cost grows with every part, the browser would draw and
  simulate each, and a level from counted parts is a staircase. The parts world is right for
  discrete items, so it stays as it is.
- **Bulk in the liquid flow network** (a dense fluid). Its flow is set by heads; bulk flow is
  set by the feeder and, out of an outlet, is independent of the height above it (Beverloo
  1961). Forcing it would need invented resistances tuned per scene, and would put a second
  kind of node in the solver ADR-0005 accepted.
- **Particles in the browser** (a granular simulation in the physics engine). Not deterministic
  across browsers and machines, not authoritative in the core, too costly at plant rates, and
  each viewer would see a different plant.
- **Volume as the conserved quantity.** Bulk density changes with handling and consolidation,
  load cells read mass, and batching is by weight. Volume is derived, for the level only.
- **A full bin design model** (wall pressures, flow-pattern design after Jenike, *Storage and
  Flow of Solids*, Bulletin 123, Utah Engineering Experiment Station, 1964). That is a design
  method for a silo's geometry, not what a PLC needs to see; out of scope (9).

## Consequences

- A new, small module beside `core/flownet.py`, stepped each tick in the core with `dt`, with no
  wall clock (PLAN §3.2), and new component types; no change to the liquid solver, the parts
  world or any shipped scene. A scene with no bulk types runs exactly as before.
- The scene format gains an optional `materials:` block and the types above (a minor release
  under the versioning policy: optional keys and new types).
- The takeoff (5.1) can name the new types, and the authoring guide's type table grows with
  them; `openfactory layout` (5.5) needs a rule for where a bin and its feeders go (a vertical
  stack: bin, airlock under it, screw from there).
- Checks would gain bulk forms of the existing ones (a bin's level within limits, never full,
  never empty; a batch's mass within a tolerance), to be designed with 5.7.

## Questions for the owner

1. **One vessel type or two?** `bin` with a shape (this proposal) for both hopper and silo, or
   separate `hopper` and `silo` types as PLAN 5.7 names them?
2. **Bulk into a liquid tank:** may a bulk link end on a liquid `tank`, adding its mass as an
   ingredient (a powder dosed into a mix tank), in 5.7 or later?
3. **A belt carrying bulk:** a new type `bulk_belt` (this proposal), or a mode of
   `belt_conveyor`? And is its belt scale (`rate` AI) part of 5.7?
4. **Bagging and packing** (bulk into parts, a bag filler making cartons): out of 5.7, or one
   bridging type in it?
5. **Materials:** a scene-level `materials:` block with bulk density (this proposal), or the
   density given on each bin and supply?
6. **Faults:** which of bridge, plugged chute, motor trip and slipping elevator belt are in 5.7,
   and which wait?
7. **The shipped scene** that 5.7's Done-when needs: is "a silo, its airlock and screw filling a
   weigh hopper to a batch weight, then discharging" the right one, with checks on the batch
   mass and the silo's level?
8. **Units on the I/O:** kg and kg/s throughout (this proposal), or t and t/h on the analog
   ranges where plants usually print them?
9. **The order of the types:** bin, airlock and screw first (one scene can use all three), then
   the elevator and the bulk belt?
