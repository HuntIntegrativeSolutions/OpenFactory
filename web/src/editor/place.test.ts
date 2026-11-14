// PLAN 4.1e: a new component lands clear of the plant, and a new conveyor dropped near an open end
// snaps to it.
import { describe, expect, it } from "vitest";
import { dropSnap, freeOffset, MARGIN_M, moved, openEnds, overlaps, placeNew, shifted, type NewItem, type Rect } from "./place";
import { localPort, normalize, worldPort, type Pose, type SnapPort } from "./snap";

function belt(at: Pose, length = 2): SnapPort[] {
  const r = (at.rotation_deg * Math.PI) / 180;
  const end: [number, number, number] = [at.position[0] + length * Math.cos(r), at.position[1], at.position[2] - length * Math.sin(r)];
  return [
    { name: "infeed", kind: "load", position: [...at.position], heading_deg: at.rotation_deg + 180, width: 0.5, role: "in" },
    { name: "discharge", kind: "load", position: end, heading_deg: at.rotation_deg, width: 0.5, role: "out" },
  ];
}

const rect = (minX: number, minZ: number, maxX: number, maxZ: number): Rect => ({ minX, minZ, maxX, maxZ });

describe("free placement on Add (PLAN 4.1e)", () => {
  it("a drop that is already clear stays put", () => {
    expect(freeOffset(rect(5, 5, 5.2, 5.2), [rect(0, -0.25, 4, 0.25)])).toEqual([0, 0]);
  });

  it("a drop over a conveyor moves to the nearest clear spot, with the margin kept", () => {
    const conveyor = rect(0, -0.25, 4, 0.25);
    const mine = rect(1.9, -0.1, 2.1, 0.1); // a small box in the belt's middle
    const [dx, dz] = freeOffset(mine, [conveyor])!;
    expect(dx).toBe(0); // straight off the side, the shortest way out
    const out = shifted(mine, dx, dz);
    expect(overlaps(out, conveyor, MARGIN_M)).toBe(false);
    expect(Math.abs(dz)).toBeLessThanOrEqual(0.25 + 0.1 + MARGIN_M + 0.1 + 1e-9);
  });

  it("finds the nearest clear floor and never goes past its edge", () => {
    const wall = rect(-20, -20, 20, 10); // the floor beyond z 10 is free; the drop is not on it
    const [, dz] = freeOffset(rect(-0.5, -0.5, 0.5, 0.5), [wall])!;
    const r = shifted(rect(-0.5, -0.5, 0.5, 0.5), 0, dz);
    expect(r.maxZ).toBeLessThanOrEqual(20);
    expect(r.minZ).toBeGreaterThanOrEqual(10 + MARGIN_M);
  });

  it("gives up rather than leave the floor", () => {
    const all = rect(-20, -20, 20, 19.5); // only a 0.5 m strip is left: too narrow for 1 m
    expect(freeOffset(rect(-0.5, 14, 0.5, 15), [all])).toBeNull();
  });

  it("an open end is one no other end meets", () => {
    const a = { id: "CONV_01", ports: belt({ position: [0, 0.8, 0], rotation_deg: 0 }) };
    const b = { id: "CONV_02", ports: belt({ position: [2, 0.8, 0], rotation_deg: 0 }) };
    const open = openEnds([a, b]);
    expect(open[0].ports.map((p) => p.name)).toEqual(["infeed"]);
    expect(open[1].ports.map((p) => p.name)).toEqual(["discharge"]);
  });

  it("a conveyor dropped near an open discharge snaps on, at its height, facing on", () => {
    const fixed = { id: "CONV_01", ports: belt({ position: [0, 0.8, 0], rotation_deg: 30 }) };
    const dropped: Pose = { position: [2.3, 0, -0.4], rotation_deg: 0 }; // floor height, unturned
    const item: NewItem = { id: "CONV_02", ...dropped, position: dropped.position, rect: rect(2.3, -0.65, 4.3, -0.15), ports: belt(dropped) };
    const snap = dropSnap(item, [fixed])!;
    expect(snap.connection).toEqual({ from: "CONV_01", to: "CONV_02" });
    const mine = worldPort(localPort(item.ports[0], dropped), { position: snap.position, rotation_deg: snap.rotation_deg });
    const theirs = fixed.ports[1];
    for (let i = 0; i < 3; i++) expect(Math.abs(mine.position[i] - theirs.position[i])).toBeLessThan(1e-3);
    expect(Math.abs(normalize(mine.heading - (theirs.heading_deg + 180)))).toBeLessThan(0.1);
    expect(snap.position[1]).toBeCloseTo(0.8, 6);
  });

  it("does not snap to an end that is already joined, or one too far", () => {
    const a = { id: "CONV_01", ports: belt({ position: [0, 0.8, 0], rotation_deg: 0 }) };
    const b = { id: "CONV_02", ports: belt({ position: [2, 0.8, 0], rotation_deg: 0 }) };
    const at: Pose = { position: [2.2, 0.8, 0.5], rotation_deg: 0 }; // near the joined seam only
    const item: NewItem = { id: "CONV_03", position: at.position, rotation_deg: 0, rect: rect(2.2, 0.25, 4.2, 0.75), ports: belt(at) };
    expect(dropSnap(item, [a, b])).toBeNull();
    const far: Pose = { position: [10, 0.8, 10], rotation_deg: 0 };
    expect(dropSnap({ ...item, position: far.position, ports: belt(far) }, [a, b])).toBeNull();
  });

  it("placeNew snaps when the way is clear, else falls back to a free spot", () => {
    const fixedPose: Pose = { position: [0, 0.8, 0], rotation_deg: 0 };
    const fixed = { id: "CONV_01", ports: belt(fixedPose) };
    const dropped: Pose = { position: [2.2, 0.8, 0.3], rotation_deg: 0 };
    const item: NewItem = { id: "CONV_02", position: dropped.position, rotation_deg: 0, rect: rect(2.2, 0.05, 4.2, 0.55), ports: belt(dropped) };
    const own = { id: "CONV_01", rect: rect(0, -0.25, 2, 0.25) };
    const snapped = placeNew(item, [own], [fixed])!;
    expect(snapped.snapped).toBe("CONV_02.infeed -> CONV_01.discharge");
    expect(snapped.position).toEqual([2, 0.8, 0]);
    const bin = { id: "BIN_01", rect: rect(2.5, -0.5, 3.5, 0.5) }; // right where it would land
    const free = placeNew(item, [own, bin], [fixed])!;
    expect(free.snapped).toBeNull();
    expect(free.connection).toBeNull();
    const landed = shifted(item.rect, free.position[0] - 2.2, free.position[2] - 0.3);
    expect(overlaps(landed, own.rect, MARGIN_M) || overlaps(landed, bin.rect, MARGIN_M)).toBe(false);
  });

  it("moved turns a footprint about the component's origin", () => {
    const r = moved(rect(0, -0.25, 2, 0.25), { position: [0, 0, 0], rotation_deg: 0 }, { position: [1, 0, 1], rotation_deg: 90 });
    // +x turns to -z: the belt now runs from z 1 to z -1
    expect(r.minX).toBeCloseTo(0.75, 6);
    expect(r.maxX).toBeCloseTo(1.25, 6);
    expect(r.minZ).toBeCloseTo(-1, 6);
    expect(r.maxZ).toBeCloseTo(1, 6);
  });
});
