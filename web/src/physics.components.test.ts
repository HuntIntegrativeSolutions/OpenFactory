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
