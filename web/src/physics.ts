// Viewer physics (PLAN 1.12): a Rapier world for the plant, DOM-free so the viewer and the node
// parity test (physics.parity.test.ts) run the same code.
//
// - Belts: `kinematicVelocityBased` bodies (a cuboid whose top face is the belt surface). Before
//   every step each belt is teleported back to `home` with setTranslation and given its surface
//   velocity with setLinvel, so the solver drags parts by friction while the belt never drifts
//   more than v·dt (docs/research/libraries.md, "Rapier conveyor approach"; contact-based surface
//   velocity is Rust-only). The viewer draws belt meshes at home, never at the body.
// - Parts: dynamic boxes. Belts and parts use the scene's friction μ, so the default Average
//   combine rule gives μ, and parts accelerate at μ·g like core/kinematics.py.
// - Pusher plates: `kinematicPositionBased`, driven with setNextKinematicTranslation to the
//   core's stroke. The plate is frictionless (combine rule Min) so it pushes along its axis only,
//   like the headless model.
// - Photo eyes: a thin sensor cuboid along the beam with collision events; a beam is blocked
//   while any part intersects it. step() returns the beam edges, which the viewer sends upstream.
//
// Frames: geometry.py's convention (x along flow, y up, rotation_deg about +y) is three.js's.
import type * as RapierNs from "@dimforge/rapier3d-compat";
import type { ComponentGeometry, Vec3 } from "./api";

export type Rapier = typeof RapierNs;

export const GRAVITY = 9.81;
const BELT_HALF_THICKNESS = 0.05;
const BEAM_HALF_THICKNESS = 0.001;
const PLATE_HALF_DEPTH = 0.01;

export interface PhysicsScene {
  components: ComponentGeometry[];
  friction: number;
  dt: number;
}

export interface SensorEdge {
  id: string;
  blocked: boolean;
}

/** Rotate a local vector about +y (same as geometry.rotate_y and three.js rotation.y). */
export function rotateY(v: Vec3, degrees: number): Vec3 {
  const r = (degrees * Math.PI) / 180;
  const c = Math.cos(r);
  const s = Math.sin(r);
  return [v[0] * c + v[2] * s, v[1], -v[0] * s + v[2] * c];
}

export function toWorld(origin: Vec3, degrees: number, local: Vec3): Vec3 {
  const w = rotateY(local, degrees);
  return [origin[0] + w[0], origin[1] + w[1], origin[2] + w[2]];
}

function quatY(degrees: number) {
  const half = (degrees * Math.PI) / 360;
  return { x: 0, y: Math.sin(half), z: 0, w: Math.cos(half) };
}

const vec = (v: Vec3) => ({ x: v[0], y: v[1], z: v[2] });
const num = (g: ComponentGeometry, key: string, fallback: number) =>
  typeof g[key] === "number" ? (g[key] as number) : fallback;

interface Belt {
  body: RapierNs.RigidBody;
  home: Vec3;
  flow: Vec3;
  speed: number;
}

interface Plate {
  body: RapierNs.RigidBody;
  origin: Vec3;
  rotation: number;
}

export class PlantPhysics {
  readonly world: RapierNs.World;
  private readonly R: Rapier;
  private readonly friction: number;
  private readonly events: RapierNs.EventQueue;
  private readonly belts = new Map<string, Belt>();
  private readonly plates = new Map<string, Plate>();
  private readonly beams = new Map<number, string>(); // sensor collider handle -> eye id
  private readonly inBeam = new Map<string, Set<number>>(); // eye id -> part collider handles
  private readonly parts = new Map<string, RapierNs.RigidBody>();
  private readonly reported = new Map<string, boolean>(); // eye id -> last state step() returned

  constructor(R: Rapier, scene: PhysicsScene) {
    this.R = R;
    this.friction = scene.friction;
    this.world = new R.World({ x: 0, y: -GRAVITY, z: 0 });
    this.world.timestep = scene.dt;
    this.events = new R.EventQueue(true);
    for (const g of scene.components) {
      if (g.type === "belt_conveyor") this.addBelt(g);
      else if (g.type === "pusher") this.addPlate(g);
      else if (g.type === "photo_eye") this.addBeam(g);
    }
  }

  private addBelt(g: ComponentGeometry): void {
    const { R } = this;
    const length = num(g, "length", 1);
    const width = num(g, "width", 0.5);
    const home = toWorld(g.position, g.rotation_deg, [length / 2, -BELT_HALF_THICKNESS, 0]);
    const body = this.world.createRigidBody(
      R.RigidBodyDesc.kinematicVelocityBased().setTranslation(...home).setRotation(quatY(g.rotation_deg)),
    );
    this.world.createCollider(
      R.ColliderDesc.cuboid(length / 2, BELT_HALF_THICKNESS, width / 2).setFriction(this.friction).setRestitution(0),
      body,
    );
    this.belts.set(g.id, { body, home, flow: rotateY([1, 0, 0], g.rotation_deg), speed: 0 });
  }

  private addPlate(g: ComponentGeometry): void {
    const { R } = this;
    const body = this.world.createRigidBody(
      R.RigidBodyDesc.kinematicPositionBased()
        .setTranslation(...toWorld(g.position, g.rotation_deg, [0, 0, -PLATE_HALF_DEPTH]))
        .setRotation(quatY(g.rotation_deg)),
    );
    this.world.createCollider(
      R.ColliderDesc.cuboid(num(g, "plate_width", 0.3) / 2, num(g, "plate_height", 0.15) / 2, PLATE_HALF_DEPTH)
        .setFriction(0)
        .setFrictionCombineRule(R.CoefficientCombineRule.Min),
      body,
    );
    this.plates.set(g.id, { body, origin: g.position, rotation: g.rotation_deg });
  }

  private addBeam(g: ComponentGeometry): void {
    const { R } = this;
    const body = this.world.createRigidBody(
      R.RigidBodyDesc.fixed().setTranslation(...g.position).setRotation(quatY(g.rotation_deg)),
    );
    const collider = this.world.createCollider(
      R.ColliderDesc.cuboid(BEAM_HALF_THICKNESS, BEAM_HALF_THICKNESS, num(g, "beam_length", 0.5) / 2)
        .setSensor(true)
        .setActiveEvents(R.ActiveEvents.COLLISION_EVENTS),
      body,
    );
    this.beams.set(collider.handle, g.id);
    this.inBeam.set(g.id, new Set());
    this.reported.set(g.id, false);
  }

  /** Belt surface speed in m/s along its flow (the core's `speed_mps` state). */
  setBelt(id: string, speed: number): void {
    const belt = this.belts.get(id);
    if (belt) belt.speed = speed;
  }

  /** Pusher plate travel in m from retracted (the core's `position_m` state), for the next step. */
  setPusher(id: string, stroke: number): void {
    const plate = this.plates.get(id);
    if (plate) {
      plate.body.setNextKinematicTranslation(vec(toWorld(plate.origin, plate.rotation, [0, 0, stroke - PLATE_HALF_DEPTH])));
    }
  }

  /** Add a dynamic box; `velocity` (m/s) starts it moving as the core's part already is. */
  addPart(id: string, size: Vec3, position: Vec3, velocity: Vec3 = [0, 0, 0]): void {
    if (this.parts.has(id)) return;
    const { R } = this;
    const body = this.world.createRigidBody(
      R.RigidBodyDesc.dynamic().setTranslation(...position).setLinvel(...velocity),
    );
    this.world.createCollider(
      R.ColliderDesc.cuboid(size[0] / 2, size[1] / 2, size[2] / 2).setFriction(this.friction).setRestitution(0),
      body,
    );
    this.parts.set(id, body);
  }

  removePart(id: string): void {
    const body = this.parts.get(id);
    if (!body) return;
    for (let i = 0; i < body.numColliders(); i++) {
      const handle = body.collider(i).handle;
      for (const handles of this.inBeam.values()) handles.delete(handle);
    }
    this.world.removeRigidBody(body);
    this.parts.delete(id);
  }

  hasPart(id: string): boolean {
    return this.parts.has(id);
  }

  partIds(): string[] {
    return [...this.parts.keys()];
  }

  part(id: string): { position: Vec3; rotation: [number, number, number, number] } | null {
    const body = this.parts.get(id);
    if (!body) return null;
    const t = body.translation();
    const q = body.rotation();
    return { position: [t.x, t.y, t.z], rotation: [q.x, q.y, q.z, q.w] };
  }

  blocked(eyeId: string): boolean {
    return (this.inBeam.get(eyeId)?.size ?? 0) > 0;
  }

  /**
   * One fixed step. Returns the photo-eye beams whose blocked state changed since the last step,
   * including changes from parts removed between steps.
   */
  step(): SensorEdge[] {
    for (const belt of this.belts.values()) {
      belt.body.setTranslation(vec(belt.home), true); // never let the belt drift
      belt.body.setLinvel(vec([belt.flow[0] * belt.speed, 0, belt.flow[2] * belt.speed]), true);
    }
    this.world.step(this.events);
    this.events.drainCollisionEvents((h1, h2, started) => {
      const eye = this.beams.get(h1) ?? this.beams.get(h2);
      if (eye === undefined) return;
      const other = this.beams.has(h1) ? h2 : h1;
      const set = this.inBeam.get(eye)!;
      if (started) set.add(other);
      else set.delete(other);
    });
    const edges: SensorEdge[] = [];
    for (const [id, was] of this.reported) {
      const now = this.blocked(id);
      if (now !== was) {
        edges.push({ id, blocked: now });
        this.reported.set(id, now);
      }
    }
    return edges;
  }

  free(): void {
    this.events.free();
    this.world.free();
  }
}

/** Load Rapier's WASM (about 4 MB, so the viewer imports this lazily). */
export async function loadRapier(): Promise<Rapier> {
  const R = await import("@dimforge/rapier3d-compat");
  await R.init();
  return R;
}
