// PLAN 1.11: mesh builders place each component at its scene origin and show ISA-101 status.
import * as THREE from "three";
import { describe, expect, it } from "vitest";
import type { ComponentGeometry } from "../api";
import { ISA101 } from "../isa101";
import { BUILDERS, buildComponent, floorFor } from "./index";

const ctx = { floorY: -0.75 };

function geom(type: string, extra: Record<string, unknown> = {}): ComponentGeometry {
  return { id: `${type.toUpperCase()}_01`, type, description: type, position: [1, 0.2, -0.3], rotation_deg: 90, ...extra };
}

function color(object: THREE.Object3D | undefined): number {
  return ((object as THREE.Mesh).material as THREE.MeshStandardMaterial).color.getHex();
}

describe("component mesh builders", () => {
  it("cover every Milestone 1 type and place them at the scene origin and rotation", () => {
    for (const type of Object.keys(BUILDERS)) {
      const view = buildComponent(geom(type, { length: 2, width: 0.5, beam_length: 0.6, stroke: 0.5, size: [0.3, 1, 1] }), ctx);
      expect(view.type).toBe(type);
      expect(view.object.position.toArray()).toEqual([1, 0.2, -0.3]);
      expect(view.object.rotation.y).toBeCloseTo(Math.PI / 2);
      expect(view.object.children.length).toBeGreaterThan(0);
    }
  });

  it("colors a conveyor belt green while running and grey when stopped", () => {
    const view = buildComponent(geom("belt_conveyor", { length: 4, width: 0.5 }), ctx);
    const belt = view.object.getObjectByName("belt");
    expect(color(belt)).toBe(ISA101.stopped);
    view.update({ running: true, travel_m: 0.1 });
    expect(color(belt)).toBe(ISA101.running);
    view.update({ running: false });
    expect(color(belt)).toBe(ISA101.stopped);
  });

  it("draws the belt from the infeed end along local +x, with legs to the floor", () => {
    const view = buildComponent({ ...geom("belt_conveyor", { length: 4, width: 0.5 }), position: [0, 0, 0], rotation_deg: 0 }, ctx);
    view.object.updateMatrixWorld(true);
    const bounds = new THREE.Box3().setFromObject(view.object);
    expect(bounds.min.x).toBeCloseTo(-0.04 - 0.0, 1);
    expect(bounds.max.x).toBeCloseTo(4.04, 1);
    expect(bounds.min.y).toBeCloseTo(-0.75, 5);
    expect(bounds.max.y).toBeCloseTo(0.0, 1);
  });

  it("moves the pusher plate with the core's stroke and colors it while extending", () => {
    const view = buildComponent(geom("pusher", { stroke: 0.5, plate_width: 0.3, plate_height: 0.15 }), ctx);
    const plate = view.object.getObjectByName("plate")!;
    expect(plate.position.z).toBeCloseTo(-0.01);
    view.update({ position_m: 0.25, extending: true });
    expect(plate.position.z).toBeCloseTo(0.24);
    expect(color(plate)).toBe(ISA101.running);
  });

  it("lights the photo-eye beam on detect", () => {
    const view = buildComponent(geom("photo_eye", { beam_length: 0.6 }), ctx);
    const beam = view.object.getObjectByName("beam");
    view.update({ detect: true, blocked: true });
    expect(color(beam)).toBe(ISA101.running);
    view.update({ detect: false, blocked: false });
    expect(color(beam)).toBe(ISA101.stopped);
  });

  it("renders unknown types as a grey fallback box", () => {
    const view = buildComponent(geom("turntable"), ctx);
    expect(view.type).toBe("turntable");
    expect(color(view.object.children[0])).toBe(ISA101.stopped);
  });

  it("puts the floor 0.75 m below the lowest conveyor", () => {
    expect(floorFor([geom("belt_conveyor"), { ...geom("belt_conveyor"), position: [0, -0.1, 0] }])).toBeCloseTo(-0.85);
  });
});
