import { describe, expect, it } from "vitest";
import { findRail, followersOn, type RailPort } from "./mount";

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

