// PLAN 2.5: viewer physics for the Milestone 2 components agrees with core/kinematics.py on the
// numbers the unit tests pin (tests/unit/test_<component>.py).
import { describe, expect, it } from "vitest";
import type { ComponentGeometry } from "./api";
import { loadRapier, PlantPhysics } from "./physics";

const DT = 0.02;

function roller(friction: number | null): ComponentGeometry {
  return {
    id: "RC_01", type: "roller_conveyor", description: "", position: [0, 0, 0], rotation_deg: 0,
    length: 2, width: 0.5, roller_pitch: 0.1, friction,
  };
}

/** Steps a box placed at rest on the running conveyor takes to reach 99 % of 0.5 m/s. */
async function ticksToSpeed(g: ComponentGeometry, sceneFriction: number): Promise<number> {
  const R = await loadRapier();
  const physics = new PlantPhysics(R, { components: [g], friction: sceneFriction, dt: DT });
  physics.setBelt(g.id, 0.5);
  physics.addPart("P1", [0.3, 0.2, 0.3], [0.5, 0.1, 0]);
  let x = physics.part("P1")!.position[0];
  for (let k = 1; k < 200; k++) {
    physics.step();
    const now = physics.part("P1")!.position[0];
    if ((now - x) / DT >= 0.495) return k;
    x = now;
  }
  throw new Error("never reached belt speed");
}

describe("roller_conveyor friction (PLAN 2.5)", () => {
  it("ramps a part at the roller's own mu, like the headless model (9 ticks at 0.3, 4 at 0.8)", async () => {
    const slow = await ticksToSpeed(roller(0.3), 0.8);
    const scene = await ticksToSpeed(roller(null), 0.8);
    console.log(`Rapier ticks to 0.5 m/s: roller mu 0.3 -> ${slow}, scene mu 0.8 -> ${scene}`);
    expect(Math.abs(slow - 9)).toBeLessThanOrEqual(2);
    expect(Math.abs(scene - 4)).toBeLessThanOrEqual(2);
  });
});

describe("photo-eye beams see parts only", () => {
  it("a pusher plate and a raised stopper blade crossing a beam leave it clear; a box blocks it", async () => {
    const R = await loadRapier();
    const comps: ComponentGeometry[] = [
      { id: "CONV_01", type: "belt_conveyor", description: "", position: [0, 0, 0], rotation_deg: 0, length: 4, width: 0.5 },
      // beam along z at x 1.0, 0.1 m up; the plate sweeps across it, the blade rises through it
      { id: "PE_01", type: "photo_eye", description: "", position: [1.0, 0.1, 0], rotation_deg: 0, beam_length: 0.6 },
      { id: "CYL_01", type: "pusher", description: "", position: [1.0, 0.1, -0.4], rotation_deg: 0, stroke: 0.6, plate_width: 0.3, plate_height: 0.15 },
      { id: "STP_01", type: "stopper", description: "", position: [1.0, 0, 0], rotation_deg: 0, width: 0.5, height: 0.2 },
    ];
    const physics = new PlantPhysics(R, { components: comps, friction: 0.8, dt: DT });
    const edges: string[] = [];
    const step = () => physics.step().forEach((e) => edges.push(`${e.id} ${e.blocked}`));
    physics.setPusher("CYL_01", 0.6); // plate face swept across the beam
    physics.setStopper("STP_01", 1); // blade up through the beam
    for (let k = 0; k < 10; k++) step();
    expect(physics.blocked("PE_01")).toBe(false);
    physics.addPart("P1", [0.3, 0.2, 0.3], [2.0, 0.1, 0]);
    physics.removePart("P1");
    physics.addPart("P2", [0.3, 0.2, 0.3], [1.0, 0.105, 0.0]); // in the beam
    for (let k = 0; k < 3; k++) step();
    expect(physics.blocked("PE_01")).toBe(true);
    expect(edges).toEqual(["PE_01 true"]);
  });
});
