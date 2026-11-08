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
      if (type === "pipe") continue; // drawn through its points in scene coordinates (below)
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

  it("draws a belt's slats as one instanced mesh that runs with the core's travel (PLAN 2.9)", () => {
    const view = buildComponent(geom("belt_conveyor", { length: 4, width: 0.5 }), ctx);
    const slats = view.object.getObjectByName("slats") as THREE.InstancedMesh;
    expect(slats).toBeInstanceOf(THREE.InstancedMesh);
    expect(slats.count).toBe(16); // one every 0.25 m
    const xs = () => {
      const m = new THREE.Matrix4();
      const v = new THREE.Vector3();
      return Array.from({ length: slats.count }, (_, i) => {
        slats.getMatrixAt(i, m);
        return v.setFromMatrixPosition(m).x;
      });
    };
    expect(xs()[1]).toBeCloseTo(0.25, 6);
    view.update({ running: true, travel_m: 1.1 }); // 0.1 m past a whole number of pitches
    const moved = xs();
    expect(moved[0]).toBeCloseTo(0.1, 6);
    expect(moved[15]).toBeCloseTo(3.85, 6);
    expect(Math.max(...moved)).toBeLessThan(4); // wrapped round, never past the discharge end
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

  it("lights the counter display's segments from the core's text", () => {
    const view = buildComponent(geom("counter_display", { digits: 4, decimals: 1, color: "green" }), ctx);
    view.update({ text: " 42.7" });
    const lit = (name: string) => color(view.object.getObjectByName(name)) === ISA101.running;
    // " 42.7": blank, 4, 2 with its point, 7
    expect(["a", "b", "c", "d", "e", "f", "g"].some((s) => lit(`digit0.${s}`))).toBe(false);
    expect(["b", "c", "f", "g"].every((s) => lit(`digit1.${s}`))).toBe(true);
    expect(lit("digit1.a")).toBe(false);
    expect(lit("digit2.dp")).toBe(true);
    expect(["a", "b", "c"].every((s) => lit(`digit3.${s}`))).toBe(true);
    expect(lit("digit3.g")).toBe(false);
    view.update({ text: "    " });
    expect(lit("digit3.a")).toBe(false);
  });

  it("points a bidirectional belt's arrow along the flow, and back toward the infeed in reverse", () => {
    const view = buildComponent(geom("belt_conveyor", { length: 4, width: 0.5, bidirectional: true }), ctx);
    const arrow = view.object.getObjectByName("direction")!;
    view.update({ running: true, reverse: false });
    expect(arrow.rotation.z).toBe(0);
    expect(color(arrow)).toBe(ISA101.running);
    view.update({ running: true, reverse: true });
    expect(arrow.rotation.z).toBeCloseTo(Math.PI);
    expect(buildComponent(geom("belt_conveyor", { length: 4, width: 0.5 }), ctx).object.getObjectByName("direction")).toBeUndefined();
  });
});

describe("process components (PLAN 3.2)", () => {
  it("draws a tank's liquid at the core's level, in alarm yellow while overflowing", () => {
    const view = buildComponent(geom("tank", { height: 2, diameter: 1, shape: "vertical_cylinder" }), ctx);
    const liquid = view.object.getObjectByName("liquid") as THREE.Mesh;
    view.update({ level_m: 0.8, overflowing: false });
    view.object.updateMatrixWorld(true);
    const bounds = new THREE.Box3().setFromObject(liquid);
    expect(bounds.max.y - bounds.min.y).toBeCloseTo(0.8, 5);
    expect(bounds.min.y).toBeCloseTo(0.2, 5); // on the tank bottom, at the origin's height
    expect(color(liquid)).not.toBe(ISA101.alarmMedium);
    view.update({ level_m: 2, overflowing: true });
    expect(color(liquid)).toBe(ISA101.alarmMedium);
    view.update({ level_m: 0 });
    expect(liquid.visible).toBe(false);
  });

  it("stands tanks on the floor when a scene has no conveyor, and a raised tank on legs", () => {
    const low = geom("tank", { position: [0, 0, 0] });
    const high = geom("tank", { id: "TK_02", position: [2, 1.5, 0] });
    expect(floorFor([low, high])).toBe(0);
    const legs = (v: ReturnType<typeof buildComponent>) => v.object.children.filter((o) => o.position.y < 0).length;
    expect(legs(buildComponent(high, { floorY: 0 }))).toBe(4);
    expect(legs(buildComponent(low, { floorY: 0 }))).toBe(0);
  });

  it("draws a pipe through the core's points, one segment per run", () => {
    const view = buildComponent(geom("pipe", { points: [[0, 0, 0], [0, 2, 0], [3, 2, 0]], diameter: 0.05 }), ctx);
    const first = view.object.getObjectByName("segment0") as THREE.Mesh;
    const second = view.object.getObjectByName("segment1") as THREE.Mesh;
    view.object.updateMatrixWorld(true);
    const a = new THREE.Box3().setFromObject(first), b = new THREE.Box3().setFromObject(second);
    expect(a.min.y).toBeCloseTo(0, 5);
    expect(a.max.y).toBeCloseTo(2, 5);
    expect(b.max.x - b.min.x).toBeCloseTo(3, 5);
    expect(view.object.position.toArray()).toEqual([0, 0, 0]); // points are scene coordinates
  });
});
