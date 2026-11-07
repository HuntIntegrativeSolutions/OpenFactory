// Default component models (PLAN 4.2): a builder that names a `defaultHousing` gets its fixed
// housing drawn from our own model, web/public/housings/<model>.glb (tools/make_models.py), when
// the scene gives the component no `mesh` of its own (PLAN 2.15, which still wins). A model is a
// kit of named parts; the builder lists where each copy goes and how far it stretches, from the
// scene's geometry, so one file fits every length, width and height. Each file is fetched and
// parsed once and its parts cloned per component (geometry and materials shared). Visual only:
// physics, sensors and the core keep the declared dimensions. If a file fails to load, the
// primitive housing stays and one console warning says why.
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
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

/** Clone the listed parts of `root` into one group, each placed, turned about y and scaled. */
export function assemble(root: THREE.Object3D, housing: DefaultHousing): THREE.Group {
  const group = new THREE.Group();
  group.name = "housing";
  for (const place of housing.parts) {
    const part = root.getObjectByName(place.part);
    if (!part) throw new Error(`${housing.model}.glb has no part "${place.part}"`);
    const copy = part.clone();
    copy.position.set(...place.at);
    copy.rotation.set(0, THREE.MathUtils.degToRad(place.turnDeg ?? 0), 0);
    copy.scale.set(...(place.scale ?? [1, 1, 1]));
    group.add(copy);
  }
  return group;
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
