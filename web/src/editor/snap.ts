// Snapping a dragged conveyor's end to another's (PLAN 4.1d, ruled 2026-11-10 decision 7): the
// server publishes each conveyor's `load` ports in /api/scene (core/snaps.py); while a conveyor
// is dragged, an end that comes within CAPTURE_M of an end it can join (a discharge to an
// infeed, level within LEVEL_M) is a candidate, and on release the conveyor is placed so the two
// ends coincide and face each other exactly, and the connection is added. Pure maths, no DOM:
// unit-tested in snap.test.ts.

export interface SnapPort {
  name: string;
  kind: "load";
  position: [number, number, number];
  heading_deg: number;
  width: number;
  role: "in" | "out" | "both";
}

/** Capture distance in the floor plane (decision 7). */
export const CAPTURE_M = 0.15;
/** Two ends are level when their heights differ by no more than this (the move gizmo keeps y). */
export const LEVEL_M = 0.001;

export interface Pose {
  position: [number, number, number];
  rotation_deg: number;
}

export interface Snap {
  /** Where the dragged component goes so its end meets the other's. */
  pose: Pose;
  /** The connection to add: parts leave `from`'s end onto `to`'s. */
  connection: { from: string; to: string };
  /** The dragged end and the end it meets (for the marker). */
  mine: string;
  theirs: { id: string; port: string; position: [number, number, number] };
  /** Floor-plane distance before snapping, metres. */
  distance_m: number;
}

const rad = (deg: number) => (deg * Math.PI) / 180;

/** Rotate (x, z) about +y by `deg` (positive turns +x toward -z, as rotation_deg). */
export function rotateY(x: number, z: number, deg: number): [number, number] {
  const c = Math.cos(rad(deg));
  const s = Math.sin(rad(deg));
  return [x * c + z * s, -x * s + z * c];
}

/** A port in its component's own frame, from its published world pose and the component's. */
export function localPort(port: SnapPort, at: Pose): { x: number; y: number; z: number; heading: number } {
  const dx = port.position[0] - at.position[0];
  const dz = port.position[2] - at.position[2];
  const [x, z] = rotateY(dx, dz, -at.rotation_deg);
  return { x, y: port.position[1] - at.position[1], z, heading: port.heading_deg - at.rotation_deg };
}

/** A local port placed at `pose`, in world coordinates. */
export function worldPort(local: { x: number; y: number; z: number; heading: number }, pose: Pose) {
  const [x, z] = rotateY(local.x, local.z, pose.rotation_deg);
  return {
    position: [pose.position[0] + x, pose.position[1] + local.y, pose.position[2] + z] as [number, number, number],
    heading: local.heading + pose.rotation_deg,
  };
}

const joins = (a: SnapPort["role"], b: SnapPort["role"]) => (a === "out" || a === "both") && (b === "in" || b === "both");

/**
 * The best snap for component `id`, published at `published` with `ports`, now dragged to
 * `now`, against `others` (every other conveyor's id and ports), or null when no end is within
 * CAPTURE_M. A pair already joined the other way round (a merge, `joined`) still snaps.
 */
export function findSnap(
  id: string,
  ports: SnapPort[],
  published: Pose,
  now: Pose,
  others: { id: string; ports: SnapPort[] }[],
): Snap | null {
  let best: Snap | null = null;
  for (const mine of ports) {
    const local = localPort(mine, published);
    const at = worldPort(local, now);
    for (const other of others) {
      if (other.id === id) continue;
      for (const theirs of other.ports) {
        const out = joins(mine.role, theirs.role);
        const into = joins(theirs.role, mine.role);
        if (!out && !into) continue;
        if (Math.abs(at.position[1] - theirs.position[1]) > LEVEL_M) continue;
        const d = Math.hypot(at.position[0] - theirs.position[0], at.position[2] - theirs.position[2]);
        if (d > CAPTURE_M || (best && d >= best.distance_m)) continue;
        // face exactly opposite: my heading = theirs + 180, then put my end on theirs
        const rotation = normalize(theirs.heading_deg + 180 - local.heading);
        const [ox, oz] = rotateY(local.x, local.z, rotation);
        const pose: Pose = {
          position: [theirs.position[0] - ox, now.position[1], theirs.position[2] - oz],
          rotation_deg: rotation,
        };
        // parts go from mine to theirs unless mine only receives or theirs only sends
        const forward = mine.role !== "in" && theirs.role !== "out";
        const connection = forward ? { from: id, to: other.id } : { from: other.id, to: id };
        best = { pose, connection, mine: mine.name, theirs: { id: other.id, port: theirs.name, position: theirs.position }, distance_m: d };
      }
    }
  }
  return best;
}

/** A heading in (-180, 180]. */
export function normalize(deg: number): number {
  const r = ((deg % 360) + 360) % 360;
  return r > 180 ? r - 360 : r;
}
