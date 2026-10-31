// PLAN 2.15: the example housing is a valid glTF 2.0 file to three's own loader, and a mesh hides
// only the fixed housing it replaces (the pusher's body), never the moving or status-colored parts.
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { describe, expect, it } from "vitest";
import { BUILDERS, buildComponent } from "./components";
// the committed example as a base64 data URI (Vite ?inline), so the test needs no Node file API
import glbDataUri from "../../tests/scenes/models/pusher-housing.glb?inline";
const ctx = { floorY: -0.75 };

function geom(type: string, extra: Record<string, unknown> = {}) {
  return { id: "X_01", type, description: type, position: [0, 0.2, 0] as [number, number, number], rotation_deg: 0, ...extra };
}

describe("custom meshes", () => {
  it("parse the example housing with GLTFLoader: two primitives spanning the primitive body", async () => {
    const bytes = Uint8Array.from(atob(glbDataUri.split(",")[1]), (c) => c.charCodeAt(0));
    expect(bytes.byteLength).toBe(6744);
    const gltf = await new GLTFLoader().parseAsync(bytes.buffer, "");
    const meshes: THREE.Mesh[] = [];
    gltf.scene.traverse((o) => {
      if ((o as THREE.Mesh).isMesh) meshes.push(o as THREE.Mesh);
    });
    expect(meshes.length).toBe(2); // aluminium body and rods, dark caps and foot
    const bounds = new THREE.Box3().setFromObject(gltf.scene);
    expect(bounds.min.z).toBeCloseTo(-0.76, 5);
    expect(bounds.max.z).toBeCloseTo(-0.06, 5);
    expect(bounds.max.x).toBeCloseTo(0.09, 5);
  });

  it("name the pusher's body as its housing, not the rod or the plate", () => {
    const view = buildComponent(geom("pusher", { stroke: 0.6 }), ctx);
    expect(view.housing).toHaveLength(1);
    const plate = view.object.getObjectByName("plate")!;
    expect(view.housing).not.toContain(plate);
    const body = view.housing![0] as THREE.Mesh;
    expect((body.geometry as THREE.CylinderGeometry).parameters.radiusTop).toBeCloseTo(0.045);
  });

  it("list only children of the component as housing, for every type", () => {
    for (const type of Object.keys(BUILDERS)) {
      const view = buildComponent(geom(type, { length: 2, width: 0.5, beam_length: 0.6, stroke: 0.5, size: [0.3, 1, 1], travel: 1 }), ctx);
      for (const part of view.housing ?? []) {
        let up: THREE.Object3D | null = part;
        while (up && up !== view.object) up = up.parent;
        expect(up, `${type} housing part outside its group`).toBe(view.object);
      }
    }
  });
});
