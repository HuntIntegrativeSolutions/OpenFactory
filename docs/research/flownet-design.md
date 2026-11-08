# Flow network solver design (PLAN 3.1 proposal)

**Status: accepted 2026-11-07, all ten decisions as recommended; recorded as
[ADR-0005](../adr/ADR-0005-flow-network-solver.md), of which this is the appendix.** The text
below is the proposal as ruled on. Milestone 3 tasks 3.1
(the solver), 3.2 (the process components), 3.3 (the scenes) and 3.7 (per-tag range and units)
all build on it. The decisions that need a ruling are collected at the end, each with a
recommendation.

**Recommendation in one paragraph.** Treat the liquid as incompressible and solve the network
quasi-statically once per tick. Each tick, pipes, valves and pumps are links between nodes.
Tanks, supplies and drains are nodes whose head is known at that moment, and junctions are nodes
whose head is unknown. The flow in every link and the head at every junction are solved with the
Global Gradient Algorithm, the method EPANET uses. Tank volumes are then advanced by forward
Euler with the tick's `dt`. The tick is split at the moment a tank would empty or overflow, so
volume balances exactly. Everything is pure Python with a dense solver and no new dependency.
The solve is deterministic, with a fixed iteration order, tolerance and iteration cap.

PLAN 3.1's own text asks for a simpler, pressure-free model first. The section "Why not the
pressure-free model first" explains why this proposal goes past it.

## 1. What has to be simulated

The 3.3 scenes are training exercises for level, flow and temperature control:

- `tank-level-onoff`: a pump started and stopped between level switches.
- `tank-level-pid`: an inflow valve throttled by a PID loop in the PLC.
- `two-tank-cascade`: two tanks in series.
- `batch-mix-heat`: fill two ingredients, mix, heat to a temperature.

A PLC programmer has to see the plant respond the way a real one does:

- Level rises faster with the valve further open.
- A tank drains faster when it is full than when it is nearly empty.
- A pump delivers less as the tank it fills gets higher.
- Two valves in series throttle each other.
- A pump that runs a tank dry stops delivering.

The simulation does not need to be accurate to a percent. It needs the right shape and the right
direction of every effect, so that a PI loop tuned on it behaves like one tuned on a real plant.

The time scales set the method:

- **Fast effects, finished well inside one 20 ms tick:**
  - Pressure waves in a few metres of pipe cross it in milliseconds.
  - The fluid in a short pipe comes up to a new flow in well under a second at these heads.
- **What the PLC sees (seconds to minutes):**
  - Tank levels.
  - Valve travel.
  - Pump speed ramps.
  - Heater temperatures.

So the network can be treated as being in steady state within each tick (quasi-static). The only
states that carry from tick to tick are those slow ones. EPANET's extended-period simulation is
built the same way, with hydraulic time steps of minutes to hours: a steady network solve, then
the tank levels advanced with the solved flows.

## 2. Graph model

### Nodes

| Node | Head during a solve | State carried between ticks | Scene type |
|---|---|---|---|
| Tank | Known: bottom elevation + level (open to atmosphere) | volume, temperature, ingredient fractions | `tank` |
| Supply | Known: elevation + pressure / (ρg) | none (an inexhaustible source) | `supply` (new, decision 2) |
| Drain, open (no demand) | Known: elevation (atmospheric) | volume received (statistics) | `drain` (new, decision 2) |
| Drain, with a demand | Unknown: a junction that draws its demand, reduced as its pressure falls (below) | volume received | `drain` with `demand_m3h` or `schedule` |
| Junction | Unknown: solved | none | `junction` (new, decision 2), or implicit (decision 3) |

**A drain with a demand is not a fixed-head node.**
- An open drain discharges to atmosphere at its elevation, so its head is known.
- A drain given a demand (a consumer drawing 3 m³/h) is a junction with a demand, and its head
  is solved.
- A fixed demand on a network that cannot supply it (the tank feeding it has emptied) would
  drive that head negative without limit.
- So the demand is pressure dependent, EPANET's model (manual §12.1, "Pressure dependent demand
  model", Wagner's equation, equations 12.13 to 12.17):
  - the full demand flows when the pressure head is at or above a required head `P_f`;
  - nothing flows at or below `P_0`;
  - between the two, the flow follows `((p − P_0)/(P_f − P_0))^0.5` of the demand.
- EPANET folds this into the same matrix as a virtual link per demand node, so it adds no
  second solver (decision 4).

**Tank nozzles.** Each tank has named nozzles at heights above its bottom.
- The defaults are `bottom` at 0 and `top` at the tank height.
- A link connects to a nozzle, written `TK_01.bottom`.
- A nozzle under the liquid surface sees the tank's head.
- A nozzle above the surface sees its own elevation, because liquid falls freely out of an
  inlet above the surface.
- So an inlet at the top delivers the same flow whatever the level, until the level reaches the
  nozzle.
- An outlet above the surface carries no outflow.

### Links

| Link | Head change from inlet to outlet for flow Q (m³/s, positive inlet to outlet) | Scene type |
|---|---|---|
| Pipe | loss `r·Q·abs(Q) + ε·Q` | `pipe` |
| Valve | loss `Q·abs(Q) / (k·f(x))²`; closed (x = 0): the link is closed | `valve_onoff`, `valve_control` |
| Pump | gain `ω²·(h0 − c·(Q/ω)ⁿ)`, Q ≥ 0 only (a pump does not run backwards) | `pump` |

Notes on each row:

- **Pipe.** `r` comes from Darcy-Weisbach with a fixed friction factor (default 0.02) plus the
  sum of minor-loss coefficients: `r = (f·L/D + ΣK) / (2·g·A²)`. The small linear term `ε·Q`
  keeps the slope above zero at zero flow, so the solver never divides by zero; EPANET 2.2
  added the same kind of linear relation at low flow for the same reason.
- **Valve.**
  - `x` is the opening, 0 to 1, and `f` is the inherent characteristic: `x` for linear, or
    `R^(x−1)` for equal percentage with rangeability R.
  - `k` follows from the valve's flow coefficient through the liquid sizing equation.
  - With `Kv` in m³/h at a 1 bar drop and `Kv = 0.865·Cv`, a drop of Δh metres of water
    passes `Q[m³/h] = Kv·f(x)·√(Δh / 10.2)`, taking 1 bar as 10.2 m of water at about 1000 kg/m³.
  - Choked flow and cavitation are left out (decision 7).
- **Pump.**
  - `h0` is the shutoff head and `ω` the relative speed (`speed` %, or a VFD's output, over
    100).
  - `c` and `n` are fitted to the curve.
  - A pump given only a design point gets EPANET's single-point curve: shutoff head 133 % of
    the design head, and the maximum flow at zero head twice the design flow.

A link between two nodes of known head, with nothing else attached, needs no network. Its flow
is the root of one equation, `h_in − h_out = loss(Q)`. Every PLAN 3.1 example ("flow = Cv ×
opening × pump curve") is this case, and the Global Gradient Algorithm reduces to exactly that
root finding when there are no junctions.

### Scene syntax

Each link names its two ends:

- **Two ends:** a link takes `from:` and `to:`.
- **What an end can be:** a node id, a tank nozzle (`TK_01.bottom`), or, with decision 3,
  another link.

```yaml
- {id: P_01, type: pump, from: TK_01.bottom, to: FV_01, design_flow_m3h: 6, design_head_m: 8}
- {id: FV_01, type: valve_control, from: P_01, to: TK_02.top, cv: 10}
```

**Load-time checks** (all are load errors, so a typo fails loudly):
- Every link end resolves.
- Every junction has at least two links.
- Every network has at least one node of known head.

## 3. The solve each tick

### Unknowns and equations

- **Unknowns:** the flow `Q` in every open link, and the head `h` at every junction.
- **Equations:**
  - Each link: `h_in − h_out = loss(Q)` (a pump's loss is minus its gain).
  - Each junction: flow in equals flow out. Junctions carry no demand of their own; a drain is
    the only consumer (decision 2).

This is EPANET's system: equations 12.1 to 12.4 of the EPANET 2.2 manual.

### Method: the Global Gradient Algorithm (Todini and Pilati, 1988)

This is EPANET 2.2's method (manual §12.1, equations 12.5 to 12.12). Each iteration has two
steps:

1. Linearize every link around its current flow and solve the linear system `A·h = F` for the
   junction heads.
   - `A` is symmetric, of size junctions × junctions.
   - Its diagonal is the sum of `1/p` over the links at a junction, and its off-diagonal terms
     are `−1/p`, where `p` is the slope `d(loss)/dQ` of each link.
2. Update every link's flow from the new heads: `Q ← Q − (loss(Q) − (h_in − h_out)) / p`.

After the first iteration this keeps flow continuity exactly at every junction (manual §12.1).

### Proposed settings

| Item | Proposal | Why |
|---|---|---|
| Linear solver | Dense Gaussian elimination with partial pivoting, pure Python | EPANET uses a sparse Cholesky decomposition (manual §12.1, "Linear equation solver") because its networks have thousands of nodes. A training scene has fewer than about 20 junctions, so a dense solve costs microseconds and needs no numpy (none of the core's dependencies is a numerical library). |
| Starting flows | The previous tick's flows; on the first tick, each link's design flow or a velocity of 0.3 m/s | Levels move little between ticks, so warm starts converge in two or three iterations. |
| Convergence | Sum of abs(ΔQ) over sum of abs(Q) < 1e-6, as EPANET's ACCURACY criterion but 1000 times tighter (EPANET suggests 0.001); also every link's head error < 1 mm | Tight because the result is integrated every tick, and level drift from a loose solve would add up. |
| Iteration cap | 40 | Runs that hit the cap are counted in the statistics block and logged once per run. The last iterate is used, so the tick still completes and the simulation never stops. |

**Status checks** (EPANET manual §12.1, "Status checks"). They run after convergence, and the
solve repeats if any link's status changes, at most 5 times per tick:
- A pump whose required gain exceeds its shutoff head stops delivering, which behaves as a
  check valve.
- A link drawing from an empty tank closes.
- A link filling a full tank closes, if the tank cannot overflow (decision 5).
- A `check_valve: true` pipe closes on reverse flow.

### Tank integration

Within the tick:

1. **Solve:** the network solves with tank heads from the start of the tick.
2. **Net flow:** each tank's net inflow is `Q_net`.
3. **Volume:** forward Euler gives `V(t+dt) = V(t) + Q_net·dt`, EPANET's extended-period update
   (manual §12.1, "Extended period analysis").
4. **Level:** the new volume gives the new level, `V/A` for a vertical tank.

**Splitting the tick.** EPANET shortens its time step to the moment a tank fills or empties. A
fixed 50 Hz tick cannot do that, so the tick is split instead:
1. Find the first tank that would cross empty or full within the tick, and the fraction `τ` of
   `dt` until it does.
2. Advance every tank by `τ·dt`.
3. Apply that tank's status change.
4. Solve again and advance the rest of the tick.

This is deterministic and bounded: at most one split per tank per tick, five in total.

**Stability.** Explicit Euler is stable here: a tank's time constant (area over the slope of its
outflow against level) is tens of seconds in any sensible scene, against a 0.02 s step. Lint
could warn when a scene's smallest time constant is under 10 ticks (decision 9).

**Exact volume balance.** With the split, the change in all tank volumes in a tick equals supply
inflow minus drain outflow minus overflow, times `dt`, to rounding. A unit test asserts this
every tick (see §8).

### Tick order

`LoadedScene.step` today runs:
1. Kinematics, which moves parts using the actuators' state from the end of the last tick.
2. Composite logic.
3. Each component, in scene order.
4. The transform stage.

The flow solve goes right after kinematics and follows the same rule. It uses valve openings,
pump speeds and heater power as they stood at the end of the last tick. The components then step
in scene order:
- Valves travel toward their commands.
- Pumps ramp.
- Transmitters and level switches read the levels this tick produced.

The PLC therefore sees the process one tick behind its commands, exactly as it sees a pusher
move. There is no new hook, and step order in the scene file still never changes a tag's value.

**Cost.** Ten links, five junctions and three iterations come to a few hundred floating-point
operations per tick in Python, well under the existing conveyor step (about 2.5 ms at 200
parts). A `flow-` scene joins `tools/bench.py` with 3.1, so the tick-cost gate covers it.

## 4. Fluid properties

**One liquid, water-like, at constant properties.**
- Values: density about 998 kg/m³, kinematic viscosity about 1.0 mm²/s, specific heat about
  4.18 kJ/(kg·K), all at 20 °C. They are taken from NIST Standard Reference Database 69 (the
  NIST Chemistry WebBook's fluid properties) when 3.1 is written, and the exact values are
  cited in the module.
- Density does not change with temperature, and the friction factor does not depend on Reynolds
  number. A training plant with a 10 % error in friction is still a plant a PI loop can be tuned
  on.
- EPANET handles low flow with a laminar formula. Here the linear `ε·Q` term (§2) plays that
  part.

**Temperature.**
- Each tank holds one well-mixed temperature `T`.
- Each supply delivers at its `temperature_c`.
- The heat balance per tick is `m·cp·dT/dt = P_heater + Σ ṁ_in·cp·(T_in − T) − UA·(T − T_ambient)`,
  integrated with the same split tick as the volume.
- Pipes carry the temperature of the node they draw from, with no transport delay (decision 8).

**Ingredients** (for `batch-mix-heat`):
- Each supply names an ingredient, and each tank tracks the volume fraction of each ingredient,
  well mixed.
- Fractions affect nothing physical: same density, same heat capacity.
- They can be read by a `transmitter` (`measures: TK_01.fraction.syrup`), and a check can ask
  for a recipe (decision 8).

## 5. Valves, pumps and the PID loop through the PLC

The simulator never runs a control algorithm. In a closed loop, each part has its own role:

- **The process variable.** A `transmitter` (or a tank's own `level` port) writes the measured
  value as an AI in engineering units, with an optional first-order lag and noise.
  - Under `int16_scaled`, the raw counts follow ADR-0003: 0 to 32767 for the tag's range, the
    same convention as a 4 to 20 mA input card.
  - The range is the transmitter's `range`, or the binding's `range:` from 3.7.
- **The controller.** The PLC's PID (`P_PIDE` in the 3.4 PlantPAx demo) or the stand-in script's
  PI.
  - The stand-in script for `tank-level-pid` implements a textbook PI with anti-windup
    (Åström and Murray, *Feedback Systems*, chapter "PID Control", section "Integrator windup").
  - So `openfactory test` can grade the scene in CI, as every shipped scene's script does today.
- **The manipulated variable.** `valve_control.position` is an AO, 0 to 100 %. The valve stem
  follows the command with a rate limit (`stroke_s` for full travel) and a first-order lag
  (`lag_s`). `position_fb` reports where the stem really is, so a PLC can alarm on deviation.
- **Loop timing.**
  - The loop sees one tick of simulation delay, plus the PLC's scan and the protocol poll
    (100 ms is typical for Modbus).
  - Tank time constants of 20 s to several minutes make that delay negligible, which is what a
    real level loop sees.
  - Scenes declare no deadtime of their own; the transmitter's `lag_s` is the knob for a harder
    loop.

**Disturbances.** A level loop needs a load change to be an exercise. Drains take a demand
schedule: a list of `[time_s, m³/h]` steps, repeating if `repeat: true`. This follows EPANET's
demand time patterns. The schedule is part of the scene, so a run is reproducible and gradable.
Drains are the only nodes that consume by demand (decision 4).

## 6. Determinism and replay

The existing rules (PLAN §3.2) hold unchanged:

- **No clock.** `step` never reads the clock.
- **Fixed order.**
  - Components step in scene order.
  - The solver visits links and junctions in scene order.
  - It sums in that order.
  - It uses a fixed tolerance, iteration cap and status-check limit, so two runs of a scene on
    one machine produce identical floats.
- **Floating point.** The solver uses `+ − × ÷` and `math.sqrt`, which IEEE 754 rounds
  correctly, plus `math.pow` for pump curve exponents and equal-percentage characteristics. The
  kinematics already relies on `math.sin` and `math.cos` from the same platform library, so the
  flow network adds no new class of cross-platform difference.
- **Randomness.** Transmitter noise draws from a per-component generator seeded from the scene
  seed and the component id, as fault noise does today. Adding a transmitter shifts no other
  component's random draws.
- **Recordings** (2.12).
  - A recording carries I/O changes and state frames, and replay never re-solves.
  - New state keys appear only on the new types, so every existing parity trace, recording and
    kit stays byte-identical. This is the same rule as 2.5b.

## 7. Parity: the viewer only animates

The viewer has no physics for the process plant. It draws:

- each tank's liquid at its `level_m`, colored by temperature if the scene asks;
- valve stems or handles at their positions;
- pumps turning while `running`;
- optionally, moving texture on pipes, at a speed proportional to `flow_m3h`.

Every number comes from the state frame, so there is no browser model to disagree with the core,
and no parity trace is proposed. What the parity traces guarded against (the screen showing what
the PLC does not see) cannot happen when the screen is a pure rendering of core state. The
viewer's tests check that the drawn liquid height equals `level_m` and that a screenshot of each
3.3 scene shows the levels moving.

In place of parity, the solver gets headless invariants as unit tests (§8).

## 8. How 3.1 would be tested

- **Single links against closed form:**
  - A tank draining through an orifice-like valve follows Torricelli's law, `Q = Cd·a·√(2gh)`,
    and its level against time matches the analytic drain time within 0.5 %.
  - A pump into a tank against a static head settles where the pump curve meets the system
    curve.
- **Networks against hand solutions:** two pipes in parallel split flow inversely to the square
  roots of their resistances, and two valves in series add their resistances.
- **Invariants every tick, on every 3.3 scene, for 300 s:**
  - Volume balance closes to 1e-9 m³.
  - Continuity holds at every junction to 1e-12 m³/s.
  - Every converged link has a head error under 1 mm.
  - The iteration cap is never hit.
- **Determinism:** two recordings of each 3.3 scene are byte-identical, the 2.12 criterion.
- **Status logic:** a tank pumped dry closes the pump's suction and the pump delivers nothing
  until the tank refills; a check valve stops reverse flow; a full tank closes its inflow (or
  overflows, per decision 5).

## Why not the pressure-free model first

PLAN 3.1 says "simple pressure-free model first (flow = Cv × opening × pump curve)", computing
each link's flow on its own from its own settings. That model is fine for the first two scenes'
simplest versions, but it breaks on behaviour every 3.3 scene needs:

| Behaviour | Pressure-free model | Quasi-static solve |
|---|---|---|
| Gravity drain slows as the tank empties (√h) | Needs a special case per link | Falls out: the tank's head drives the link |
| Pump delivers less against a higher tank | Needs the downstream level fed into the curve by hand | Falls out: the curve meets the system |
| Two valves in series | Wrong: each passes its own Cv flow, so the pair passes more than either | Right: resistances add |
| One pump feeding two branches | Undefined: how the flow splits needs heads | Solved at the junction |
| Reverse flow, check valves | Not represented | Status checks |
| A pump starving on an empty tank | Special case | Status check, as EPANET does it |

**Cost of going straight to the solve:**
- About 300 lines of solver and a careful test suite, instead of about 80.
- One more determinism concern (the iteration rules in §6).

**Cost of building the pressure-free model first:**
- `two-tank-cascade` and `batch-mix-heat` have to wait for a second model anyway.
- Every scene written for the first model needs re-tuning when the second replaces it, because
  levels and timings change.

**Recommendation:** go straight to the solve, and keep the pressure-free case as what it really
is: the junction-free special case of the same code, tested first.

## Proposed component schemas (3.2)

Existing names are reused where they fit:
- Parameters: `position`, `rotation_deg`, `description`, `io`.
- Ports: the `run`/`running` and `speed` pair from the conveyors.
- Fault kinds: the PLAN 2.6 taxonomy.

`*` marks a required port. Units in names follow the existing style (`speed_mps`,
`extend_time_s`).

Analog ports carry a range for `int16_scaled`. 3.7 lands first (as PLAN 3.7 requires) and adds
the per-binding override, `{tag: TK_01_LVL_AI, range: [0, 2.0], units: m}`. Each default range
below is the one used when the binding gives none.

```yaml
- type: tank                   # node; origin = centre of the tank bottom, on its floor
  shape: vertical_cylinder     # or box; area from diameter, or from length x width
  diameter: 1.0                # m (box: length, width)
  height: 2.0                  # m; overflow at this level (decision 5)
  initial_level: 0.5           # m
  initial_temperature_c: 20
  nozzles: {bottom: 0.0, top: 2.0}   # name: height above the bottom, m; defaults shown
  hi_level: 1.8                # m, where hi_sw turns on (falls 0.02 m lower: switch deadband)
  lo_level: 0.2                # m, where lo_sw turns on
  io: {level: AI, hi_sw: DI, lo_sw: DI, temp: AI}   # level 0-height m; temp 0-100 C
  # faults: stuck_on/stuck_off (switches), drift/noisy (level), misaligned (switch heights)
  # state: level_m, volume_m3, temperature_c, overflowing (only on tanks)

- type: supply                 # node, new (decision 2); origin = connection point
  pressure_bar: 2.0            # gauge, at the connection point's elevation
  temperature_c: 15
  ingredient: water            # name for mixing (decision 8)
  io: {}                       # no ports; a valve in front of it is the control element

- type: drain                  # node, new (decision 2); origin = connection point
  demand_m3h: null             # null: open to atmosphere, takes whatever arrives (known head)
  schedule: null               # or [[0, 2.0], [120, 4.0], [300, 2.0]]: demand steps (s, m3/h)
  repeat: false
  required_head_m: 2.0         # P_f: full demand at or above this pressure head (decision 4)
  minimum_head_m: 0.0          # P_0: no flow at or below it
  io: {}                       # with a demand, the drain is a junction (section 2)

- type: junction               # node, new (decision 3: or implicit only)
  io: {}                       # pressure read through a transmitter (measures: J_01.pressure)

- type: pipe                   # link; origin = its from end; drawn along path
  from: TK_01.bottom           # required
  to: J_01                     # required
  diameter: 0.05               # m
  length: null                 # m; default the drawn length of path
  path: []                     # via points, scene coordinates, for the viewer
  friction: 0.02               # Darcy friction factor
  minor_loss: 1.5              # sum of K (entry, bends, exit)
  check_valve: false
  io: {}
  # state: flow_m3h (only on pipes)

- type: valve_onoff            # link; origin = valve body centre
  from: J_01                   # required
  to: TK_02.top                # required
  cv: 10                       # US gpm at 1 psi (decision 6: or kv)
  travel_s: 2.0                # full open or close; opening ramps linearly
  io: {open: DO*, opened: DI, closed: DI}     # both false mid-travel, as the pusher's limits
  # faults: jammed (stops where it is, limits honest), slow, stuck_on/stuck_off (limits)

- type: valve_control          # link; origin = valve body centre
  from: SUP_01                 # required
  to: TK_01.top                # required
  cv: 10
  characteristic: equal_percentage   # or linear
  rangeability: 50
  stroke_s: 10.0               # full stroke at the rate limit
  lag_s: 0.5                   # first-order lag of the actuator
  fail: closed                 # position when the command reads below 0 % or fails (decision 6)
  opening: 100                 # % held when position is unbound: a hand valve (as an unbound
                               # conveyor speed means 100 %)
  io: {position: AO, position_fb: AI}          # both 0-100 %
  # faults: jammed (stem sticks), slow, drift (position_fb), noisy

- type: pump                   # link; origin = pump centre
  from: TK_01.bottom           # required
  to: J_01                     # required
  design_flow_m3h: 6.0
  design_head_m: 8.0           # single-point curve: 133 % shutoff, 200 % max flow (EPANET)
  curve: null                  # or [[0, 10.6], [6, 8], [12, 0]] (m3/h, m): three-point fit
  ramp_s: 1.0                  # 0 to full speed
  io: {run: DO*, speed: AO, running: DI, flow: AI}   # speed 0-100 %, flow 0-2x design m3/h
  # running follows run (contactor auxiliary), like belt_conveyor
  # faults: jammed (seized: no flow, running honest), slow (worn: head x factor),
  #         stuck_on/stuck_off (running), drift/noisy (flow)
  # state: running, speed, flow_m3h, dry (suction tank empty)

- type: heater                 # not in the network; heats one tank; origin = element centre
  tank: TK_01                  # required
  rated_kw: 6.0
  min_level: 0.3               # m; below it the element is uncovered and adds no heat
  ua_w_per_k: 15               # heat loss of the tank to ambient
  ambient_c: 20
  io: {"on": DO*, power: AO, temp: AI}   # power 0-100 % (unbound: 100 %); temp 0-100 C
  # faults: stuck_off (element open), drift/noisy (temp)
  # state: on, power_kw, uncovered

- type: transmitter            # reads one process value; origin = where it is drawn
  measures: TK_01.level        # level | temperature | fraction.<name> on a tank,
                               # flow on a link, pressure on a junction
  range: [0, 2.0]              # engineering units of the pv; the AI tag's range
  units: m
  lag_s: 0.0                   # first-order filter
  noise: 0.0                   # standard deviation as a fraction of span
  io: {pv: AI*}
  # faults: drift, noisy, stuck_on/stuck_off (pv reads top or bottom of range),
  #         signal_fail (decision 7: NAMUR NE 43 failure levels)

- type: vfd_motor              # drives one pump (or conveyor); origin = drive cabinet
  drives: P_01                 # required: a pump or a belt/roller conveyor
  rated_kw: 2.2
  trip_s: 5.0                  # time above rated power before an overload trip
  accel_s: 3.0                 # 0 to full speed
  io: {run: DO*, speed: AO*, reset: DO, running: DI, speed_fb: AI, fault: DI}
  # speed and speed_fb 0-100 %; the driven pump's own run/speed ports must be unbound
  # fault latches on overload; cleared by reset, or by run falling when reset is unbound
  # faults: jammed (motor stalls: overload trips), drift (speed_fb)
```

Shaft power for the overload is `ρ·g·Q·H / η`, with an efficiency `efficiency` (default 0.6) on
the pump.

## The first three scenes (3.3) and their checks

The existing check kinds (`parts_reached`, `none_lost`, `max_cycle_s`) are about parts. Process
scenes need four new ones (decision 10). They follow the same rules as the existing checks:
optional keys, names checked at load, and no easy passes.

| Key | Passes when |
|---|---|
| `level_within: {TK_01: {band: [lo, hi], after_s: T}}` | From `T` to the end of the run the tank's level stays in the band, **and** it got there: the level at `T` is in the band. A tank that never filled fails. |
| `never_overflow: [TK_01, ...]` | The tank never reached its height (no overflow volume). |
| `never_dry: [P_01, ...]` | The pump never ran with its suction tank empty. |
| `max_starts: {P_01: N}` | The pump started at most N times in the run (short-cycling). It must also have started at least once, so a pump that never runs fails. |

### `tank-level-onoff`

**Scene.**
- A supply tank `TK_01` (2 m, starting at 1.5 m) feeds pump `P_01` into the process tank
  `TK_02` (2 m) through a top inlet.
- `TK_02` sits above `TK_01` and drains back into it by gravity, from `TK_02.bottom` through
  `HV_02` to `TK_01.top`. `HV_02` is a hand valve: a `valve_control` with its `position` port
  unbound, held at `opening: 40` (see the schema). The liquid circulates, and nothing is
  supplied or drained.
- `TK_02` has `hi_sw` at 1.6 m and `lo_sw` at 0.6 m; the pump has `run` and `running`.
- **The exercise:** keep `TK_02` between the switches, starting the pump at low and stopping it
  at high.
- **Stand-in script:** 10 lines, latching on the two switches.

```yaml
checks:
  duration_s: 300               # a fill and drain cycle takes about 2 minutes
  level_within: {TK_02: {band: [0.5, 1.7], after_s: 60}}
  never_overflow: [TK_02]
  never_dry: [P_01]
  max_starts: {P_01: 6}         # set 25-30 % above the script's measured count, as max_cycle_s
```

### `tank-level-pid`

**Scene.**
- Supply `SUP_01` (2 bar) feeds `TK_01` (2 m) through `FV_01`, a `valve_control` (Cv 10, equal
  percentage, stroke 10 s).
- `TK_01` drains through a pipe to `DR_01`, whose demand steps from 2 to 4 m³/h at 180 s and
  back at 360 s: the load disturbance.
- `LT_01` is a transmitter on the level, range 0 to 2 m, lag 1 s, noise 0.2 % of span.
- **The exercise:** hold 1.0 m with a PI loop: `LT_01.pv` to `FV_01.position`.
- **Stand-in script:** a textbook PI with anti-windup (§5).

```yaml
checks:
  duration_s: 480
  level_within: {TK_01: {band: [0.95, 1.05], after_s: 120}}   # through both demand steps
  never_overflow: [TK_01]
```

The band and the disturbance sizes are first proposals, to be set from the script's measured
run. The band must still fail an on/off program and a P-only loop with its offset, both of which
a test runs to prove it.

### `two-tank-cascade`

**Scene.**
- Supply `SUP_01` feeds `TK_01` through `FV_01` (`valve_control`).
- `TK_01` drains by gravity through a pipe from its bottom into the top of `TK_02`, set lower,
  so the flow between them depends on `TK_01`'s level alone (non-interacting tanks).
- `TK_02` drains to `DR_01` with a demand step at 240 s.
- Transmitters on both levels.
- **The exercise:** hold `TK_02` at 1.0 m.
  - A single PI loop on `TK_02` works but is sluggish (two lags in series).
  - The intended answer is cascade control: an outer loop on `TK_02` sets the setpoint of an
    inner loop on `TK_01`.
- **Stand-in script:** the cascade.

```yaml
checks:
  duration_s: 600
  level_within: {TK_02: {band: [0.95, 1.05], after_s: 200}}
  never_overflow: [TK_01, TK_02]
```

`batch-mix-heat`, the fourth 3.3 scene, needs ingredients, the heater and a recipe check
(decision 8). It is left for after the rulings.

## Decisions for you (recommendation first)

1. **Model.** Go straight to the quasi-static network solve (Global Gradient Algorithm), with
   the pressure-free case as its junction-free special case, rather than building the
   pressure-free model first as PLAN 3.1 now says. *Alternative:* the pressure-free model first,
   with `two-tank-cascade` and `batch-mix-heat` waiting for the second model.
2. **Sources and sinks as types.** Add `supply`, `drain` and `junction` to the 3.2 list. Each has
   a position (the viewer draws a connection point, and lint can check distances), and
   `junction` lets a transmitter read a pressure. *Alternative:* a `flownet:` block in the scene
   for nodes, outside `components`.
3. **Link-to-link ends.** Allow `to: FV_01` to join a pump straight to a valve, with an implicit
   junction between them at the downstream link's position. *Alternative:* require an explicit
   `junction` between any two links, which is clearer in the file but tedious.
4. **Demand drains.** A drain with a demand is a junction with a pressure-dependent demand
   (EPANET's Wagner model, §2), so a consumer on an emptied tank slows to nothing instead of
   driving heads negative. `tank-level-pid` and `two-tank-cascade` rely on it for their load
   steps. *Alternative:* a fixed demand that the status checks cut off when its supply tank
   empties. Simpler, but the demand then jumps from full to zero, which a level loop sees as a
   second disturbance.
5. **Overflow.** A tank at its height overflows: the level holds at the height, the excess goes
   to an overflow count in the statistics, and `never_overflow` fails. *Alternative:* EPANET's
   default, where a full tank closes its inflow links. That is gentler and hides a missing high
   interlock, which is the mistake the exercises should expose.
6. **Valve coefficient and failure.** Take `cv` (US gpm at 1 psi), as most North American
   datasheets give it, and accept `kv` as an alternative key (`Kv = 0.865·Cv`). `fail: closed |
   open | last` sets where a `valve_control` goes when its AO reads below 0 %. That is the only
   signal-loss case a Modbus AO can express.
7. **Out of scope for 3.1.** Choked flow and cavitation in valves, water hammer, Reynolds
   dependence of friction, compressible fluids, tank shapes other than vertical cylinder and
   box. Transmitter `signal_fail` uses the NAMUR NE 43 failure levels (at or below 3.6 mA, at or
   above 21 mA) as values below or above range under `float32`. Under `int16_scaled` the raw
   count clamps at 0 or 32767 (ADR-0003), so a PLC sees the bottom or top of the range: say so
   in the component reference and do not extend the raw range.
8. **Temperature and ingredients.** One well-mixed temperature and ingredient fractions per
   tank, constant properties, no transport delay in pipes. This is enough for `batch-mix-heat`;
   its recipe check (`fractions_within`, `temperature_within`) is proposed with that scene.
9. **Lint.** New checks with 3.1: an unconnected nozzle or link end (error), a network with no
   node of known head (error), a pump whose design head is below the static lift it faces
   (warning), and a tank time constant under 10 ticks (warning).
10. **Checks.** Add `level_within`, `never_overflow`, `never_dry` and `max_starts` to `checks:`
    with 3.3, as defined above. Each is graded from core state observed every tick during the
    run, as `max_cycle_s` is: tank levels, and pump starts counted the way the statistics block
    already counts actuator cycles (`core/stats.py`, which gains pumps).

## Sources

All public:

- **EPANET 2.2 user manual:** L. A. Rossman, H. Woo, M. Tryby, F. Shang, R. Janke, T. Haxton,
  *EPANET 2.2 User Manual*, U.S. Environmental Protection Agency, EPA/600/R-20/133, 2020. Read
  2026-11-07 from the EPA's
  [PDF](https://www.epa.gov/system/files/documents/2021-07/epanet_users_manual_2.2.0-1.pdf).
  - That address served the Spanish-language edition, with the same document number and
    equation numbering.
  - Used: chapter 12, "Analysis Algorithms", §12.1:
    - the head-loss and pump relations, equations 12.1 to 12.4;
    - the pressure-dependent demand model, equations 12.13 to 12.17;
    - the Global Gradient Algorithm, equations 12.5 to 12.12, with the linear equation solver
      and the initial flows;
    - the status checks, including the check valve, pump and tank-link rules;
    - the convergence criteria (ACCURACY, suggested value 0.001);
    - the extended-period analysis (manual pp. 115 to 126).
  - Also used: chapter 3, "Pump curve", for the single-point curve (133 % shutoff head, twice
    the design flow; manual p. 22).
  - Also used: the demand time patterns from the tutorial.
- **Global Gradient Algorithm:** E. Todini and S. Pilati, "A gradient method for the solution of
  looped pipe networks", in B. Coulbeck and C. H. Orr (eds.), *Computer Applications in Water
  Supply*, vol. 1, 1988, as cited by the EPANET manual. Not read directly.
- **Liquid valve sizing:** ANSI/ISA-75.01.01 (IEC 60534-2-1 Mod), *Flow Equations for Sizing
  Control Valves*. The standard is sold, not read.
  - The liquid sizing form and `N1 = 0.865` for m³/h and bar (`Kv = 0.865·Cv`) are as given by
    [Che Calc, liquid control valve sizing](https://checalc.com/liquid_cv_sizing.html), which
    states them per ISA-75.01.
  - Verify against the standard before 3.2 documents valve sizing as conforming.
- **Torricelli's law and the discharge coefficient:**
  [Wikipedia, "Torricelli's law"](https://en.wikipedia.org/wiki/Torricelli%27s_law), used for
  the analytic drain-time test.
- **PID and integrator windup:** K. J. Åström and R. M. Murray, *Feedback Systems: An
  Introduction for Scientists and Engineers*, chapter "PID Control", section "Integrator
  windup", free online from the authors
  ([Caltech wiki page](https://www.cds.caltech.edu/~murray/amwiki/PID_Control.html)).
- **NAMUR NE 43 signal levels:** as stated in manufacturer documentation, e.g. the Pyromation
  T71 transmitter datasheet ([tt07.pdf](https://pyromation.com/Catalog/tt07.pdf)): sensor
  failure at or below 3.6 mA or at or above 21.0 mA. The NAMUR recommendation itself was not
  read.
- **Water properties:** NIST Standard Reference Database 69, the NIST Chemistry WebBook,
  [Thermophysical Properties of Fluid Systems](https://webbook.nist.gov/chemistry/fluid/). The
  values in §4 are rounded; the exact values are to be read there and cited in the module when
  3.1 is written.
- **Affinity laws and pump and system curves:** U.S. Department of Energy and Hydraulic
  Institute, *Improving Pumping System Performance: A Sourcebook for Industry*, 2nd ed.
  ([PDF](https://www.energy.gov/sites/prod/files/2014/05/f16/pump.pdf)). It is background for
  scaling the pump curve by `ω` (EPANET's equation 12.2 already uses relative speed). Its text
  was not read beyond the contents; read it before 3.2's `vfd_motor` power model.
