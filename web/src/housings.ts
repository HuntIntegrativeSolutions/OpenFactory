// Default component models (PLAN 4.2): a builder that names a `defaultHousing` gets its fixed
// housing drawn from our own model, web/public/housings/<model>.glb (tools/make_models.py), when
// the scene gives the component no `mesh` of its own (PLAN 2.15, which still wins). A model is a
// kit of named parts; the builder lists where each copy goes and how far it stretches, from the
// scene's geometry, so one file fits every length, width and height. Each file is fetched and
// parsed once; each component's part copies are baked and merged into one mesh per material
// (materials shared between components), so a model costs a few draw calls. Visual only:
// physics, sensors and the core keep the declared dimensions. If a file fails to load, the
// primitive housing stays and one console warning says why.
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { mergeGeometries } from "three/examples/jsm/utils/BufferGeometryUtils.js";
import type { ComponentView, DefaultHousing } from "./components/types";

export type ModelLoader = (model: string) => Promise<THREE.Object3D>;

const gltf = new GLTFLoader();
const cache = new Map<string, Promise<THREE.Object3D>>();

/** Fetch and parse /housings/<model>.glb once per page. */
export const loadModel: ModelLoader = (model) => {
  let pending = cache.get(model);
  if (!pending) {
    pending = gltf.loadAsync(`/housings/${model}.glb`).then((g) => g.scene);
    cache.set(model, pending);
  }
  return pending;
};

/**
 * The listed parts of `root`, each placed, turned about y and scaled, merged into one mesh per
 * material: a conveyor frame of twenty-odd part copies costs four draw calls, not twenty-odd
 * (ruled 2026-11-07: the sorter under 45 with its four housings). The baked geometry is this
 * component's own; the materials are the parsed model's, shared by every component using it.
 */
export function assemble(root: THREE.Object3D, housing: DefaultHousing): THREE.Group {
  root.updateMatrixWorld(true);
  const pieces = new Map<THREE.Material, THREE.BufferGeometry[]>();
  const placed = new THREE.Matrix4();
  const toPart = new THREE.Matrix4();
  const bake = new THREE.Matrix4();
  for (const place of housing.parts) {
    const part = root.getObjectByName(place.part);
    if (!part) throw new Error(`${housing.model}.glb has no part "${place.part}"`);
    placed.compose(
      new THREE.Vector3(...place.at),
      new THREE.Quaternion().setFromEuler(new THREE.Euler(0, THREE.MathUtils.degToRad(place.turnDeg ?? 0), 0)),
      new THREE.Vector3(...(place.scale ?? [1, 1, 1])),
    );
    toPart.copy(part.matrixWorld).invert();
    part.traverse((o) => {
      const mesh = o as THREE.Mesh;
      if (!mesh.isMesh) return;
      bake.multiplyMatrices(placed, toPart).multiply(mesh.matrixWorld);
      const geometry = mesh.geometry.clone().applyMatrix4(bake);
      if (bake.determinant() < 0) flipWinding(geometry); // a mirrored copy (scale -1) faces out again
      const material = mesh.material as THREE.Material;
      pieces.set(material, [...(pieces.get(material) ?? []), geometry]);
    });
  }
  const group = new THREE.Group();
  group.name = "housing";
  for (const [material, geometries] of pieces) {
    const merged = mergeGeometries(geometries, false);
    if (!merged) throw new Error(`${housing.model}.glb: parts could not be merged`);
    for (const g of geometries) g.dispose();
    group.add(new THREE.Mesh(merged, material));
  }
  return group;
}

/** Reverse each triangle's vertex order, so a mirrored geometry's front faces point outward. */
function flipWinding(geometry: THREE.BufferGeometry): void {
  const index = geometry.getIndex();
  if (!index) throw new Error("default models are indexed (tools/make_models.py)");
  for (let i = 0; i < index.count; i += 3) {
    const b = index.getX(i + 1);
    index.setX(i + 1, index.getX(i + 2));
    index.setX(i + 2, b);
  }
  index.needsUpdate = true;
}

/** Draw `view`'s default housing in place of its primitive one. */
export async function attachHousing(view: ComponentView, load: ModelLoader = loadModel): Promise<void> {
  const housing = view.defaultHousing!;
  const group = assemble(await load(housing.model), housing);
  for (const part of housing.replaces ?? view.housing ?? []) part.visible = false;
  (housing.parent ?? view.object).add(group);
}

/**
 * Attach every default housing, except on components whose scene declares a mesh (`meshed`).
 * Resolves when all have loaded or failed (never rejects), with the component types drawn.
 */
export async function attachHousings(
  views: ComponentView[],
  meshed: Set<string>,
  load: ModelLoader = loadModel,
): Promise<{ loaded: number; failed: number; types: string[] }> {
  const pending = views.filter((v) => v.defaultHousing && !meshed.has(v.id));
  const results = await Promise.allSettled(pending.map((v) => attachHousing(v, load)));
  const types = new Set<string>();
  let loaded = 0;
  results.forEach((r, i) => {
    if (r.status === "fulfilled") {
      loaded += 1;
      types.add(pending[i].type);
    } else console.warn(`${pending[i].id}: default model not drawn (${String(r.reason)}); primitive kept`);
  });
  return { loaded, failed: results.length - loaded, types: [...types].sort() };
}
