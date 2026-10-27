// Parts drawn from the core's state frames: one box per part id, created when a part first
// appears and removed when the core removes it (the core decides which parts exist). With viewer
// physics (PLAN 1.12) each mesh takes its Rapier body's pose via setPose(); without it (Rapier
// failed to load) its target is the core's kinematic position. Either way the mesh eases toward its
// target every rendered frame, because poses arrive only with 20 Hz state frames (PLAN 2.0a: physics
// steps on core ticks, not between frames).
import * as THREE from "three";
import type { PartState } from "./api";
import { NEUTRAL } from "./components/types";

const EASE = 0.35;
/** A pose this far from the mesh is a jump (respawn, reset): snap instead of easing. */
const SNAP_M = 0.5;

export class PartMeshes {
  readonly group = new THREE.Group();
  private meshes = new Map<
    string,
    { mesh: THREE.Mesh; target: THREE.Vector3; turn: THREE.Quaternion; physics: boolean }
  >();
  private materials = new Map<string, THREE.MeshStandardMaterial>();

  private material(name: string): THREE.MeshStandardMaterial {
    let m = this.materials.get(name);
    if (!m) {
      const tone = this.materials.size % 2 ? NEUTRAL.productAlt : NEUTRAL.product;
      m = new THREE.MeshStandardMaterial({ color: tone, roughness: 0.9 });
      this.materials.set(name, m);
    }
    return m;
  }

  /** Sync with a frame's part list. */
  update(parts: PartState[]): void {
    const seen = new Set<string>();
    for (const p of parts) {
      seen.add(p.id);
      let entry = this.meshes.get(p.id);
      if (!entry) {
        const mesh = new THREE.Mesh(new THREE.BoxGeometry(...p.size), this.material(p.name));
        mesh.name = p.id;
        mesh.position.set(...p.position);
        const edges = new THREE.LineSegments(
          new THREE.EdgesGeometry(mesh.geometry),
          new THREE.LineBasicMaterial({ color: 0x3f3428 }),
        );
        mesh.add(edges);
        entry = { mesh, target: new THREE.Vector3(...p.position), turn: new THREE.Quaternion(), physics: false };
        this.meshes.set(p.id, entry);
        this.group.add(mesh);
      }
      if (!entry.physics) entry.target.set(...p.position);
    }
    for (const [id, entry] of this.meshes) {
      if (!seen.has(id)) {
        this.group.remove(entry.mesh);
        entry.mesh.geometry.dispose();
        this.meshes.delete(id);
      }
    }
  }

  /** Pose from the physics world; from then on it is the mesh's target instead of the core position. */
  setPose(id: string, position: [number, number, number], rotation: [number, number, number, number]): void {
    const entry = this.meshes.get(id);
    if (!entry) return;
    entry.target.set(...position);
    entry.turn.set(...rotation);
    if (!entry.physics || entry.mesh.position.distanceTo(entry.target) > SNAP_M) {
      entry.mesh.position.copy(entry.target); // first pose, or a jump: no easing
      entry.mesh.quaternion.copy(entry.turn);
    }
    entry.physics = true;
  }

  /** Call once per rendered frame. */
  animate(): void {
    for (const { mesh, target, turn, physics } of this.meshes.values()) {
      mesh.position.lerp(target, EASE);
      if (physics) mesh.quaternion.slerp(turn, EASE);
    }
  }

  /** Drop every mesh (the server restarted: part ids start again from P000001). */
  clear(): void {
    this.update([]);
  }

  get count(): number {
    return this.meshes.size;
  }
}
