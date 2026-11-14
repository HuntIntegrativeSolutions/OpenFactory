// Where a new component lands (PLAN 4.1e). The palette's Add drops a component at the floor point
// at the centre of the view; once it is drawn, the viewer (main.ts) measures its footprint and
// every other component's, and this module decides where it goes:
//
// - a conveyor whose end comes within DROP_SNAP_M of an open end of another conveyor (an end no
//   other conveyor's end meets) is placed so the two ends meet and face, at that end's height,
//   with the connection added: the 4.1d snap, with a wider reach because Add has no drag;
// - anything else, or a conveyor whose snapped place would overlap the plant, moves to the
//   nearest spot on the floor where its footprint, with MARGIN_M around it, overlaps nothing and
//   stays on the floor; a drop that is already clear stays where it is.
//
// Footprints are axis-aligned rectangles in the floor plane (x, z) of what the viewer drew. Pure
// maths, no DOM: unit-tested in place.test.ts.
import { localPort, normalize, rotateY, type SnapPort } from "./snap";

export interface Rect {
  minX: number;
  minZ: number;
  maxX: number;
  maxZ: number;
}

/** Clearance kept around a new component's footprint. */
export const MARGIN_M = 0.05;
/** Spacing of the spots searched (a multiple of the editor's 0.05 m move grid). */
export const STEP_M = 0.1;
/** How far from the drop point a free spot is searched for. */
export const SEARCH_M = 15;
/** Half the floor's side: the lint's W204 warns past 20 m. */
export const FLOOR_HALF_M = 20;
/** A dropped conveyor snaps to an open end within this distance of one of its own ends. */
export const DROP_SNAP_M = 1.0;
/** Two ends closer than this (as the lint's W212) already meet: neither is open. */
export const MEET_M = 0.02;

export function overlaps(a: Rect, b: Rect, margin = 0): boolean {
  return a.minX < b.maxX + margin && b.minX < a.maxX + margin && a.minZ < b.maxZ + margin && b.minZ < a.maxZ + margin;
}

export function shifted(r: Rect, dx: number, dz: number): Rect {
  return { minX: r.minX + dx, minZ: r.minZ + dz, maxX: r.maxX + dx, maxZ: r.maxZ + dz };
}

function onFloor(r: Rect): boolean {
  return r.minX >= -FLOOR_HALF_M && r.maxX <= FLOOR_HALF_M && r.minZ >= -FLOOR_HALF_M && r.maxZ <= FLOOR_HALF_M;
}

let offsets: [number, number][] | null = null;

/** Every grid offset within SEARCH_M, nearest first (ties in a fixed order). */
function searchOffsets(): [number, number][] {
  if (offsets) return offsets;
  const n = Math.round(SEARCH_M / STEP_M);
  const out: [number, number, number][] = [];
  for (let i = -n; i <= n; i++) {
    for (let j = -n; j <= n; j++) {
      if (i * i + j * j <= n * n) out.push([i, j, i * i + j * j]);
    }
  }
  out.sort((a, b) => a[2] - b[2] || a[0] - b[0] || a[1] - b[1]);
  offsets = out.map(([i, j]) => [Number((i * STEP_M).toFixed(3)), Number((j * STEP_M).toFixed(3))]);
  return offsets;
}

/** The nearest offset (dx, dz) that clears `mine` of every obstacle, [0, 0] when it is already
 * clear, or null when no spot within SEARCH_M is. */
export function freeOffset(mine: Rect, obstacles: Rect[], margin = MARGIN_M): [number, number] | null {
  for (const [dx, dz] of searchOffsets()) {
    const r = shifted(mine, dx, dz);
    if (onFloor(r) && !obstacles.some((o) => overlaps(r, o, margin))) return [dx, dz];
  }
  return null;
}

/** The footprint `r` (drawn at `from`, turned `fromDeg`) moved to `to`, turned `toDeg`: the
 * rectangle around its corners turned about the component's own origin. */
export function moved(r: Rect, from: { position: number[]; rotation_deg: number }, to: { position: number[]; rotation_deg: number }): Rect {
  const turn = to.rotation_deg - from.rotation_deg;
  const corners: [number, number][] = [
    [r.minX, r.minZ], [r.minX, r.maxZ], [r.maxX, r.minZ], [r.maxX, r.maxZ],
  ];
  const xs: number[] = [];
  const zs: number[] = [];
  for (const [x, z] of corners) {
    const [lx, lz] = rotateY(x - from.position[0], z - from.position[2], turn);
    xs.push(to.position[0] + lx);
    zs.push(to.position[2] + lz);
  }
  return { minX: Math.min(...xs), minZ: Math.min(...zs), maxX: Math.max(...xs), maxZ: Math.max(...zs) };
}

export interface Conveyor {
  id: string;
  ports: SnapPort[];
}

/** The ends of `conveyors` no other conveyor's end meets (within MEET_M, level within MEET_M). */
export function openEnds(conveyors: Conveyor[]): Conveyor[] {
  const all = conveyors.flatMap((c) => c.ports.map((p) => ({ id: c.id, p })));
  const meets = (id: string, p: SnapPort) =>
    all.some((o) => o.id !== id && Math.abs(o.p.position[1] - p.position[1]) <= MEET_M &&
      Math.hypot(o.p.position[0] - p.position[0], o.p.position[2] - p.position[2]) <= MEET_M);
  return conveyors.map((c) => ({ id: c.id, ports: c.ports.filter((p) => !meets(c.id, p)) }));
}

export interface NewItem {
  id: string;
  /** Its position and turn in the file, as dropped. */
  position: [number, number, number];
  rotation_deg: number;
  /** Its drawn footprint at that pose. */
  rect: Rect;
  /** Its `load` ports as published at that pose (a conveyor), else empty. */
  ports: SnapPort[];
}

export interface Placement {
  position: [number, number, number];
  rotation_deg: number;
  /** The connection a snap adds, or null. */
  connection: { from: string; to: string } | null;
  /** "CONV_02.infeed -> CONV_01.discharge" after a snap, else null. */
  snapped: string | null;
  /** How far it moved from the drop point in the floor plane, m. */
  moved_m: number;
}

const joins = (a: SnapPort["role"], b: SnapPort["role"]) => (a === "out" || a === "both") && (b === "in" || b === "both");

/** The snap a dropped conveyor makes to the nearest open end within DROP_SNAP_M, or null. */
export function dropSnap(item: NewItem, conveyors: Conveyor[]): (Placement & { joins: string }) | null {
  const at = { position: item.position, rotation_deg: item.rotation_deg };
  let best: (Placement & { joins: string }) | null = null;
  let bestD = Infinity;
  for (const other of openEnds(conveyors)) {
    if (other.id === item.id) continue;
    for (const theirs of other.ports) {
      for (const mine of item.ports) {
        const forward = joins(mine.role, theirs.role);
        const backward = joins(theirs.role, mine.role);
        if (!forward && !backward) continue;
        const d = Math.hypot(mine.position[0] - theirs.position[0], mine.position[2] - theirs.position[2]);
        if (d > DROP_SNAP_M || d >= bestD) continue;
        const local = localPort(mine, at);
        const rotation = normalize(theirs.heading_deg + 180 - local.heading);
        const [ox, oz] = rotateY(local.x, local.z, rotation);
        const position: [number, number, number] = [
          Number((theirs.position[0] - ox).toFixed(4)),
          Number((theirs.position[1] - local.y).toFixed(4)),
          Number((theirs.position[2] - oz).toFixed(4)),
        ];
        const fwd = mine.role !== "in" && theirs.role !== "out";
        bestD = d;
        best = {
          position,
          rotation_deg: Number(rotation.toFixed(4)),
          connection: fwd ? { from: item.id, to: other.id } : { from: other.id, to: item.id },
          snapped: `${item.id}.${mine.name} -> ${other.id}.${theirs.name}`,
          moved_m: Math.hypot(position[0] - item.position[0], position[2] - item.position[2]),
          joins: other.id,
        };
      }
    }
  }
  return best;
}

/**
 * Where a new component goes (see the header), or null to leave it where it was dropped.
 * `obstacles` are the footprints of everything else drawn, with the id each belongs to.
 */
export function placeNew(item: NewItem, obstacles: { id: string; rect: Rect }[], conveyors: Conveyor[]): Placement | null {
  const snap = item.ports.length ? dropSnap(item, conveyors) : null;
  if (snap) {
    const rect = moved(item.rect, item, snap);
    // the conveyor it joins touches it end to end; anything else in the way and it is not snapped
    const blocked = obstacles.some((o) => o.id !== snap.joins && overlaps(rect, o.rect, -1e-3));
    if (!blocked && onFloor(rect)) {
      const { joins: _joined, ...placement } = snap;
      return placement;
    }
  }
  const offset = freeOffset(item.rect, obstacles.map((o) => o.rect));
  if (!offset || (offset[0] === 0 && offset[1] === 0)) return null;
  const position: [number, number, number] = [
    Number((item.position[0] + offset[0]).toFixed(3)),
    item.position[1],
    Number((item.position[2] + offset[1]).toFixed(3)),
  ];
  return { position, rotation_deg: item.rotation_deg, connection: null, snapped: null, moved_m: Math.hypot(offset[0], offset[1]) };
}
