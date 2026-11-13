// PLAN 4.1d: snapping maths. A dragged conveyor's end within 0.15 m of another's is placed so the
// ends coincide (within 1 mm) and face each other (headings within 0.1 degrees), and the
// connection runs from the end parts leave to the end they arrive at.
import { describe, expect, it } from "vitest";
import { CAPTURE_M, findSnap, localPort, normalize, worldPort, type Pose, type SnapPort } from "./snap";

/** A belt's ports as core/snaps.py publishes them: infeed at the origin, discharge `length` on. */
function belt(at: Pose, length = 2, roles: ["in" | "both", "out" | "both"] = ["in", "out"]): SnapPort[] {
  const r = (at.rotation_deg * Math.PI) / 180;
  const end: [number, number, number] = [at.position[0] + length * Math.cos(r), at.position[1], at.position[2] - length * Math.sin(r)];
  return [
    { name: "infeed", kind: "load", position: [...at.position], heading_deg: at.rotation_deg + 180, width: 0.5, role: roles[0] },
    { name: "discharge", kind: "load", position: end, heading_deg: at.rotation_deg, width: 0.5, role: roles[1] },
  ];
}

const facing = (a: number, b: number) => Math.abs(normalize(a - (b + 180)));

describe("snap ports in the editor (PLAN 4.1d)", () => {
  const fixed: Pose = { position: [0, 0.8, 0], rotation_deg: 0 };
  const others = [{ id: "CONV_01", ports: belt(fixed) }];

  it("a conveyor dropped near another's discharge lands on it, ends within 1 mm, headings equal", () => {
    const published: Pose = { position: [5, 0.8, 3], rotation_deg: 0 };
    const now: Pose = { position: [2.08, 0.8, 0.06], rotation_deg: 3 }; // 0.1 m off, 3 degrees out
    const snap = findSnap("CONV_02", belt(published), published, now, others)!;
    expect(snap).not.toBeNull();
    expect(snap.connection).toEqual({ from: "CONV_01", to: "CONV_02" });
    expect([snap.mine, snap.theirs.port]).toEqual(["infeed", "discharge"]);
    const mine = worldPort(localPort(belt(published)[0], published), snap.pose);
    const theirs = others[0].ports[1];
    expect(Math.hypot(mine.position[0] - theirs.position[0], mine.position[2] - theirs.position[2])).toBeLessThan(0.001);
    expect(facing(mine.heading, theirs.heading_deg)).toBeLessThan(0.1);
    expect(snap.pose.rotation_deg).toBeCloseTo(0, 6); // headings equal
  });

  it("dragging a feeder's discharge to an infeed connects it as the feeder", () => {
    const published: Pose = { position: [-5, 0.8, 0], rotation_deg: 0 };
    const now: Pose = { position: [-2.1, 0.8, 0], rotation_deg: 0 };
    const snap = findSnap("CONV_00", belt(published), published, now, others)!;
    expect(snap.connection).toEqual({ from: "CONV_00", to: "CONV_01" });
    expect(snap.pose.position[0]).toBeCloseTo(-2, 9);
  });

  it("a lane dropped at an angle near an infeed is turned to face it and lands on it", () => {
    const published: Pose = { position: [0, 0.8, 5], rotation_deg: 90 };
    // rotated to 80 degrees with its discharge about 6 cm from CONV_01's infeed
    const now: Pose = { position: [-0.30, 0.8, 1.95], rotation_deg: 80 };
    const snap = findSnap("CONV_02", belt(published), published, now, others)!;
    expect(snap.connection).toEqual({ from: "CONV_02", to: "CONV_01" });
    expect(snap.pose.rotation_deg).toBeCloseTo(0, 6); // turned to face the infeed exactly
  });

  it("nothing snaps beyond the capture distance, at another height, or end to wrong end", () => {
    const published: Pose = { position: [5, 0.8, 3], rotation_deg: 0 };
    const far: Pose = { position: [2 + CAPTURE_M + 0.01, 0.8, 0], rotation_deg: 0 };
    expect(findSnap("CONV_02", belt(published), published, far, others)).toBeNull();
    const low: Pose = { position: [2.05, 0.7, 0], rotation_deg: 0 };
    expect(findSnap("CONV_02", belt({ ...published, position: [5, 0.7, 3] }), { ...published, position: [5, 0.7, 3] }, low, others)).toBeNull();
    // only discharge ends near each other: two discharges never join
    const head: Pose = { position: [4.05, 0.8, 0], rotation_deg: 180 };
    const pub: Pose = { position: [9, 0.8, 0], rotation_deg: 180 };
    expect(findSnap("CONV_02", belt(pub).slice(1), pub, head, others.map((o) => ({ ...o, ports: o.ports.slice(1) })))).toBeNull();
  });

  it("the nearest candidate wins, and a component never snaps to itself", () => {
    const published: Pose = { position: [5, 0.8, 3], rotation_deg: 0 };
    const now: Pose = { position: [2.1, 0.8, 0], rotation_deg: 0 };
    const near = { id: "CONV_09", ports: belt({ position: [0, 0.8, 0.0], rotation_deg: 0 }, 2.05) };
    expect(findSnap("CONV_02", belt(published), published, now, [...others, near])!.theirs.id).toBe("CONV_09");
    expect(findSnap("CONV_01", belt(fixed), fixed, fixed, others)).toBeNull();
  });
});
