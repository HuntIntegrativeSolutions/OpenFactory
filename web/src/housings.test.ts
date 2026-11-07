// PLAN 4.2: default component models. Each committed model parses with three's own GLTFLoader and
// has the parts its builder asks for; a default housing hides only the primitive housing it
// replaces, never a moving or status-colored part; a scene mesh (PLAN 2.15) wins over it; a model
// that fails to load leaves the primitive and is counted.
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { describe, expect, it } from "vitest";
import { buildComponent } from "./components";
import type { ComponentView } from "./components/types";
import { assemble, attachHousings, type ModelLoader } from "./housings";
// the committed models as base64 data URIs (Vite ?inline), so the test needs no Node file API
import conveyorFrame from "../public/housings/conveyor_frame.glb?inline";
import photoEye from "../public/housings/photo_eye.glb?inline";
import pusher from "../public/housings/pusher.glb?inline";
import stopper from "../public/housings/stopper.glb?inline";
import turntable from "../public/housings/turntable.glb?inline";
import diverterArm from "../public/housings/diverter_arm.glb?inline";
import pushbutton from "../public/housings/pushbutton.glb?inline";
import pilotLight from "../public/housings/pilot_light.glb?inline";
import selectorSwitch from "../public/housings/selector_switch.glb?inline";

const MODELS: Record<string, string> = {
  conveyor_frame: conveyorFrame,
  photo_eye: photoEye,
  pusher,
  stopper,
  turntable,
  diverter_arm: diverterArm,
  pushbutton,
  pilot_light: pilotLight,
  selector_switch: selectorSwitch,
};
/** The types with a default model so far (PLAN 4.2). */
const TYPES = ["belt_conveyor", "roller_conveyor", "photo_eye", "pusher", "stopper", "turntable", "diverter_arm", "pushbutton", "pilot_light", "selector_switch"];

/**
 * Fixed-size housings: the model fills the primitive housing's box, give or take `grow` m on
 * any side (a collar or a motor that the plain box leaves out), so nothing floats or sinks.
 */
const FILLS: [string, Record<string, unknown>, number][] = [
  ["stopper", {}, 0],
  ["turntable", { length: 0.8, width: 0.5 }, 0.06], // the bearing and base plate are wider
  ["diverter_arm", { length: 0.6 }, 0.09], // the motor behind the pivot
  ["pushbutton", {}, 0.006], // the guard collar in front of the plate
  ["pilot_light", {}, 0.01], // the bezel ring in front of the plate
  ["selector_switch", {}, 0.002],
];
const ctx = { floorY: -0.75 };

function geom(type: string, extra: Record<string, unknown> = {}) {
  return { id: "X_01", type, description: type, position: [0, 0.2, 0] as [number, number, number], rotation_deg: 0, ...extra };
}

async function parse(model: string): Promise<THREE.Object3D> {
  const uri = MODELS[model];
  if (!uri) throw new Error(`no model ${model}`);
  const bytes = Uint8Array.from(atob(uri.split(",")[1]), (c) => c.charCodeAt(0));
  return (await new GLTFLoader().parseAsync(bytes.buffer, "")).scene;
}

const committed: ModelLoader = (model) => parse(model);

function bounds(object: THREE.Object3D): THREE.Box3 {
  let root = object;
  while (root.parent) root = root.parent;
  root.updateMatrixWorld(true); // from the top, so a moved parent group counts
  return new THREE.Box3().setFromObject(object);
}

describe("default component models", () => {
  it("are each under the 1 MB rule and hold every part a builder places", async () => {
    for (const [model, uri] of Object.entries(MODELS)) {
      expect(atob(uri.split(",")[1]).length).toBeLessThan(1_000_000);
      const root = await parse(model);
      for (const type of TYPES) {
        const view = buildComponent(geom(type, { length: 2, width: 0.5 }), ctx);
        if (view.defaultHousing?.model !== model) continue;
        expect(() => assemble(root, view.defaultHousing!)).not.toThrow();
      }
    }
  });

  it("fit a belt conveyor's frame to its length, width and height above the floor", async () => {
    const view = buildComponent(geom("belt_conveyor", { length: 3, width: 0.6 }), ctx);
    // both in world space: the primitive frame's parts, and then the model that replaces them
    const primitive = view.housing!.reduce((box, part) => box.union(bounds(part)), new THREE.Box3());
    const result = await attachHousings([view], new Set(), committed);
    expect(result).toEqual({ loaded: 1, failed: 0, types: ["belt_conveyor"] });
    const model = bounds(view.object.getObjectByName("housing")!);
    // the model fills the primitive frame's footprint: rail ends, rail sides, floor to rail top
    expect(model.min.x).toBeCloseTo(primitive.min.x, 2);
    expect(model.max.x).toBeCloseTo(primitive.max.x, 2);
    // the rails cover the primitive rails; only the drums' bearing blocks stand outside them
    expect(model.min.z).toBeLessThanOrEqual(primitive.min.z + 1e-6);
    expect(model.max.z).toBeGreaterThanOrEqual(primitive.max.z - 1e-6);
    expect(model.max.z - primitive.max.z).toBeLessThan(0.06);
    expect(model.max.y).toBeCloseTo(primitive.max.y, 2);
    expect(model.min.y).toBeCloseTo(primitive.min.y, 1); // the feet's rubber pads reach the floor
    for (const part of view.housing!) expect(part.visible).toBe(false);
    expect(view.object.getObjectByName("belt")!.visible).toBe(true); // status stays a primitive
    expect(view.object.getObjectByName("slats")!.visible).toBe(true);
  });

  it("give every listed type a default model, and the roller conveyor no drums", () => {
    for (const type of TYPES) expect(buildComponent(geom(type), ctx).defaultHousing).toBeDefined();
    const roller = buildComponent(geom("roller_conveyor", { length: 2 }), ctx).defaultHousing!;
    expect(roller.parts.map((p) => p.part)).not.toContain("drum");
  });

  it("hang the photo eye's heads in its moving group, facing the beam from both ends", async () => {
    const view = buildComponent(geom("photo_eye", { beam_length: 0.6 }), ctx);
    const replaced = view.defaultHousing!.replaces!;
    expect(await attachHousings([view], new Set(), committed)).toMatchObject({ loaded: 1, types: ["photo_eye"] });
    const housing = view.object.getObjectByName("housing")!;
    const at = bounds(housing);
    expect(at.min.z).toBeCloseTo(-(0.3 + 0.02 + 0.06), 3); // a bracket behind each head
    expect(at.max.z).toBeCloseTo(0.3 + 0.02 + 0.06, 3);
    for (const head of replaced) expect(head.visible).toBe(false);
    expect(view.object.getObjectByName("beam")!.visible).toBe(true); // status stays a primitive
    view.update({ detect: false, beam_offset_m: 0.05 }); // a misaligned fault moves the heads
    expect(bounds(housing).getCenter(new THREE.Vector3()).x).toBeCloseTo(at.getCenter(new THREE.Vector3()).x + 0.05, 5);
  });

  it("stretch the pusher's cylinder over its body, behind the plate, for any stroke", async () => {
    for (const stroke of [0.3, 0.6]) {
      const view = buildComponent(geom("pusher", { stroke }), ctx);
      const body = bounds(view.housing![0]);
      await attachHousings([view], new Set(), committed);
      const model = bounds(view.object.getObjectByName("housing")!);
      expect(model.min.z).toBeCloseTo(body.min.z, 5); // the rear cap ends where the body does
      expect(model.max.z).toBeCloseTo(body.max.z + 0.012, 5); // the rod gland, short of the plate
      expect(model.max.z).toBeLessThan(bounds(view.object.getObjectByName("plate")!).min.z);
      expect(view.housing![0].visible).toBe(false);
      expect(view.object.getObjectByName("plate")!.visible).toBe(true); // status and motion stay
      view.update({ position_m: stroke, extending: true });
      expect(bounds(view.object.getObjectByName("housing")!).min.z).toBeCloseTo(model.min.z, 5);
    }
  });

  it("give a turntable on the floor no pedestal model, as it has no primitive pedestal", () => {
    const low = buildComponent({ ...geom("turntable"), position: [0, -0.66, 0] as [number, number, number] }, ctx);
    expect(low.housing).toEqual([]);
    expect(low.defaultHousing).toBeUndefined();
  });

  it.each(FILLS)("fill the %s's primitive housing box", async (type, extra, grow) => {
    const view = buildComponent(geom(type, extra), ctx);
    const primitive = view.housing!.reduce((box, part) => box.union(bounds(part)), new THREE.Box3());
    expect(await attachHousings([view], new Set(), committed)).toMatchObject({ loaded: 1, failed: 0 });
    const model = bounds(view.object.getObjectByName("housing")!);
    for (const axis of ["x", "y", "z"] as const) {
      expect(model.min[axis]).toBeGreaterThanOrEqual(primitive.min[axis] - grow - 1e-6);
      expect(model.max[axis]).toBeLessThanOrEqual(primitive.max[axis] + grow + 1e-6);
      expect(model.max[axis] - model.min[axis]).toBeGreaterThan(0.6 * (primitive.max[axis] - primitive.min[axis]));
    }
    for (const part of view.housing!) expect(part.visible).toBe(false);
  });

  it("leave a component with a scene mesh, and one whose model fails, to the 2.15 path", async () => {
    const meshed = buildComponent(geom("belt_conveyor", { id: "M_01" }), ctx);
    const broken = buildComponent(geom("belt_conveyor", { id: "B_01" }), ctx);
    const fail: ModelLoader = () => Promise.reject(new Error("404"));
    expect(await attachHousings([meshed], new Set(["M_01"]), fail)).toEqual({ loaded: 0, failed: 0, types: [] });
    const warn = console.warn;
    console.warn = () => {};
    try {
      expect(await attachHousings([broken], new Set(), fail)).toEqual({ loaded: 0, failed: 1, types: [] });
    } finally {
      console.warn = warn;
    }
    for (const part of broken.housing!) expect(part.visible).toBe(true);
    expect(broken.object.getObjectByName("housing")).toBeUndefined();
  });

  it("clone parts, sharing the parsed geometry between components", async () => {
    const root = await parse("conveyor_frame");
    const views: ComponentView[] = [0, 1].map((i) => buildComponent(geom("belt_conveyor", { length: 1 + i }), ctx));
    const groups = views.map((v) => assemble(root, v.defaultHousing!));
    const rail = (g: THREE.Group) => g.children[0] as THREE.Mesh;
    expect(rail(groups[0]).geometry).toBe(rail(groups[1]).geometry);
  });
});
