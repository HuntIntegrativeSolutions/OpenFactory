// The move and turn gizmo's colors (PLAN 4.1c fix 2, ruled 2026-11-12). three's TransformControls
// draws pure red, green, blue and yellow handles, white and grey ones, which read as ISA-101
// status colors (running green, alarm red and yellow, normal-text white). Every gizmo material is
// recolored from this table, chosen outside the ISA-101 table (web/src/isa101.ts): each differs
// from every entry there by more than STATUS_MARGIN levels in at least one channel.
// docs/editor.md, "Gizmo colors", lists them.
import * as THREE from "three";
import type { TransformControls } from "three/addons/controls/TransformControls.js";

/** Smallest per-channel distance (0-255) a gizmo color keeps from every ISA-101 color. */
export const STATUS_MARGIN = 40;

export const GIZMO_COLORS = {
  /** the floor's x axis (move) */
  x: 0xd946ef, // fuchsia
  /** the vertical axis (turn ring) */
  y: 0xa78bfa, // violet
  /** the floor's z axis (move) */
  z: 0x22d3ee, // cyan
  /** a handle under the pointer or being dragged */
  active: 0xf0abfc, // light pink
  /** neutral handles: the centre handle and free-move box */
  neutral: 0xc4b5fd, // lavender
  /** the turn gizmo's dim background disc */
  dim: 0x7c6f9f, // grey violet
} as const;

/** three's defaults (r186 TransformControls) and the color each becomes. */
const REMAP = new Map<number, number>([
  [0xff0000, GIZMO_COLORS.x],
  [0x00ff00, GIZMO_COLORS.y],
  [0x0000ff, GIZMO_COLORS.z],
  [0xffff00, GIZMO_COLORS.active],
  [0xffffff, GIZMO_COLORS.neutral],
  [0x787878, GIZMO_COLORS.dim],
]);

function materials(root: THREE.Object3D): THREE.Material[] {
  const out = new Set<THREE.Material>();
  root.traverse((o) => {
    const m = (o as THREE.Mesh).material as THREE.Material | THREE.Material[] | undefined;
    for (const x of Array.isArray(m) ? m : m ? [m] : []) out.add(x);
  });
  return [...out];
}

/** Every color the gizmo can draw: each handle's material, and the active color it switches to. */
export function gizmoColors(controls: TransformControls): number[] {
  const lib = (controls as unknown as { _gizmo: { materialLib: Record<string, THREE.MeshBasicMaterial> } })._gizmo.materialLib;
  const colors = new Set<number>();
  for (const m of [...materials(controls.getHelper()), ...Object.values(lib)]) {
    if ("color" in m) colors.add((m as THREE.MeshBasicMaterial).color.getHex());
  }
  return [...colors];
}

/**
 * Recolor `controls`' gizmo from GIZMO_COLORS; call once, before it is first drawn (a handle
 * caches its color the first time it is highlighted). A color three adds in a later release is
 * not in REMAP: it throws, so an upgrade cannot bring a status color back unnoticed.
 */
export function recolorGizmo(controls: TransformControls): void {
  controls.setColors(GIZMO_COLORS.x, GIZMO_COLORS.y, GIZMO_COLORS.z, GIZMO_COLORS.active);
  for (const m of materials(controls.getHelper())) {
    if (!("color" in m)) continue;
    const color = (m as THREE.MeshBasicMaterial).color;
    const hex = color.getHex();
    if ([...REMAP.values()].includes(hex)) continue;
    const to = REMAP.get(hex);
    if (to === undefined) throw new Error(`gizmo color #${hex.toString(16).padStart(6, "0")} has no entry in GIZMO_COLORS`);
    color.setHex(to);
  }
}
