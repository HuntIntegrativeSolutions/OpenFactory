// PLAN 1.12 parity: replay the headless model's inputs (tools/parity_trace.py) through the
// Rapier plant and require every photo-eye edge and belt checkpoint to agree within the trace's
// tolerance_ticks, max(2 ticks, 25 % of the computed ramp time) (tools/parity_trace.py).
// tests/integration/test_kinematic_parity.py checks the traces are current, runs this file, and
// reads the measured deltas from the "PARITY_REPORT {json}" line each trace prints.
import { describe, expect, it } from "vitest";
import type { SceneDocument, Vec3 } from "./api";
import sorter from "./fixtures/parity-sorter.json";
import sorterMu03 from "./fixtures/parity-sorter-mu0.3.json";
import queue from "./fixtures/parity-queue.json";
import stopper from "./fixtures/parity-stopper.json";
import stopperTall from "./fixtures/parity-stopper-tall.json";
import diverter from "./fixtures/parity-diverter.json";
import turntable from "./fixtures/parity-turntable.json";
import pickPlace from "./fixtures/parity-pick-place.json";
import { loadRapier, PlantPhysics } from "./physics";

interface Trace {
  scene: SceneDocument;
  dt: number;
  ticks: number;
  friction: number;
  ramp_ticks: number;
  tolerance_ticks: number;
  inputs: {
    tick: number;
    belts?: Record<string, number>;
    pushers?: Record<string, number>;
    stoppers?: Record<string, number>;
    diverters?: Record<string, number>;
    turntables?: Record<string, number>;
    spawn?: { id: string; size: Vec3; position: Vec3 }[];
    remove?: string[];
    /** Parts the core places (PLAN §3.3): its position after this tick, applied during the step. */
    follow?: Record<string, Vec3>;
    /** Parts handed back to Rapier before this step, with their velocity. */
    release?: Record<string, Vec3>;
  }[];
  edges: Record<string, [number, boolean][]>;
  checkpoints: Record<string, Record<string, number>>;
  /** false: a known model difference, reported but not held to the tolerance. */
  gate?: boolean;
}

const TRACES: Record<string, Trace> = {
  "parity-sorter": sorter as unknown as Trace,
  "parity-sorter-mu0.3": sorterMu03 as unknown as Trace,
  "parity-queue": queue as unknown as Trace, // PLAN 2.0: a placed queue released onto a faster belt
  "parity-stopper": stopper as unknown as Trace, // PLAN 2.5: a queue formed against a stopper
  "parity-stopper-tall": stopperTall as unknown as Trace, // the same with 0.4 m boxes (no tipping)
  "parity-diverter": diverter as unknown as Trace, // PLAN 2.5: a box slid along a diverter arm
  "parity-turntable": turntable as unknown as Trace, // boxes straight on and turned 90 degrees
  "parity-pick-place": pickPlace as unknown as Trace, // boxes lifted across by a gantry
};

function load(name: string): Trace {
  return TRACES[name];
}

async function replay(trace: Trace) {
  const R = await loadRapier();
  const physics = new PlantPhysics(R, { components: trace.scene.components, friction: trace.friction, dt: trace.dt });
  const byTick = new Map(trace.inputs.map((entry) => [entry.tick, entry]));
  const edges: Record<string, [number, boolean][]> = Object.fromEntries(Object.keys(trace.edges).map((id) => [id, []]));
  const checkpoints: Record<string, Record<string, number>> = {};
  const marks = Object.keys(Object.values(trace.checkpoints)[0] ?? {}).map(Number);
  let maxDrift = 0;
  for (let k = 1; k <= trace.ticks; k++) {
    const input = byTick.get(k);
    for (const [id, v] of Object.entries(input?.belts ?? {})) physics.setBelt(id, v);
    for (const [id, s] of Object.entries(input?.pushers ?? {})) physics.setPusher(id, s);
    for (const [id, f] of Object.entries(input?.stoppers ?? {})) physics.setStopper(id, f);
    for (const [id, a] of Object.entries(input?.diverters ?? {})) physics.setDiverter(id, a);
    for (const [id, a] of Object.entries(input?.turntables ?? {})) physics.setTurntable(id, a);
    for (const id of input?.remove ?? []) physics.removePart(id);
    for (const p of input?.spawn ?? []) physics.addPart(p.id, p.size, p.position);
    for (const [id, v] of Object.entries(input?.release ?? {})) physics.release(id, v);
    for (const [id, pos] of Object.entries(input?.follow ?? {})) physics.follow(id, pos);
    for (const edge of physics.step()) edges[edge.id]?.push([k, edge.blocked]);
    for (const id of physics.partIds()) {
      const x = physics.part(id)!.position[0];
      const seen = (checkpoints[id] ??= {});
      for (const m of marks) if (seen[String(m)] === undefined && x >= m) seen[String(m)] = k;
    }
  }
  for (const id of physics.partIds()) maxDrift = Math.max(maxDrift, Math.abs(physics.part(id)!.position[2]));
  physics.free();
  return { edges, checkpoints, maxDrift };
}

describe.each(Object.keys(TRACES))("Rapier vs kinematics.py parity (%s)", (name) => {
  it("agrees within tolerance_ticks on every photo-eye edge and belt checkpoint", async () => {
    const trace = load(name);
    const tolerance = trace.tolerance_ticks;
    expect(tolerance).toBeCloseTo(Math.max(2, 0.25 * trace.ramp_ticks), 5); // the formula, not a stale constant
    const got = await replay(trace);
    const rows: { what: string; kinematic: number; rapier: number | null; delta: number | null }[] = [];
    for (const [eye, expected] of Object.entries(trace.edges)) {
      expected.forEach(([tick, blocked], i) => {
        const match = got.edges[eye][i];
        const ok = match && match[1] === blocked;
        rows.push({ what: `${eye} ${blocked ? "blocked" : "clear"} #${i + 1}`, kinematic: tick, rapier: ok ? match[0] : null, delta: ok ? match[0] - tick : null });
      });
      expect(got.edges[eye].length, `${eye} edge count`).toBe(expected.length);
    }
    for (const [part, marks] of Object.entries(trace.checkpoints)) {
      for (const [x, tick] of Object.entries(marks)) {
        const r = got.checkpoints[part]?.[x] ?? null;
        rows.push({ what: `${part} x>=${x}`, kinematic: tick, rapier: r, delta: r === null ? null : r - tick });
      }
    }
    const worst = Math.max(...rows.map((r) => (r.delta === null ? Infinity : Math.abs(r.delta))));
    const summary = { trace: name, friction: trace.friction, toleranceTicks: tolerance, comparisons: rows.length, worstDeltaTicks: worst, rows };
    console.info(`PARITY_REPORT ${JSON.stringify(summary)}`);
    console.info(`${name} (mu ${trace.friction}): ${rows.length} comparisons, worst |delta| = ${worst} ticks, tolerance ${tolerance}${trace.gate === false ? " (informational, not a gate)" : ""}`);
    if (trace.gate === false) return;
    for (const row of rows) expect(Math.abs(row.delta ?? Infinity), row.what).toBeLessThanOrEqual(tolerance);
  });
});
