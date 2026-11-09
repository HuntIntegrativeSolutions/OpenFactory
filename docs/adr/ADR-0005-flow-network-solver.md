# ADR-0005: Flow network solver, a quasi-static incompressible network solved each tick

**Status:** Accepted, 2026-11-07 (all ten decisions of the proposal as recommended, plus two
additions).
**Deciders:** HIS Controls (T. Hunt), Claude Opus 5.5
**Appendix:** [Flow network solver design](../research/flownet-design.md), the proposal these
decisions were taken from. It carries the reasoning, the equations, the component schemas and
the scene checks in full; this record lists what was decided. The shipped scenes depart from
the proposal in sizes, durations and one added check kind; the appendix's "Departures" section
lists each, accepted 2026-11-08.
**Applies to:** PLAN Milestone 3: 3.1 (`core/flownet.py`), 3.2 (the process components), 3.3
(the process scenes), and 3.7 (per-binding range and units, which lands first).

## Context

Milestone 3 adds process simulation: tanks, valves, pumps and heaters, and the level, flow and
temperature loops a PLC closes around them. PLAN 3.1 asked for "a simple pressure-free model
first (flow = Cv × opening × pump curve)". That model cannot represent valves in series, a pump
working against a rising tank, gravity drain that slows with level, a flow splitting at a
junction, or reverse flow, and the 3.3 scenes need all of them. The appendix compares the two.

## Decision

1. **Model.** The liquid is incompressible and the network is solved quasi-statically once per
   tick with the Global Gradient Algorithm (Todini and Pilati; EPANET 2.2 manual, EPA/600/R-20/133,
   §12.1): link flows and junction heads, with tanks, supplies and open drains as nodes of known
   head. Dense Gaussian elimination in pure Python, no numerical dependency; warm-started from the
   last tick's flows; converged when the sum of absolute flow changes over the sum of absolute
   flows is below 1e-6 and every link's head error is below 1 mm; at most 40 iterations (a run
   that hits the cap is counted and logged, never stopped); EPANET's status checks (pump shutoff,
   check valves, empty and full tanks) with at most 5 re-solves per tick. Tank volumes advance by
   forward Euler with the tick split where a tank empties or fills, so volume balances exactly.
   The pressure-free model is the junction-free special case of the same code, tested first.
2. **Sources, sinks and junctions are component types:** `supply`, `drain` and `junction`, each
   with a position.
3. **Link-to-link ends.** A link's `from`/`to` may name another link, which joins them through an
   implicit junction at the downstream link's position.
4. **Demand drains.** A drain with no demand is open to atmosphere (known head). A drain with a
   demand (`demand_m3h` or a `schedule` of steps) is a junction with a pressure-dependent demand
   (EPANET's Wagner model, equations 12.13 to 12.17: full demand at or above `required_head_m`,
   none at or below `minimum_head_m`, the square root of the fraction between).
5. **Overflow.** A tank at its height overflows: the level holds, the excess is counted in the
   statistics, and the `never_overflow` check fails.
6. **Valve coefficient and failure.** `cv` in US gpm at 1 psi, `kv` accepted (`Kv = 0.865·Cv`).
   `valve_control.fail: closed | open | last` sets where the valve goes when its `position`
   command reads below 0 %, which a `float32` AO can carry. Under `int16_scaled` the raw count
   clamps at 0 (ADR-0003), a command never reads below 0 %, and `fail` never triggers; the
   component reference says so (addition, ruled 2026-11-07). `position` is optional: unbound,
   the valve holds `opening` (default 100 %), a hand valve.
7. **Out of scope for 3.1:** choked flow and cavitation, water hammer, Reynolds-dependent
   friction, compressible fluids, tank shapes other than vertical cylinder and box. Transmitter
   `signal_fail` reads below or above range (NAMUR NE 43 failure levels) under `float32`; under
   `int16_scaled` it clamps to the bottom or top of the range, as documented.
8. **Temperature and ingredients.** One well-mixed temperature and ingredient volume fractions
   per tank, constant water-like properties (NIST SRD 69 values at 20 °C), no transport delay in
   pipes.
9. **Lint, with the first component that needs each:** an unconnected nozzle or link end
   (error); a network with no node of known head (error); a pump whose design head is below the
   static lift it faces (warning); a tank time constant under 10 ticks (warning).
10. **Checks.** `level_within`, `never_overflow`, `never_dry` and `max_starts` join `checks:`,
    each graded from core state observed every tick, with the existing no-easy-pass rules (a
    tank that never reached its band fails; a pump that never started fails `max_starts`).

**Additions (ruled 2026-11-07):**

- **ISA tag names in the shipped process scenes.** Level switches and transmitters are tagged by
  ISA-5.1 function letters so the exported I/O list reads like a real one: `TK_02_LSH_DI` (level
  switch high), `TK_02_LSL_DI` (level switch low), `TK_01_LT_AI` (level transmitter).
- The `fail` position note in decision 6.

**Tick order.** The solve runs right after the conveyor kinematics in `LoadedScene.step`, with
the actuator states (valve openings, pump speeds, heater power) from the end of the last tick;
the components then step in scene order, so transmitters and switches read the levels this tick
produced. The PLC sees the process one tick behind its commands, as it sees a pusher.

**Parity.** The viewer draws process state only (levels, valve positions, pumps turning) and
has no process physics, so there is no parity trace. The solver is held to headless invariants
instead: volume balance every tick, junction continuity, head error, the iteration cap never hit
on a shipped scene, and byte-identical recordings.

## Consequences

- Process scenes behave with the right shape: square-root drain, pump and system curves meeting,
  series resistances adding, pumps starving on an empty tank. A PI loop tuned on them behaves
  like one tuned on a real level loop.
- About 300 lines of solver and its tests, instead of about 80 for the pressure-free model; one
  more determinism concern, met by fixed iteration order, tolerance and caps.
- Four component types beyond PLAN's original 3.2 list (`supply`, `drain`, `junction`, and the
  hand-valve use of `valve_control`).
- New state keys appear only on the new types, so every existing parity trace, recording and kit
  stays byte-identical.
- 3.7 (per-binding `range` and `units`) is a prerequisite: transmitters and tank levels need
  ranges set per tag for `int16_scaled`.
