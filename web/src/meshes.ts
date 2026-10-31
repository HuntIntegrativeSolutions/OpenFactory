// Custom meshes (PLAN 2.15): a component whose scene declares `mesh` gets that .glb drawn in place
// of its fixed housing (ComponentView.housing). The mesh is visual only: physics, sensors, and the
// core keep the declared dimensions. The server serves the file at /models/<id>.glb and lists it
// in the scene document only when the loader found a sound file, so a missing or broken file never
// reaches here; if a load still fails, the primitive stays and one console warning says why.
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import type { MeshSpec } from "./api";
import type { ComponentView } from "./components";

const loader = new GLTFLoader();

/**
 * Load `spec` and hang it on `view`: offset in the component's local frame, turned by
 * rotation_deg about local y, scaled; then hide the housing primitives it replaces.
 */
export async function attachMesh(view: ComponentView, spec: MeshSpec): Promise<void> {
  const gltf = await loader.loadAsync(spec.url);
  const holder = new THREE.Group();
  holder.name = "mesh";
  holder.position.set(...spec.offset);
  holder.rotation.y = THREE.MathUtils.degToRad(spec.rotation_deg);
  holder.scale.set(...spec.scale);
  holder.add(gltf.scene);
  for (const part of view.housing ?? []) part.visible = false;
  view.object.add(holder);
}

/** Attach every declared mesh; resolves when all have loaded or failed (never rejects). */
export async function attachMeshes(views: ComponentView[], specs: Map<string, MeshSpec>): Promise<{ loaded: number; failed: number }> {
  const meshed = views.filter((v) => specs.has(v.id));
  const results = await Promise.allSettled(meshed.map((v) => attachMesh(v, specs.get(v.id)!)));
  let loaded = 0;
  results.forEach((r, i) => {
    if (r.status === "fulfilled") loaded += 1;
    else console.warn(`${meshed[i].id}: custom mesh not drawn (${String(r.reason)}); primitive kept`);
  });
  return { loaded, failed: results.length - loaded };
}
