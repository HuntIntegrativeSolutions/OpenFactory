# ADR-0006: Bulk solids as a mass flow, not as parts

**Status:** Accepted, 2026-11-20 (the owner's rulings 1 to 9 on the proposal's nine questions,
written in below as decisions 11 to 19; the gate type and the blocked-feeder rule added on
acceptance). Built in two rounds: PLAN 5.7a (bin, airlock, screw, gate, supply and discharge) and
5.7b (bucket elevator, bulk belt, the slipping-belt fault).
**Deciders:** HIS Controls (owner), Claude Opus 5.5 (proposal and amendment)
**Applies to:** PLAN 5.7 (bulk solids): hopper and silo (level, load cell), screw conveyor,
bucket elevator, rotary airlock, a belt carrying bulk, and a slide gate under a bin.

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
  granular solids through orifices", *Chemical Engineering Science* 15 (3-4), 1961, pp. 260 to
  269, doi:10.1016/0009-2509(61)85030-6; pages as the publisher's Crossref record gives them,
  read 2026-11-20),
  unlike a liquid's, and in a screw, an airlock or on a belt the flow is set by the machine's
  speed and its fill, not by a pressure difference.

## Decision

1. **The model: mass, not parts.** Bulk material is a quantity, never an object. Each vessel
   holds a mass of material (kg); each feeder moves a mass flow (kg/s). Mass is the conserved
   quantity, because load cells read mass and PLCs batch by weight; volume and level follow
   from it through the material's **bulk density** (kg/m³).
2. **Materials.** A scene names its materials once (`materials:`, each a name and a bulk density;
   decision 15). A vessel holds one material. Blends (mass fractions, as a tank holds
   ingredients by volume, ADR-0005 decision 8) wait for a scene that needs one: in 5.7a a
   supply, and every bin its links lead to, name the same material, and lint refuses a link that
   would carry one material into a bin of another.
3. **A separate solids network.** Vessels and their sources and sinks are nodes; feeders are
   directed links from a node's outlet, or from a carrying link's outlet, to the next node or
   carrying link. An airlock or a gate sits under a node: its `from` is a bin or a supply. There
   is no head and no iteration. Each tick the links are taken **downstream first** (the reverse
   of the network's topological order, ties in the scene's order), so a link that empties a bin
   has done so before the link that fills it asks for room, and material moves at most one link
   a tick. The network steps right after the liquid network in each tick, with the feeders'
   commands from the end of the last tick, as the liquid network does (ADR-0005, tick order).
   Each link moves

   `min(capacity × speed fraction × dt, mass available upstream, room downstream)`

   where `capacity` is the link's rated mass flow at full speed and full fill. Available
   upstream is a bin's mass (none while it is bridged), or a supply's capacity × dt (unlimited
   when unset). Room downstream is a bin's volume × bulk density less its mass, a discharge's
   demand × dt less what it took this tick (unlimited when unset), or a carrying link's room at
   its inlet (see the blocked-feeder rule). A link into a full vessel moves nothing; a vessel
   never holds more than its volume × bulk density. A loop is broken by taking the link that
   closes it as the most upstream, so the order is fixed and the result deterministic (PLAN
   §3.2). Mass balances every tick, to float rounding: the sum of every bin and every carrying
   link's load changes by what the supplies delivered less what the discharges took.

   **The blocked-feeder rule** (added on acceptance). A carrying link (a screw; in 5.7b an
   elevator and a bulk belt) is a ring of `N = round(travel_s / dt)` slots from its inlet to its
   outlet, and **a slot holds at most `capacity × dt`**, what enters in one tick at full speed
   and full fill; so **the link holds at most `capacity × travel_s`**. Running at speed fraction
   `s`, it advances by `s` slots a tick (whole slots, the fraction carried to the next tick). To
   advance, the slot at its outlet must leave in full: it goes to the link's `to`, up to the room
   there. **If the outlet has no room for all of it, the link is blocked:** what it delivered is
   gone, the rest of that slot stays, the ring does not advance, and it takes nothing in that
   tick; its mass stays on it, never dropped and never lost, until the room returns. Its inlet
   slot takes in up to its free room, whether it is running or not: so **a stopped or blocked
   link under a running one is never overfilled**: its inlet fills to one slot's worth, and
   then the link feeding it is blocked in its turn. An airlock or a gate carries nothing; what
   it moves arrives in the same tick, limited by the same room.

4. **Transport delay.** A screw, an elevator and a belt carry material for a time: `travel_s`
   at full speed (`travel_s / s` at speed fraction `s`). The ring of decision 3 delivers each
   slot when it has travelled (plug flow): mass entering in one tick leaves `N` ticks later at
   full speed. Stopping the feeder stops the ring; restarting delivers what was on it. An
   airlock and a gate deliver in the tick they move it. This gives a PLC the dead time real
   conveyors have, which a level loop or a batch cut-off must allow for.
5. **Levels.** A vessel's level is the height of its contents from the volume (mass / bulk
   density) through its shape: a cylinder or box over a cone or wedge (a frustum's volume).
   The surface is drawn and measured flat; the heap's slope and a hopper's rat-hole are not
   geometry (see 9).
6. **Where it meets the flow network: nowhere, in 5.7.** A vessel is a liquid tank or a bulk
   vessel, never both; no link joins the two networks, and lint refuses a bulk end that names a
   liquid component or a liquid end that names a bulk one (decision 12).
7. **Where it meets the parts world: nowhere, in 5.7.** A belt carrying bulk is its own type,
   not a `belt_conveyor` with parts (decision 13), so the kinematic model and its parity with
   the browser physics (PLAN 2.9) are untouched. Bagging and any bulk-to-parts type are out of
   5.7 (decision 14).
8. **The viewer** draws contents, not grains: a vessel's fill as a solid of its level, a belt's
   or screw's load as a ridge whose height follows the mass in each slot of its ring. No
   particles in the browser, no physics there; the core is authoritative, as for liquids.
9. **What it will not model:** particle mechanics (no discrete element method), segregation,
   consolidation over time, moisture and caking, flow patterns (mass and funnel flow) beyond a
   bridging fault, the heap's shape, pneumatic conveying (pressures, dilute and dense phase),
   temperature (that is 5.8's), dust, explosion and its protection, and structural loads on a
   silo. A load cell reads the contents' mass plus a tare, with no dynamics of the structure.
10. **Faults** (the PLAN 2.6 fault model, new kinds `bridge`, `plugged` and `trip`): a **bridge**
    in a bin (its outlet delivers nothing while it holds mass, until cleared), a **plugged
    chute** (a link's outlet takes nothing: the link is blocked by the rule of decision 3, and
    its `plugged` switch reads true), a **motor trip** (the airlock's or screw's motor stops at
    once and its `running` feedback goes false while `run` is still on, until the fault is
    cleared). The **slipping elevator belt** (running feedback true, zero-speed switch false)
    comes with the elevator in 5.7b (decision 16). Each is training, not a model of the failure.

### The owner's rulings (2026-11-20), as decisions

11. **One vessel type, `bin`,** with a `shape`: a silo and a hopper are both bins, differing only
    in size and shape. There are no `hopper` or `silo` types.
12. **Bulk into a liquid tank is not in 5.7.** It is a later PLAN task (5.7c); until then no
    link joins the two networks (decision 6).
13. **A belt carrying bulk is its own type, `bulk_belt`,** with its belt scale (`rate` AI, kg/s).
    It comes in 5.7b.
14. **Bagging and any bulk-to-parts type are out of 5.7.** A later PLAN task (5.7d).
15. **Materials** are a scene-level `materials:` block: each a `name` and a `bulk_density`
    (kg/m³). A bin and a supply name one. The angle of repose of the proposal is not in the
    first block; the viewer draws the surface flat (decision 5).
16. **Faults in 5.7:** bridge, plugged chute and motor trip (5.7a). The slipping belt comes with
    the elevator (5.7b).
17. **The shipped scene** is a silo, its airlock and a screw filling a weigh hopper to a batch
    weight, then discharging it through a gate; its checks are the batch mass within a tolerance
    and the silo never empty and never full. For it the ADR gains a gate under a bin, the
    `slide_gate` (see the table).
18. **Units: kg and kg/s on every port.** A plant's t/h is a scaling on the transmitter or in the
    PLC, not a unit of the simulation. (`transmitter` reads level, temperature, fraction, flow or
    pressure of the liquid network today and has no gain of its own: it cannot read a bin or
    scale kg/s to t/h, and no binding transform scales a value either; the PLC scales, from the
    engineering value or from the raw counts over the binding's `range`. A bulk `transmitter`
    measure, and a scaling on it, are not part of 5.7a.)
19. **Order:** bin, airlock, screw, gate, supply and discharge in 5.7a; the elevator and the bulk
    belt in 5.7b. PLAN 5.7 stays unticked until both are done.

### The types and their ports

Names follow the existing types' style. `capacity_kg_s` is the rated mass flow at 100 % speed.
I/O kinds as in the component reference: DO and AO written by the PLC, DI and AI read by it.

| Type | What it is | Parameters | I/O ports | Network ends |
|---|---|---|---|---|
| `bulk_supply` | material arriving from off the drawing (a truck, a line) | `material`, `capacity_kg_s` (unlimited when unset) | `enable` DO | `outlet` |
| `bulk_discharge` | material leaving the drawing (a mill, a bagger) | `demand_kg_s` (takes all when unset) | | `inlet` |
| `bin` | a silo or hopper: a cylinder or box over a cone or pyramid | `shape`, `diameter` or `length` and `width`, `height` (straight wall), `cone_height`, `outlet_diameter`, `material`, `initial_mass_kg`, `tare_kg`, `hi_level`, `lo_level` | `level` AI (m), `weight` AI (kg, from load cells), `hi_sw` DI, `lo_sw` DI | `inlet`, `outlet` |
| `rotary_airlock` | a rotary valve under a bin's outlet | `capacity_kg_s`, `from`, `to` | `run` DO, `speed` AO, `running` DI, `plugged` DI | `inlet`, `outlet` |
| `screw_conveyor` | a screw feeder or conveyor | `length`, `incline_deg`, `capacity_kg_s`, `travel_s`, `from`, `to` | `run` DO, `speed` AO, `running` DI, `motion` DI (zero-speed switch), `plugged` DI | `inlet`, `outlet` |
| `bucket_elevator` | a bucket elevator | `height`, `capacity_kg_s`, `travel_s`, `from`, `to` | `run` DO, `running` DI, `motion` DI, `plugged` DI | `inlet`, `outlet` |
| `bulk_belt` | a belt conveyor carrying bulk | `length`, `width`, `speed_mps`, `capacity_kg_s`, `from`, `to` | `run` DO, `speed` AO, `running` DI, `rate` AI (kg/s, a belt scale, optional) | `inlet`, `outlet` |
| `slide_gate` | a gate under a bin's or supply's outlet: open or closed, a rated flow when open | `capacity_kg_s`, `travel_s`, `from`, `to` | `open` DO (held), `opened` DI, `closed` DI, `plugged` DI | `inlet`, `outlet` |

A `bin`'s level and weight are its own AI ports (decision 18: no `transmitter` reads a bin in
5.7a). The `bucket_elevator` and `bulk_belt` rows are 5.7b's. Every `plugged` port is optional;
it reads true while the link is blocked (decision 3), by a full outlet or a `plugged` fault.
Each type, when built,
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
- Checks gain bulk forms of the existing ones (5.7a): `never_empty` and `never_full` (bins), and
  `batch_within` (a bin's mass each time a link starts taking from it, within a tolerance of a
  target, with at least one such batch in the run). Each follows the existing no-easy-pass
  rules.
- Takeoff, scaffold, layout and the authoring guide do not learn the bulk types in 5.7a.

## Questions answered

The proposal's nine questions were answered by the owner on 2026-11-20 (decisions 11 to 19).
Two later tasks come from them: bulk into a liquid tank (PLAN 5.7c) and bagging, bulk into parts
(PLAN 5.7d).
