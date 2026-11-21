// Dropping a device on a conveyor's rail (PLAN 4.1g1, ruled 2026-11-14): each belt and roller
// conveyor publishes two `mount` ports in /api/scene (core/mounts.py), its side rails. While a
// device that mounts (a sensor, pusher, stopper or diverter) is moved, the rail it would drop on
// is found here: the device's origin over the belt or within CAPTURE_M beside it, the nearer
// rail. The editor chooses only the rail and the distance along it (on the 0.05 m grid from the
// infeed end, Shift for free); the placement itself comes from the server, which has the one copy
// of the drop rules (POST /api/edit/mount, ruling 6). Pure maths, no DOM: mount.test.ts.
import { CAPTURE_M, rotateY, type SnapPort } from "./snap";

/** Device types the drop rules place (core/mounts.py MOUNT_TYPES). */
export const MOUNT_TYPES = ["photo_eye", "distance_sensor", "color_sensor", "pusher", "stopper", "diverter_arm"];
/** The along-the-rail grid while dragging (the move gizmo's). */
export const ALONG_GRID_M = 0.05;

export interface RailPort {
  name: "rail_left" | "rail_right";
  kind: "mount";
  position: [number, number, number];
  heading_deg: number;
  width: number;
  role: "both";
  length: number;
}

export interface RailDrop {
  conveyor: string;
  rail: "rail_left" | "rail_right";
  /** Metres from the infeed end along the rail. */
  along: number;
  /** The point on the rail at `along` (for the marker). */
  point: [number, number, number];
  /** How far the device's origin is outside the belt's edge (0 over the belt), metres. */
  beside_m: number;
}

/** A conveyor's rails among its published ports. */
export function railsOf(ports: (SnapPort | RailPort)[] | undefined): RailPort[] {
  return (ports ?? []).filter((p): p is RailPort => p.kind === "mount");
}

/**
 * The rail a device whose origin is at `at` would drop on: of every conveyor whose belt (between
 * its ends, widened by CAPTURE_M across) holds the point in the floor plane, the one whose belt
 * centre line is nearest; on it, the rail on the point's side. `grid` rounds the distance along
 * to ALONG_GRID_M; null when no belt is that close.
 */
export function findRail(at: [number, number, number], conveyors: { id: string; ports: (SnapPort | RailPort)[] }[], grid = true): RailDrop | null {
  let best: { drop: RailDrop; off: number } | null = null;
  for (const c of conveyors) {
    const rails = railsOf(c.ports);
    const left = rails.find((r) => r.name === "rail_left");
    const right = rails.find((r) => r.name === "rail_right");
    if (!left || !right) continue;
    // the belt frame: x along the flow from the infeed end, z across (left rail at -width/2)
    const flow = left.heading_deg + 90; // the left rail faces in (+z), 90 degrees right of the flow
    const half = left.width / 2;
    const cx = (left.position[0] + right.position[0]) / 2;
    const cz = (left.position[2] + right.position[2]) / 2;
    const [x, z] = rotateY(at[0] - cx, at[2] - cz, -flow);
    if (x < -CAPTURE_M || x > left.length + CAPTURE_M || Math.abs(z) > half + CAPTURE_M) continue;
    let along = Math.min(Math.max(x, 0), left.length);
    if (grid) along = Math.min(Math.round(along / ALONG_GRID_M) * ALONG_GRID_M, left.length);
    const rail = z < 0 ? left : right;
    const [px, pz] = rotateY(along, 0, flow);
    const point: [number, number, number] = [rail.position[0] + px, rail.position[1], rail.position[2] + pz];
    const drop: RailDrop = { conveyor: c.id, rail: rail.name, along: Number(along.toFixed(4)), point, beside_m: Math.max(0, Math.abs(z) - half) };
    if (!best || Math.abs(z) < best.off) best = { drop, off: Math.abs(z) };
  }
  return best?.drop ?? null;
}

/** A laid-out device that rides a belt: its rail and its distance from the infeed end. */
export interface Follower {
  id: string;
  conveyor: string;
  rail: "rail_left" | "rail_right";
  along: number;
}

/**
 * The laid-out devices standing on `belt` before it moves (ruled 2026-11-20, ruling 2: a
 * laid-out device on a belt moves with its belt in the editor, as it does in `openfactory
 * layout`): each marked device of a type the drop rules place, found on the belt's rails as a
 * drop would find it (no grid: it keeps its exact distance). A device the person placed (no
 * mark) stays where it is, as before.
 */
export function followersOn(
  belt: { id: string; ports: (SnapPort | RailPort)[] },
  devices: { id: string; type: string; position: [number, number, number]; laid_out?: unknown; composite?: unknown }[],
): Follower[] {
  const out: Follower[] = [];
  for (const d of devices) {
    if (d.id === belt.id || !d.laid_out || d.composite || !MOUNT_TYPES.includes(d.type)) continue;
    const drop = findRail(d.position, [belt], false);
    if (drop) out.push({ id: d.id, conveyor: belt.id, rail: drop.rail, along: drop.along });
  }
  return out;
}
