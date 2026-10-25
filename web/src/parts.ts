// Parts drawn from the core's state frames (PLAN 1.11): one box per part id, created when a part
// first appears and removed when the core removes it. Frames arrive at 20 Hz and the screen
// renders faster, so each mesh eases toward its latest core position. PLAN 1.12 replaces these
// positions with Rapier bodies; the core still decides which parts exist.
import * as THREE from "three";
import type { PartState } from "./api";
import { NEUTRAL } from "./components/types";

const EASE = 0.35;

export class PartMeshes {
  readonly group = new THREE.Group();
  private meshes = new Map<string, { mesh: THREE.Mesh; target: THREE.Vector3 }>();
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
        entry = { mesh, target: new THREE.Vector3(...p.position) };
        this.meshes.set(p.id, entry);
        this.group.add(mesh);
      }
      entry.target.set(...p.position);
    }
    for (const [id, entry] of this.meshes) {
      if (!seen.has(id)) {
        this.group.remove(entry.mesh);
        entry.mesh.geometry.dispose();
        this.meshes.delete(id);
      }
    }
  }

  /** Call once per rendered frame. */
  animate(): void {
    for (const { mesh, target } of this.meshes.values()) mesh.position.lerp(target, EASE);
  }

  get count(): number {
    return this.meshes.size;
  }
}
