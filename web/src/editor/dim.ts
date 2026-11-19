// The editor on a plant (PLAN 5.4e, design note §4.3): one file is edited and the whole plant is
// shown. The server marks each component of the preview document with `source` (its file,
// relative to the plant's folder) and `editable` (whether it is in the edited file). Components of
// another file are drawn dimmed, and selecting one says which command edits it instead of showing a
// form; no edit operation applies to them (the panel has no data for them, main.ts attaches no
// gizmo). DOM-free, so dim.test.ts runs under node.
import * as THREE from "three";
import type { SceneDocument } from "../api";

/** How opaque another file's component is drawn. */
export const DIM_OPACITY = 0.25;

/** The file a component is read-only in, when the document is a plant's and the component is not
 * in the edited file; null when it is editable (or the document is not a plant's). A composite's
 * part carries its instance's file. */
export function foreignFile(doc: SceneDocument | null, id: string): string | null {
  const comp = doc?.components.find((c) => c.id === id);
  if (!comp || comp.editable !== false) return null;
  return typeof comp.source === "string" ? comp.source : null;
}

/** What selecting another file's component says (ruled 2026-11-18): its file and the command
 * that edits it, the plant with that file's `--area`, or the plant alone for the plant file's own
 * components. */
export function readOnlyMessage(file: string, edit: SceneDocument["edit"]): string {
  const area = edit?.areas?.[file];
  const command = edit ? `openfactory edit ${edit.plant}${area ? ` --area ${area}` : ""}` : "";
  return command ? `in ${file}: edit it with \`${command}\`` : `in ${file}: open that file to edit it`;
}

function dimmed(material: THREE.Material, opacity: number): THREE.Material {
  if (material.userData.dimmed) return material;
  const out = material.clone();
  out.transparent = true;
  out.opacity = material.opacity * opacity;
  out.depthWrite = false;
  out.userData.dimmed = true;
  return out;
}

/** Draw `object` (a component's view) dimmed: every mesh, line and point set gets its own
 * transparent copy of its material (materials may be shared with editable components), and a
 * mesh that swaps to a status material (types.ts `showStatus`) has both copies dimmed. Returns
 * how many materials were dimmed. */
export function dimObject(object: THREE.Object3D, opacity = DIM_OPACITY): number {
  let count = 0;
  object.traverse((child) => {
    const drawn = child as THREE.Mesh;
    if (!drawn.material) return;
    const one = (m: THREE.Material) => {
      count += m.userData.dimmed ? 0 : 1;
      return dimmed(m, opacity);
    };
    drawn.material = Array.isArray(drawn.material) ? drawn.material.map(one) : one(drawn.material);
    const data = child.userData as { lit?: THREE.Material; statusMaterial?: THREE.Material };
    if (data.lit) data.lit = dimmed(data.lit, opacity);
    if (data.statusMaterial) data.statusMaterial = dimmed(data.statusMaterial, opacity);
  });
  return count;
}

/** Dim every drawn view of a component not in the edited file; the ids dimmed. */
export function dimForeign(doc: SceneDocument, views: { id: string; object: THREE.Object3D }[]): string[] {
  const out: string[] = [];
  for (const view of views) {
    if (foreignFile(doc, view.id) === null) continue;
    dimObject(view.object);
    out.push(view.id);
  }
  return out;
}
