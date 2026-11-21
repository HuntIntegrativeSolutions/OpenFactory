import { describe, expect, it } from "vitest";
import { findRail, followersOn, movesBelt, type RailPort } from "./mount";

/** The rails core/mounts.py publishes for a belt at `origin`, turned `rot` degrees. */
function belt(id: string, origin: [number, number, number], rot: number, length = 3, width = 0.5) {
  const r = (rot * Math.PI) / 180;
  // local (0, 0, z) in world: rotate +z about +y by rot (positive turns +x toward -z)
  const at = (z: number): [number, number, number] => [origin[0] + z * Math.sin(r), origin[1], origin[2] + z * Math.cos(r)];
  const ports: RailPort[] = [
    { name: "rail_left", kind: "mount", position: at(-width / 2), heading_deg: rot - 90, width, role: "both", length },
    { name: "rail_right", kind: "mount", position: at(width / 2), heading_deg: rot + 90, width, role: "both", length },
  ];
  return { id, ports };
}

describe("dropping a device on a rail (PLAN 4.1g1)", () => {
  it("finds the nearer rail and the distance along it, on the grid", () => {
    const b = belt("CONV_01", [0, 0, 0], 0);
    const left = findRail([1.23, 0.1, -0.35], [b])!;
    expect(left).toMatchObject({ conveyor: "CONV_01", rail: "rail_left", along: 1.25 });
    expect(left.point).toEqual([1.25, 0, -0.25]);
    expect(left.beside_m).toBeCloseTo(0.1, 6);
    expect(findRail([1.23, 0.1, 0.05], [b])!.rail).toBe("rail_right");
    expect(findRail([1.23, 0.1, -0.35], [b], false)!.along).toBeCloseTo(1.23, 6); // Shift: free
  });

  it("captures within 0.15 m beside the belt and no further, and clamps to the rail's ends", () => {
    const b = belt("CONV_01", [0, 0, 0], 0);
    expect(findRail([1, 0, -0.39], [b])).not.toBeNull();
    expect(findRail([1, 0, -0.41], [b])).toBeNull();
    expect(findRail([3.1, 0, 0], [b])!.along).toBe(3);
    expect(findRail([-0.1, 0, 0], [b])!.along).toBe(0);
    expect(findRail([3.2, 0, 0], [b])).toBeNull();
  });

  it("works on a turned belt: one flowing along -z has its left rail at -x", () => {
    const b = belt("CONV_01", [0, 0, 0], 90);
    const drop = findRail([-0.3, 0, -1.5], [b])!;
    expect(drop.rail).toBe("rail_left");
    expect(drop.along).toBeCloseTo(1.5, 6);
    expect(drop.point[0]).toBeCloseTo(-0.25, 6);
    expect(drop.point[2]).toBeCloseTo(-1.5, 6);
  });

  it("picks the belt whose centre line is nearest when two are close", () => {
    const near = belt("CONV_A", [0, 0, 0], 0);
    const far = belt("CONV_B", [0, 0, 0.7], 0);
    expect(findRail([1, 0, 0.3], [near, far])!.conveyor).toBe("CONV_A");
    expect(findRail([1, 0, 0.42], [near, far])!.conveyor).toBe("CONV_B");
  });
});

describe("followersOn (PLAN 5.5, ruled 2026-11-20, ruling 2)", () => {
  const cv = belt("CV_201", [0, 0, 2.5], 0);
  it("finds the laid-out devices on a belt with their rail and exact distance", () => {
    const found = followersOn(cv, [
      { id: "PE_201", type: "photo_eye", position: [1.2, 0.1, 2.5], laid_out: true },
      { id: "CYL_201", type: "pusher", position: [2.0, 0.15, 2.2], laid_out: true },
      { id: "PE_202", type: "photo_eye", position: [1.73, 0.1, 2.5], laid_out: false }, // placed by a person
      { id: "TK_101", type: "tank", position: [1, 0, 2.5], laid_out: true }, // not a rail type
      { id: "PE_9", type: "photo_eye", position: [9, 0.1, 9], laid_out: true }, // on no belt
    ]);
    expect(found).toEqual([
      { id: "PE_201", conveyor: "CV_201", rail: "rail_right", along: 1.2 },
      { id: "CYL_201", conveyor: "CV_201", rail: "rail_left", along: 2 },
    ]);
  });
});

describe("two belts side by side (review of 2026-11-20, second set, 1b)", () => {
  // 0.5 m belts 0.15 m apart: A's right rail faces B's left rail
  const a = belt("CV_A", [0, 0, 0], 0);
  const b = belt("CV_B", [0, 0, 0.65], 0);
  const devices = [
    { id: "DV_A", type: "pusher", position: [1.0, 0.125, 0.3] as [number, number, number], rotation_deg: 180, laid_out: true },
    { id: "DV_B", type: "pusher", position: [2.0, 0.125, 0.35] as [number, number, number], rotation_deg: 0, laid_out: true },
  ];
  it("a device follows only the belt whose rail it stands on", () => {
    expect(followersOn(a, devices, [a, b])).toEqual([{ id: "DV_A", conveyor: "CV_A", rail: "rail_right", along: 1 }]);
    expect(followersOn(b, devices, [a, b])).toEqual([{ id: "DV_B", conveyor: "CV_B", rail: "rail_left", along: 2 }]);
  });
});

describe("a belt moved by typing in the form (review of 2026-11-20, second set, ruling 10)", () => {
  it("its position, one axis of it, or its rotation carries its devices; other fields do not", () => {
    expect(movesBelt(["position"])).toBe(true);
    expect(movesBelt(["position", 0])).toBe(true);
    expect(movesBelt(["rotation_deg"])).toBe(true);
    expect(movesBelt(["speed_mps"])).toBe(false);
    expect(movesBelt(["io", "run"])).toBe(false);
    expect(movesBelt(["laid_out"])).toBe(false);
  });
});

describe("two belts closer than 0.1 m: position alone cannot tell (review of 2026-11-20, second set, 1b)", () => {
  // a pusher stands 0.30 m from its own belt's centre line; below a 0.10 m gap it is nearer the
  // other belt's. What would tell them apart: its heading (it faces its own belt) or a mount kept
  // in the scene. Not built: the order says stop and report.
  const a = belt("CV_A", [0, 0, 0], 0);
  const b = belt("CV_B", [0, 0, 0.55], 0); // 0.05 m apart
  it.fails("a pusher on B's facing rail 0.05 m from A rides A", () => {
    const devices = [{ id: "DV_B", type: "pusher", position: [2.0, 0.125, 0.25] as [number, number, number], rotation_deg: 0, laid_out: true }];
    expect(followersOn(a, devices, [a, b])).toEqual([]);
  });
});
