# Jolt vs Rapier for the viewer physics (PLAN 2.9 spike)

**Recommendation: keep Rapier.** Neither switching criterion in PLAN 2.9 is met ("switch if
parity tightens or frame time halves"). Jolt was worse at the one behaviour that kept a parity
trace off the gate (the equal-speed seam), and at best 39 % faster per physics step, not
twice as fast. A switch would also mean rewriting `web/src/physics.ts` against an API with manual
memory management. Written 2026-11-04. No dependency was changed; the measurements come from
`docs/research/jolt-spike/bench.mjs`, run against packages installed in a scratch folder.

## What was compared

The PLAN asked for parity, frame time at 200 parts, bundle size, and API ergonomics. The spike
built the same conveyor setups in both engines in Node (v24.11.0, WSL2, 8 cores, same session):

- **Rapier** `@dimforge/rapier3d-compat` 0.21.0, set up as `web/src/physics.ts` does: belts are
  kinematic velocity-based bodies with 5 mm rounded edges, re-homed and given their surface
  velocity before every step. The spike leaves out the viewer's 2.9 seam fix, so it measures stock
  Rapier. The viewer's own numbers are quoted beside it.
- **Jolt** `jolt-physics` 1.1.0 (MIT; 1.1.1 was 12 days old), single-threaded `wasm-compat`
  build. Belts are static boxes, and the contact listener sets each belt contact's
  `mRelativeLinearSurfaceVelocity`: true conveyor surface velocity, as Jolt's conveyor sample
  does it.
- Both use μ 0.8, dt 0.02 s, gravity 9.81, and boxes 0.3 × 0.2 × 0.3 m.

The parity traces (`tools/parity_trace.py`) were not ported to Jolt: that would be most of a
rewrite of `physics.ts`. The spike measures the behaviours the traces depend on instead, each with
the headless model's answer.

## Results

| Measure | Core (headless) | Rapier 0.21 | Jolt 1.1.0 |
|---|---|---|---|
| Ticks for a box at rest to reach 99 % of 0.5 m/s | 4 (v/μg = 3.19 ticks) | 4 | 4 |
| Lag crossing an equal-speed seam at 0.5 m/s | 0 mm | 7.38 mm stock; **0.03 mm** in the viewer since the 2.9 seam fix | 15.75 mm; 14.3-25.4 mm over 9 belt and box convex radii (0, 5, 20 mm each) |
| Five boxes queued at a stop after 10 s, worst centre error | 0 | 4.07 mm | 1.14-1.26 mm |
| Same, worst tilt | 0 | 0.09° | 0.01-0.02° |
| Step time, 200 boxes on ten 13 m lanes, mean (3 runs × 1000 steps) | n/a | 2.00, 2.07, 2.42 ms | 1.26, 1.96, 1.99 ms |
| Same, p99 | n/a | 3.3, 3.6, 6.7 ms | 4.5, 7.0, 7.3 ms |
| Bundle, the form the viewer would load | n/a | 4,335,104 B (1,652,035 gzip), WASM embedded as base64 | `wasm-compat` 3,222,495 B (905,376 gzip); or `.js` 964,712 + `.wasm` 2,021,569 B (877,477 gzip together) |

What the numbers mean:

- **Parity.** Neither engine wins clearly.
  - Jolt's queue sits closer to the core's (1.2 mm against 4.1 mm), but every queue trace already
    passes its 2-tick gate under Rapier.
  - Jolt's seam loss is two to three times Rapier's stock loss. It would put `parity-turntable`
    back off the gate unless Jolt got its own seam fix, and none was found among the convex radius
    settings.
  - Rapier's loss comes from contact recycling. The viewer now keeps a part's contacts fresh near
    an equal-speed seam, and it loses 0.03 mm.
- **Frame time.** The physics step is not what limits the viewer's frame rate.
  - On the software renderer, rasterization takes about 200 ms of each frame (PLAN 2.9, 60 fps
    item). The viewer's whole main-thread state handling, Rapier included, takes 6-10 ms per state
    frame.
  - Jolt's step was 18-40 % faster in two of three runs and about equal in one, with a worse p99.
    That is not a halving.
  - Jolt's multi-threaded build was not measured. In a browser it needs `SharedArrayBuffer`, so
    the server would have to send cross-origin isolation headers (COOP/COEP) on every viewer page.
- **Bundle.** Jolt is about 45 % smaller gzipped (0.88-0.91 MB against 1.65 MB). Rapier already
  loads lazily, after the plant is drawn, so this is a load-time gain only.
- **Ergonomics.** Rapier's API is plain JavaScript objects with handles. Jolt's is the C++ API
  through Emscripten:
  - every `new Jolt.Vec3`, settings object and shape must be freed with `Jolt.destroy`;
  - contact callbacks receive raw pointers to wrap with `Jolt.wrapPointer`;
  - a world needs object-layer and broad-phase-layer tables before it can step.
  - Jolt's one clear gain is true surface velocity on contact, which Rapier JS lacks
    (`libraries.md`, "Rapier conveyor approach"). The kinematic-belt approach has not been the
    source of a parity failure: the seam loss came from contact recycling, not from the belt
    model.

## What would change the recommendation

- A parity trace failing because Rapier's kinematic belts cannot reproduce the core's friction
  model (none does today), or the 60 fps item finding the physics step, not rendering, limiting
  frame rate on the owner's machine.
- Before switching on either, port `parity-sorter` and `parity-turntable` to Jolt and measure in
  a browser, multi-threaded and with COOP/COEP; the spike's script is the starting point.

The PLAN also asked for ADR-0005; the 2026-11-04 ruling made this written comparison the
deliverable instead, with no dependency change.
