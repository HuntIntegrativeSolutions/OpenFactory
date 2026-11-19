// Aimed views and a plan view (PLAN 5.14c). DOM-free, so views.test.ts runs under node.
//
// - `?look=ID` frames one component and what touches it: framing.fitPose over the drawn box of the
//   component (a composite instance's parts with it) and of every component it is connected to,
//   grown 30 % about its centre. What touches it (lookTargets): a `connections:` entry naming it at
//   either end; a link field (`from`, `to`, `measures`, `tank`) naming it, or named by it, as a
//   component id with or without a `.nozzle`, when the document carries one; and a link (a pipe,
//   valve or pump: a component with drawn `points`) whose first or last point is within 5 cm of
//   its drawn box, or of whose drawn box its own end is within 5 cm (linkTouches: the scene
//   document carries a link's drawn points, not its from and to).
// - `?view=px,py,pz,tx,ty,tz` sets the camera's position and the point it looks at, in metres.
// - `?view=top` is a plan view: a straight-down orthographic camera with north up, +x to the
//   right. North is the scene's -z: seen from above, positive rotation_deg turns +x toward -z
//   (docs/components.md), as a compass bearing turns from east toward north on a map.
// - A `?view=` value is six finite numbers (position and target apart) or the word `top`; anything
//   else is the default view with a console warning (parseView), never an exception. An id `?look=`
//   does not name is the default view with a warning too.
// - The scale bar's length is 1, 2 or 5 times a power of ten metres, the first to reach 80 px.
// - "Copy view" copies `view=...` for the view on screen (viewParam).
import * as THREE from "three";
import type { SceneDocument } from "./api";
import { FILL } from "./framing";

export type ViewRequest =
  | { kind: "pose"; position: THREE.Vector3; target: THREE.Vector3 }
  | { kind: "top" }
  | { kind: "default"; warning: string | null };

/** `?view=`'s value (null when absent) as a request; anything not understood is the default view
 * with the warning to log. */
export function parseView(raw: string | null): ViewRequest {
  if (raw === null) return { kind: "default", warning: null };
  const text = raw.trim();
  if (text === "top") return { kind: "top" };
  const parts = text.split(",");
  const numbers = parts.map((p) => (p.trim() === "" ? NaN : Number(p)));
  if (parts.length !== 6 || !numbers.every(Number.isFinite)) {
    return {
      kind: "default",
      warning: `?view=${raw}: not six numbers (px,py,pz,tx,ty,tz) or "top"; the default view is shown`,
    };
  }
  const position = new THREE.Vector3(numbers[0], numbers[1], numbers[2]);
  const target = new THREE.Vector3(numbers[3], numbers[4], numbers[5]);
  if (position.distanceTo(target) < 1e-6) {
    return { kind: "default", warning: `?view=${raw}: the position and the target are one point; the default view is shown` };
  }
  return { kind: "pose", position, target };
}

const LINK_FIELDS = ["from", "to", "measures", "tank"] as const;

/** The component id a link field's value names (`TK_01.outlet` names TK_01). */
function named(value: unknown): string | null {
  return typeof value === "string" && value ? value.split(".")[0] : null;
}

/** Pairs of ids that touch because a link's end point (its `points`' first or last) lies within
 * `tolerance` metres of another component's drawn box (`boxes`, by view id). */
export function linkTouches(doc: SceneDocument, boxes: Map<string, THREE.Box3>, tolerance = 0.05): [string, string][] {
  const out: [string, string][] = [];
  for (const link of doc.components) {
    const points = link.points as number[][] | undefined;
    if (!Array.isArray(points) || points.length < 2) continue;
    for (const end of [points[0], points[points.length - 1]]) {
      const at = new THREE.Vector3(end[0], end[1], end[2]);
      for (const [id, box] of boxes) {
        if (id === link.id || box.isEmpty()) continue;
        if (box.clone().expandByScalar(tolerance).containsPoint(at)) out.push([link.id, id]);
      }
    }
  }
  return out;
}

/** The ids `?look=id` frames: the component, the parts of a composite instance, and every
 * component it is connected to (see the module comment), each once, in scene order. Empty when
 * the document has no such component. */
export function lookTargets(doc: SceneDocument, id: string, touches: [string, string][] = []): string[] {
  const instance = (c: SceneDocument["components"][number]) => (c.composite as { id?: string } | undefined)?.id;
  const own = doc.components.filter((c) => c.id === id || instance(c) === id);
  if (!own.length) return [];
  const touching = new Set<string>([id]);
  for (const conn of doc.connections ?? []) {
    const ends = [named(conn.from), named(conn.to)];
    if (ends.includes(id)) for (const e of ends) if (e) touching.add(e);
  }
  const owner = (vid: string) => {
    const c = doc.components.find((g) => g.id === vid);
    return (c && instance(c)) ?? vid;
  };
  for (const [a, b] of touches) {
    if (owner(a) === id) touching.add(owner(b));
    if (owner(b) === id) touching.add(owner(a));
  }
  for (const c of doc.components) {
    for (const field of LINK_FIELDS) {
      const target = named(c[field]);
      if (target === null) continue;
      if (target === id) touching.add(c.id);
      if (c.id === id || instance(c) === id) touching.add(target);
    }
  }
  return doc.components.filter((c) => touching.has(c.id) || touching.has(instance(c) ?? "")).map((c) => c.id);
}

/** A box grown by `fraction` of its size about its centre (30 % for ?look=). */
export function grow(box: THREE.Box3, fraction = 0.3): THREE.Box3 {
  const centre = box.getCenter(new THREE.Vector3());
  const half = box.getSize(new THREE.Vector3()).multiplyScalar((1 + fraction) / 2);
  return new THREE.Box3(centre.clone().sub(half), centre.clone().add(half));
}

export interface PlanFrame {
  /** Centre of the view on the floor plane (x, z), and the half height of the frustum, metres. */
  centre: THREE.Vector3;
  halfHeight: number;
}

/** The plan view's frame for `bounds` in a window of `aspect` (width / height): centred on the
 * bounds, the whole x-z extent within FILL of the frustum both ways. */
export function planFrame(bounds: THREE.Box3, aspect: number): PlanFrame {
  const size = bounds.getSize(new THREE.Vector3());
  const centre = bounds.getCenter(new THREE.Vector3());
  const halfHeight = Math.max(size.z / 2, size.x / 2 / Math.max(aspect, 1e-6), 0.5) / FILL;
  return { centre, halfHeight };
}

/** Point an orthographic camera straight down at `frame`: north (-z) up, +x right, from above
 * `top` (the plant's highest point). */
export function aimPlan(camera: THREE.OrthographicCamera, frame: PlanFrame, aspect: number, top: number, floor: number): void {
  camera.left = -frame.halfHeight * aspect;
  camera.right = frame.halfHeight * aspect;
  camera.top = frame.halfHeight;
  camera.bottom = -frame.halfHeight;
  camera.up.set(0, 0, -1);
  camera.position.set(frame.centre.x, top + 10, frame.centre.z);
  camera.near = 0.05;
  camera.far = top - floor + 20;
  camera.lookAt(frame.centre.x, floor, frame.centre.z);
  camera.updateProjectionMatrix();
}

/** The scale bar for `pixelsPerMetre`: a length of 1, 2 or 5 times a power of ten metres, the first
 * at least `minPx` long, and its width in pixels. */
export function scaleBar(pixelsPerMetre: number, minPx = 80): { metres: number; px: number; label: string } {
  let metres = 0.01;
  for (let i = 0; i < 40 && metres * pixelsPerMetre < minPx; i++) {
    const exp = Math.floor(Math.log10(metres) + 1e-9);
    const lead = Math.round(metres / 10 ** exp);
    metres = lead === 1 ? 2 * 10 ** exp : lead === 2 ? 5 * 10 ** exp : 10 ** (exp + 1);
  }
  const label = metres >= 1 ? `${Number(metres.toPrecision(3))} m` : `${Math.round(metres * 1000)} mm`;
  return { metres, px: metres * pixelsPerMetre, label };
}

/** `view=...` for the view on screen: `top` in the plan view, else the camera's position and
 * target to the millimetre. */
export function viewParam(top: boolean, position: THREE.Vector3, target: THREE.Vector3): string {
  if (top) return "view=top";
  const mm = (v: number) => String(Number(v.toFixed(3)));
  return `view=${[...position.toArray(), ...target.toArray()].map(mm).join(",")}`;
}
