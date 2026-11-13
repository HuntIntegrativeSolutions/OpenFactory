// Snap markers (PLAN 4.1d): while a dragged conveyor's end is within capture of another's, a
// ring marks the end it will meet and a dot marks its own end. They are edit overlays (no
// shadows, lighting.ts editOverlay) in a gizmo color, never an ISA-101 status color
// (editor/gizmo.ts), drawn over the plant.
import * as THREE from "three";
import { editOverlay } from "../lighting";
import { GIZMO_COLORS } from "./gizmo";

/** The marker color: the gizmo's active color (outside the ISA-101 table, gizmo.test.ts). */
export const SNAP_MARKER_COLOR = GIZMO_COLORS.active;

function flat(geometry: THREE.BufferGeometry): THREE.Mesh {
  const mesh = new THREE.Mesh(
    geometry,
    new THREE.MeshBasicMaterial({ color: SNAP_MARKER_COLOR, transparent: true, opacity: 0.9, depthTest: false, toneMapped: false, side: THREE.DoubleSide }),
  );
  mesh.rotation.x = -Math.PI / 2; // lie on the belt
  mesh.renderOrder = 10;
  return mesh;
}

/** A group holding the target ring and the dragged end's dot; hidden until `show`. */
export class SnapMarker {
  readonly object = editOverlay(new THREE.Group());
  private readonly ring = flat(new THREE.RingGeometry(0.06, 0.09, 32));
  private readonly dot = flat(new THREE.CircleGeometry(0.035, 24));

  constructor() {
    this.object.name = "snap-marker";
    this.object.add(this.ring, this.dot);
    editOverlay(this.object); // again, now that it has children
    this.object.visible = false;
  }

  show(target: [number, number, number], mine: [number, number, number]): void {
    this.ring.position.set(target[0], target[1] + 0.005, target[2]);
    this.dot.position.set(mine[0], mine[1] + 0.006, mine[2]);
    this.object.visible = true;
  }

  hide(): void {
    this.object.visible = false;
  }
}
