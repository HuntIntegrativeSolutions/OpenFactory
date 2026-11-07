// PLAN 2.5: viewer physics for the Milestone 2 components agrees with core/kinematics.py on the
// numbers the unit tests pin (tests/unit/test_<component>.py).
import { describe, expect, it } from "vitest";
import type { ComponentGeometry } from "./api";
import { loadRapier, offDeck, PlantPhysics, tiltDeg } from "./physics";

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

describe("lift deck (PLAN 2.5b)", () => {
  it("moves its belt body to the core's deck height, so a box comes to rest on the raised deck", async () => {
    const R = await loadRapier();
    const g: ComponentGeometry = {
      id: "LFT_01", type: "lift", description: "", position: [2, 0, 0], rotation_deg: 0,
      length: 1, width: 0.5, lift_height: 0.6,
    };
    const physics = new PlantPhysics(R, { components: [g], friction: 0.8, dt: DT });
    physics.setLift("LFT_01", 0.6);
    physics.step();
    physics.addPart("P1", [0.3, 0.3, 0.3], [2.5, 0.8, 0]);
    for (let k = 0; k < 60; k++) physics.step();
    expect(physics.part("P1")!.position[1]).toBeCloseTo(0.75, 2);
    physics.setLift("LFT_01", 0);
    physics.addPart("P2", [0.3, 0.3, 0.3], [2.15, 0.2, 0]);
    for (let k = 0; k < 60; k++) physics.step();
    expect(physics.part("P2")!.position[1]).toBeCloseTo(0.15, 2);
  });
});

describe("reversible belt (PLAN 2.5b)", () => {
  it("carries a box toward the infeed end at the belt's negative speed", async () => {
    const R = await loadRapier();
    const g: ComponentGeometry = {
      id: "CONV_A", type: "belt_conveyor", description: "", position: [0, 0, 0], rotation_deg: 0,
      length: 3, width: 0.5, bidirectional: true,
    };
    const physics = new PlantPhysics(R, { components: [g], friction: 0.8, dt: DT });
    physics.setBelt("CONV_A", -0.5);
    physics.addPart("P1", [0.3, 0.3, 0.3], [2.0, 0.15, 0]);
    for (let k = 0; k < 50; k++) physics.step();
    const x = physics.part("P1")!.position[0];
    expect(x).toBeLessThan(1.6); // about 0.5 m back in 1 s, less the ramp-up
    expect(x).toBeGreaterThan(1.4);
  });
});

describe("a viewer joining a run already going (PLAN 2.9, the tipped box)", () => {
  // tests/scenes/gated-conveyor.yaml's GC_01: a 2 m belt at 0.4 m/s with a 0.1 m gate blade at
  // x 1.8, and the box the core holds against it (centre x 1.65, front face on the blade line).
  const comps: ComponentGeometry[] = [
    { id: "GC_01_BELT", type: "belt_conveyor", description: "", position: [0, 0, 0], rotation_deg: 0, length: 2, width: 0.5 },
    { id: "GC_01_GATE", type: "stopper", description: "", position: [1.8, 0, 0], rotation_deg: 0, width: 0.5, height: 0.1, thickness: 0.02 },
    { id: "GC_02_BELT", type: "belt_conveyor", description: "", position: [2, 0, 0], rotation_deg: 0, length: 3, width: 0.5 },
  ];

  /** The viewer's first frame (main.ts, plan.reset): devices to the frame's pose, then the parts
   * at the core's; then 100 steps. Returns the box's worst tilt (degrees), top height and x. */
  async function join(snap: boolean, x: number): Promise<{ tilt: number; y: number; x: number }> {
    const R = await loadRapier();
    const physics = new PlantPhysics(R, { components: comps, friction: 0.8, dt: DT });
    physics.setBelt("GC_01_BELT", 0.4);
    physics.setBelt("GC_02_BELT", 0.4);
    physics.setStopper("GC_01_GATE", 1);
    if (snap) physics.snapDevices();
    physics.addPart("P1", [0.3, 0.2, 0.3], [x, 0.1, 0]);
    let tilt = 0;
    let y = 0;
    for (let k = 0; k < 100; k++) {
      physics.step();
      const pose = physics.part("P1")!;
      tilt = Math.max(tilt, tiltDeg(pose.rotation));
      y = Math.max(y, pose.position[1]);
    }
    const end = physics.part("P1")!.position[0];
    physics.free();
    return { tilt, y, x: end };
  }

  it("keeps the box held at a raised gate upright; a blade rising through it would throw it", async () => {
    const held = await join(true, 1.65);
    expect(held.tilt).toBeLessThan(2);
    expect(held.y).toBeLessThan(0.11);
    expect(held.x).toBeCloseTo(1.65, 2); // still against the blade
    const swept = await join(false, 1.65); // the cause: the blade rose out of the belt in one step
    console.log(`joined at the gate: snapped tilt ${held.tilt.toFixed(2)} deg, swept ${swept.tilt.toFixed(1)} deg, top ${swept.y.toFixed(2)} m`);
    expect(swept.tilt).toBeGreaterThan(45);
  });

  it("lets a box the core holds over a raised blade's line pass, as the core does", async () => {
    const over = await join(true, 1.8); // the blade rose under it before the viewer joined
    expect(over.tilt).toBeLessThan(2);
    expect(over.y).toBeLessThan(0.11);
    expect(over.x).toBeGreaterThan(2.3); // carried on past the blade onto GC_02
  });
});

describe("seam crossing (PLAN 2.9)", () => {
  /** Metres a box riding at 0.5 m/s falls behind ideal travel while crossing from belt A onto
   * belt B, both at 0.5 m/s; `connected`: the scene declares the A-to-B connection. */
  async function lag(connected: boolean): Promise<number> {
    const R = await loadRapier();
    const comps: ComponentGeometry[] = [
      { id: "A", type: "belt_conveyor", description: "", position: [0, 0, 0], rotation_deg: 0, length: 2, width: 0.5 },
      { id: "B", type: "belt_conveyor", description: "", position: [2, 0, 0], rotation_deg: 0, length: 2, width: 0.5 },
    ];
    const physics = new PlantPhysics(R, {
      components: comps, connections: connected ? [{ from: "A", to: "B" }] : [], friction: 0.8, dt: DT,
    });
    physics.setBelt("A", 0.5);
    physics.setBelt("B", 0.5);
    physics.addPart("P1", [0.3, 0.2, 0.3], [1.0, 0.1, 0], [0.5, 0, 0]);
    for (let k = 0; k < 20; k++) physics.step(); // settled on A at belt speed
    const x0 = physics.part("P1")!.position[0];
    for (let k = 0; k < 150; k++) physics.step(); // across the seam at x 2.0 and on along B
    const x = physics.part("P1")!.position[0];
    physics.free();
    return x0 + 0.5 * DT * 150 - x;
  }

  it("carries a box across an equal-speed seam without the 1.2-tick loss Rapier's recycled contacts cause", async () => {
    const fresh = await lag(true);
    const recycled = await lag(false); // the same belts, undeclared: the old behaviour
    console.log(`seam lag at 0.5 m/s: ${(fresh * 1000).toFixed(2)} mm connected, ${(recycled * 1000).toFixed(2)} mm not`);
    expect(Math.abs(fresh)).toBeLessThan(0.001);
    expect(recycled).toBeGreaterThan(0.005);
  });
});

describe("the sorter divert (PLAN 2.9, the divert overshoot)", () => {
  // scenes/sorting-by-height.yaml: CYL_01 pushes tall boxes off CONV_01 onto CONV_02 alongside. The
  // core places the box with its back face on the plate face (z -0.3 + 0.6 + 0.15 = 0.45) and it
  // rides CONV_02 from there. Left to Rapier, the box at the plate's 1.5 m/s caught its leading
  // bottom edge on CONV_02's rounded side edge, tipped over the plate, and flew off CONV_02.
  const comps: ComponentGeometry[] = [
    { id: "CONV_01", type: "belt_conveyor", description: "", position: [0, 0, 0], rotation_deg: 0, length: 4, width: 0.5 },
    { id: "CONV_02", type: "belt_conveyor", description: "", position: [2, 0, 0.5], rotation_deg: 0, length: 1.5, width: 0.5 },
    { id: "CYL_01", type: "pusher", description: "", position: [2.5, 0.175, -0.3], rotation_deg: 0, stroke: 0.6, plate_width: 0.3, plate_height: 0.25 },
  ];

  it("leaves a pushed tall box upright on CONV_02 where the core puts it, with no speed across", async () => {
    const R = await loadRapier();
    for (const lead of [0, 0.02, 0.04, 0.06]) {
      const physics = new PlantPhysics(R, { components: comps, friction: 0.8, dt: DT });
      physics.setBelt("CONV_01", 0.5);
      physics.setBelt("CONV_02", 0.5);
      physics.addPart("P1", [0.3, 0.4, 0.3], [2.4 - lead, 0.2, 0], [0.5, 0, 0]);
      let stroke = 0;
      let extending = true;
      let worstTilt = 0;
      let lowZ = Infinity;
      let highZ = -Infinity;
      for (let k = 0; k < 60; k++) {
        // the core's plate: out at 0.6 m / 0.4 s (0.03 m a tick), back at once at full stroke
        stroke = extending ? Math.min(0.6, stroke + 0.03) : Math.max(0, stroke - 0.03);
        if (stroke >= 0.6) extending = false;
        physics.setPusher("CYL_01", stroke);
        physics.step();
        const pose = physics.part("P1")!;
        worstTilt = Math.max(worstTilt, tiltDeg(pose.rotation));
        if (k >= 20) {
          lowZ = Math.min(lowZ, pose.position[2]);
          highZ = Math.max(highZ, pose.position[2]);
        }
      }
      const end = physics.part("P1")!.position;
      console.log(
        `divert, box ${lead} m ahead: z ${lowZ.toFixed(4)}..${highZ.toFixed(4)} after the push, ` +
          `worst tilt ${worstTilt.toFixed(2)} deg, at ${end.map((v) => v.toFixed(3)).join(", ")} after 1.2 s`,
      );
      expect(worstTilt).toBeLessThan(1);
      expect(Math.abs(lowZ - 0.45)).toBeLessThan(0.005); // where the core leaves it
      expect(Math.abs(highZ - 0.45)).toBeLessThan(0.005); // and it stays: no momentum across
      expect(end[0]).toBeGreaterThan(2.4 - lead + 0.5 * 1.0); // carried along CONV_02 meanwhile
      expect(end[1]).toBeCloseTo(0.2, 2);
    }
  });
});

describe("offDeck, the viewer's position check (PLAN 2.9)", () => {
  it("is 0 over a deck and the distance outside it otherwise, for belts and turned turntables", () => {
    const belt: ComponentGeometry = { id: "CONV_02", type: "belt_conveyor", description: "", position: [2, 0, 0.5], rotation_deg: 0, length: 1.5, width: 0.5 };
    expect(offDeck(belt, [2.6, 0.2, 0.45])).toBe(0);
    expect(offDeck(belt, [2.6, 0.2, 0.95])).toBeCloseTo(0.2, 9); // 0.2 m past the far edge (z 0.75)
    expect(offDeck(belt, [1.7, 0.2, 0.5])).toBeCloseTo(0.3, 9); // 0.3 m before the infeed end
    const turned: ComponentGeometry = { ...belt, rotation_deg: 90 }; // runs along -z from its position
    expect(offDeck(turned, [2, 0, -0.5])).toBe(0);
    expect(offDeck(turned, [2, 0, 1])).toBeCloseTo(0.5, 9);
    const deck: ComponentGeometry = { id: "TT_01", type: "turntable", description: "", position: [0, 0, 0], rotation_deg: 0, length: 1, width: 0.4 };
    expect(offDeck(deck, [0.45, 0, 0])).toBe(0);
    expect(offDeck(deck, [0.45, 0, 0], 90)).toBeCloseTo(0.25, 9); // turned: 0.45 across a 0.2 half width
  });
});
