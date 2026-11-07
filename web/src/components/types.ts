// Shared types and helpers for the per-type mesh builders (PLAN §5: one builder per component
// type). Builders are DOM-free (three.js objects only) so vitest can run them under node.
import * as THREE from "three";
import type { ComponentGeometry } from "../api";

/** Non-status colors for equipment and product. Status colors come from ISA101 only. */
export const NEUTRAL = {
  frame: 0x4b5563, // conveyor rails, legs, sensor housings
  structure: 0x6b7280, // cylinder bodies, rollers
  product: 0xa78b6a, // parts (cardboard), deliberately not a status color
  productAlt: 0x8c7356,
  slat: 0x374151,
} as const;

export interface BuildContext {
  /** World y of the floor, for conveyor legs. */
  floorY: number;
}

export interface ComponentView {
  id: string;
  type: string;
  /** Placed at the component origin with its rotation; children are in the local frame. */
  object: THREE.Group;
  /** Local point the id label hangs from. */
  labelAnchor: THREE.Vector3;
  /** Meshes a click on which is an operator action (pushbutton press, selector turn). */
  pickable: THREE.Object3D[];
  /**
   * The fixed housing a scene `mesh` replaces (PLAN 2.15): parts that never move and carry no
   * status color. Moving parts (plate, rod, blade, arm, carriage, deck, belt) stay primitives.
   * Empty when a type has none; a mesh is then drawn beside the primitive.
   */
  housing?: THREE.Object3D[];
  /**
   * The type's default model (PLAN 4.2), drawn in place of the primitive housing when the scene
   * gives the component no `mesh`: which parts of web/public/housings/<model>.glb go where.
   */
  defaultHousing?: DefaultHousing;
  update(state: Record<string, unknown>): void;
}

/** One copy of a model part, in the local frame of the housing's parent (PLAN 4.2). */
export interface HousingPart {
  /** The part's node name in the model (tools/make_models.py). */
  part: string;
  at: [number, number, number];
  /** Stretch per axis; a negative value mirrors. Default [1, 1, 1]. */
  scale?: [number, number, number];
  /** Turn about local +y, degrees. */
  turnDeg?: number;
}

/** A builder's default model: the file, the part copies, where they hang, what they hide. */
export interface DefaultHousing {
  /** web/public/housings/<model>.glb */
  model: string;
  parts: HousingPart[];
  /** The group the parts hang in; default the component's object. */
  parent?: THREE.Object3D;
  /** The primitives the model replaces; default the view's `housing`. */
  replaces?: THREE.Object3D[];
}

export type Builder = (geometry: ComponentGeometry, ctx: BuildContext) => ComponentView;

export function num(g: ComponentGeometry, key: string, fallback: number): number {
  const v = g[key];
  return typeof v === "number" ? v : fallback;
}

/** Group at the component's scene position, turned about +y by rotation_deg (geometry.py). */
export function placed(g: ComponentGeometry): THREE.Group {
  const group = new THREE.Group();
  group.name = g.id;
  group.position.set(g.position[0], g.position[1], g.position[2]);
  group.rotation.y = THREE.MathUtils.degToRad(g.rotation_deg);
  return group;
}

export function box(
  size: [number, number, number],
  color: number,
  at: [number, number, number] = [0, 0, 0],
): THREE.Mesh<THREE.BoxGeometry, THREE.MeshStandardMaterial> {
  const mesh = new THREE.Mesh(
    new THREE.BoxGeometry(...size),
    new THREE.MeshStandardMaterial({ color, roughness: 0.7, metalness: 0.1 }),
  );
  mesh.position.set(...at);
  return mesh;
}

/** A cylinder whose axis lies along local z. */
export function zCylinder(
  radius: number,
  length: number,
  color: number,
  at: [number, number, number] = [0, 0, 0],
): THREE.Mesh<THREE.CylinderGeometry, THREE.MeshStandardMaterial> {
  const mesh = new THREE.Mesh(
    new THREE.CylinderGeometry(radius, radius, length, 20),
    new THREE.MeshStandardMaterial({ color, roughness: 0.5, metalness: 0.3 }),
  );
  mesh.rotation.x = Math.PI / 2;
  mesh.position.set(...at);
  return mesh;
}

export function wireBox(size: [number, number, number], color: number, opacity = 0.12): THREE.Group {
  const group = new THREE.Group();
  const geometry = new THREE.BoxGeometry(...size);
  const fill = new THREE.Mesh(
    geometry,
    new THREE.MeshBasicMaterial({ color, transparent: true, opacity, depthWrite: false }),
  );
  const edges = new THREE.LineSegments(
    new THREE.EdgesGeometry(geometry),
    new THREE.LineBasicMaterial({ color }),
  );
  group.add(fill, edges);
  return group;
}

export function setColor(object: THREE.Object3D, color: number): void {
  object.traverse((o) => {
    const material = (o as THREE.Mesh).material as THREE.Material | undefined;
    if (material && "color" in material) (material as THREE.MeshBasicMaterial).color.setHex(color);
  });
}
