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

const MODELS: Record<string, string> = { conveyor_frame: conveyorFrame };
/** The types with a default model so far (PLAN 4.2). */
const TYPES = ["belt_conveyor", "roller_conveyor"];
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
  object.updateMatrixWorld(true);
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
    const primitive = bounds(new THREE.Group().add(...view.housing!.map((p) => p.clone())));
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
