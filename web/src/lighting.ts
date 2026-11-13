// Viewer lighting and materials (PLAN 4.14): the light rig for each quality level (quality.ts),
// the shadow camera fitted to the plant, which objects cast and receive shadows, and the floor.
// DOM-free (three.js objects only) so web/src/lighting.test.ts runs under node; the environment
// map is made by a factory the caller passes, because building it needs a WebGL renderer.
//
// Three.js references (r186, the pinned version):
//   tone mapping and shadow map types  https://threejs.org/docs/#api/en/constants/Renderer
//   a material left out of tone mapping https://threejs.org/docs/#api/en/materials/Material.toneMapped
//   environment lighting               https://threejs.org/docs/#api/en/scenes/Scene.environment
//                                      https://threejs.org/docs/#api/en/extras/PMREMGenerator
//   shadows                            https://threejs.org/docs/#api/en/lights/shadows/DirectionalLightShadow
// In r186 PCFSoftShadowMap is removed (it falls back to PCFShadowMap with a console warning);
// PCFShadowMap softens with hardware 4-tap filtering plus a disc of samples scaled by
// `shadow.radius`, which is what the high level uses.
import * as THREE from "three";
import type { Quality } from "./quality";

/** Today's lights (before 4.14): the low level keeps them exactly. */
export const LOW_LIGHTS = { hemisphere: 1.6, sun: 1.6, sunColor: 0xffffff, sunAt: [3, 8, 5] as const };

/** Standard and high: the environment carries the fill, a warm sun is the key light. */
export const LIT = { hemisphere: 0.45, sun: 2.0, sunColor: 0xfff4e6, exposure: 1.0, environment: 0.6 };

/** High: soft shadows from a 2048 square map, the camera fitted to the plant's bounds. */
export const SHADOW = { mapSize: 2048, radius: 4, bias: -0.0005, normalBias: 0.02, margin: 0.5 };

/** The sun keeps today's direction at every level; only how far away it sits changes. */
const SUN_DIRECTION = new THREE.Vector3(...LOW_LIGHTS.sunAt).normalize();

/** The renderer settings a level touches; a real WebGLRenderer, or a plain object in tests. */
export interface RendererSettings {
  toneMapping: THREE.ToneMapping;
  toneMappingExposure: number;
  shadowMap: { enabled: boolean; type: THREE.ShadowMapType };
}

export class Lighting {
  readonly hemisphere = new THREE.HemisphereLight(0xffffff, 0x334155, LOW_LIGHTS.hemisphere);
  readonly sun = new THREE.DirectionalLight(LOW_LIGHTS.sunColor, LOW_LIGHTS.sun);
  level: Quality | null = null;
  private environment: THREE.Texture | null = null;
  private bounds: THREE.Box3 | null = null;
  private readonly renderer: RendererSettings;
  private readonly scene: THREE.Scene;
  private readonly makeEnvironment: () => THREE.Texture;

  /** `makeEnvironment` is called once, the first time a level needs the environment. */
  constructor(renderer: RendererSettings, scene: THREE.Scene, makeEnvironment: () => THREE.Texture) {
    this.renderer = renderer;
    this.scene = scene;
    this.makeEnvironment = makeEnvironment;
    this.sun.position.set(...LOW_LIGHTS.sunAt);
    this.sun.shadow.mapSize.set(SHADOW.mapSize, SHADOW.mapSize);
    this.sun.shadow.radius = SHADOW.radius;
    this.sun.shadow.bias = SHADOW.bias;
    this.sun.shadow.normalBias = SHADOW.normalBias;
    renderer.shadowMap.type = THREE.PCFShadowMap;
    scene.add(this.hemisphere, this.sun, this.sun.target);
  }

  /** Switch to `level`. Materials are told to recompile, since tone mapping and shadows are
   * compiled into their shaders. */
  apply(level: Quality): void {
    const lit = level !== "low";
    this.renderer.toneMapping = lit ? THREE.ACESFilmicToneMapping : THREE.NoToneMapping;
    this.renderer.toneMappingExposure = lit ? LIT.exposure : 1;
    if (lit) this.environment ??= this.makeEnvironment();
    this.scene.environment = lit ? this.environment : null;
    this.scene.environmentIntensity = LIT.environment;
    this.hemisphere.intensity = lit ? LIT.hemisphere : LOW_LIGHTS.hemisphere;
    this.sun.intensity = lit ? LIT.sun : LOW_LIGHTS.sun;
    this.sun.color.setHex(lit ? LIT.sunColor : LOW_LIGHTS.sunColor);
    const shadows = level === "high";
    this.renderer.shadowMap.enabled = shadows;
    this.sun.castShadow = shadows;
    if (this.level !== null && this.level !== level) {
      this.scene.traverse((o) => {
        const material = (o as THREE.Mesh).material as THREE.Material | THREE.Material[] | undefined;
        for (const m of Array.isArray(material) ? material : material ? [material] : []) m.needsUpdate = true;
      });
    }
    this.level = level;
    this.placeSun();
  }

  /** Fit the sun and its shadow camera to the plant (`bounds`, floor included). */
  fit(bounds: THREE.Box3): void {
    this.bounds = bounds.clone();
    this.placeSun();
  }

  /** At low the sun stands where it always has; at standard and high it backs off along the
   * same direction from the plant's centre, and the shadow camera covers the plant's sphere. */
  private placeSun(): void {
    if (this.level === "low" || this.level === null || !this.bounds || this.bounds.isEmpty()) {
      this.sun.position.set(...LOW_LIGHTS.sunAt);
      this.sun.target.position.set(0, 0, 0);
      return;
    }
    const sphere = this.bounds.getBoundingSphere(new THREE.Sphere());
    const radius = sphere.radius + SHADOW.margin;
    this.sun.target.position.copy(sphere.center);
    this.sun.position.copy(sphere.center).addScaledVector(SUN_DIRECTION, radius * 2);
    const cam = this.sun.shadow.camera;
    cam.left = -radius;
    cam.right = radius;
    cam.top = radius;
    cam.bottom = -radius;
    cam.near = 0.1;
    cam.far = radius * 4;
    cam.updateProjectionMatrix();
    this.sun.target.updateMatrixWorld();
    this.sun.updateMatrixWorld();
  }
}

/** Whether `object` or an ancestor is marked noShadow (components/types.ts). */
function marked(object: THREE.Object3D): boolean {
  for (let o: THREE.Object3D | null = object; o; o = o.parent) if (o.userData.noShadow) return true;
  return false;
}

/**
 * Set which meshes under `root` cast and receive shadows (PLAN 4.14): solid equipment casts and
 * receives; status meshes cast but do not receive (they are unlit, and a shadow on one would read
 * as a status change); outline volumes (transparent), beams and decals (marked noShadow), lines,
 * labels and helpers do neither. Run it again after housings or custom meshes are attached.
 * Parts are not under a component and opt in themselves (parts.ts).
 */
export function applyShadowRoles(root: THREE.Object3D): void {
  root.traverse((o) => {
    const mesh = o as THREE.Mesh;
    if (!mesh.isMesh) {
      o.castShadow = false;
      o.receiveShadow = false;
      return;
    }
    const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    const solid = !marked(mesh) && !materials.some((m) => m.transparent);
    mesh.castShadow = solid;
    mesh.receiveShadow = solid && !mesh.userData.status;
  });
}

/**
 * Mark an edit-mode overlay (PLAN 4.14 with 4.1c): the move and turn gizmo and the selection
 * outline. It and everything under it neither cast nor receive a shadow, also when the roles are
 * set again over the whole scene, so an overlay never darkens the plant or reads as equipment.
 */
export function editOverlay<T extends THREE.Object3D>(object: T): T {
  object.userData.noShadow = true;
  object.traverse((o) => {
    o.castShadow = false;
    o.receiveShadow = false;
  });
  return object;
}

/** The floor (receives shadows, casts none) and its grid (neither), at `floorY`. */
export function buildStage(floorY: number): { floor: THREE.Mesh; grid: THREE.GridHelper } {
  const floor = new THREE.Mesh(
    new THREE.PlaneGeometry(40, 40),
    new THREE.MeshStandardMaterial({ color: 0x18222d, roughness: 1 }),
  );
  floor.name = "floor";
  floor.rotation.x = -Math.PI / 2;
  floor.position.y = floorY - 0.001;
  floor.receiveShadow = true;
  const grid = new THREE.GridHelper(40, 80, 0x324152, 0x253240);
  grid.name = "grid";
  grid.position.y = floorY;
  return { floor, grid };
}
