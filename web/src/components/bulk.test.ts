// PLAN 5.7a (ADR-0006): the viewer draws bulk contents from the core's state, no particles: a
// bin's fill at its level through its cone and wall, a screw's load along its length, a gate's
// blade where it stands, in ISA-101 colours.
import * as THREE from "three";
import { describe, expect, it } from "vitest";
import { ISA101 } from "../isa101";
import { buildComponent } from ".";

const ctx = { floorY: 0 };
const geom = (type: string, extra: Record<string, unknown> = {}) => ({
  id: "X_01", type, description: type, position: [0, 3, 0] as [number, number, number], rotation_deg: 0, ...extra,
});

function fillTop(view: ReturnType<typeof buildComponent>): number {
  let top = 0;
  view.object.traverse((o) => {
    if (o.name === "fill" && o.visible && (o as THREE.Mesh).geometry.attributes.position) {
      o.updateWorldMatrix(true, false);
      top = Math.max(top, new THREE.Box3().setFromObject(o).max.y - view.object.position.y);
    }
  });
  return top;
}

describe("bulk solids in the viewer (PLAN 5.7a)", () => {
  const bin = { diameter: 2, height: 3, cone_height: 1.5, outlet_diameter: 0.3 };

  it("draws a bin's fill up to its level, in the cone and in the wall", () => {
    const view = buildComponent(geom("bin", bin), ctx);
    view.update({ level_m: 0 });
    expect(fillTop(view)).toBe(0);
    view.update({ level_m: 1.0 }); // in the cone
    expect(fillTop(view)).toBeCloseTo(1.0, 3);
    view.update({ level_m: 3.0 }); // in the wall
    expect(fillTop(view)).toBeCloseTo(3.0, 3);
    view.update({ level_m: 4.5 }); // full
    expect(fillTop(view)).toBeCloseTo(4.5, 3);
  });

  it("turns a bridged bin's fill alarm yellow", () => {
    const view = buildComponent(geom("bin", bin), ctx);
    view.update({ level_m: 2, mass_kg: 1000, bridged: true });
    const fill = view.object.getObjectByName("fill") as THREE.Mesh;
    expect((fill.material as THREE.MeshStandardMaterial).color.getHex()).toBe(ISA101.alarmMedium);
  });

  it("draws a screw's load along its length and its motor green while it turns", () => {
    const view = buildComponent(geom("screw_conveyor", { length: 3, diameter: 0.25 }), ctx);
    view.update({ turning: true, load: [1, 1, 0.5, ...Array(13).fill(0)], plugged: false });
    const load: THREE.Object3D[] = [];
    view.object.traverse((o) => o.name === "load" && load.push(o));
    expect(load.map((o) => o.visible).slice(0, 4)).toEqual([true, true, true, false]);
    expect(load[2].scale.y).toBeCloseTo(load[0].scale.y / 2, 6);
    const motor = view.object.getObjectByName("motor") as THREE.Mesh;
    expect((motor.material as THREE.MeshStandardMaterial).color.getHex()).toBe(ISA101.running);
    view.update({ turning: false, load: [], plugged: true });
    expect((motor.material as THREE.MeshStandardMaterial).color.getHex()).toBe(ISA101.stopped);
    const lamp = view.object.getObjectByName("plug") as THREE.Mesh;
    expect((lamp.material as THREE.MeshStandardMaterial).color.getHex()).toBe(ISA101.alarmMedium);
  });

  it("slides a gate's blade off the outlet as it opens, green fully open", () => {
    const view = buildComponent(geom("slide_gate", { size: 0.4 }), ctx);
    const blade = view.object.getObjectByName("blade") as THREE.Mesh;
    view.update({ opening: 0 });
    expect(blade.position.x).toBeCloseTo(0, 6);
    view.update({ opening: 0.5 });
    expect(blade.position.x).toBeCloseTo(0.2, 6);
    view.update({ opening: 1 });
    expect((blade.material as THREE.MeshStandardMaterial).color.getHex()).toBe(ISA101.running);
  });
});
