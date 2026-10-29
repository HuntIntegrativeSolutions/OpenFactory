// Viewer physics (PLAN 1.12): a Rapier world for the plant, DOM-free so the viewer and the node
// parity test (physics.parity.test.ts) run the same code.
//
// - Belts: `kinematicVelocityBased` bodies (a cuboid whose top face is the belt surface). Before
//   every step each belt is teleported back to `home` with setTranslation and given its surface
//   velocity with setLinvel, so the solver drags parts by friction while the belt never drifts
//   more than v·dt (docs/research/libraries.md, "Rapier conveyor approach"; contact-based surface
//   velocity is Rust-only). The viewer draws belt meshes at home, never at the body.
// - Parts: dynamic boxes. Belts and parts use the scene's friction μ, so the default Average
//   combine rule gives μ, and parts accelerate at μ·g like core/kinematics.py. A roller conveyor
//   with its own `friction` μr uses combine rule Min (μr < μ) or Max (μr > μ), so the contact
//   friction is exactly μr, as in the headless model.
// - Pusher plates: `kinematicPositionBased`, driven with setNextKinematicTranslation to the
//   core's stroke. The plate is frictionless (combine rule Min) so it pushes along its axis only,
//   like the headless model.
// - Photo eyes: a thin sensor cuboid along the beam with collision events; a beam is blocked
//   while any part intersects it. step() returns the beam edges, which the viewer sends upstream.
// - Stopper blades (PLAN 2.5): `kinematicPositionBased` cuboids rising out of the belt to the core's
//   raised fraction, frictionless like the pusher plate. A part already over the blade line when
//   the blade starts to rise passes, as in the headless model: a contact-pair hook
//   (filterContactPair) ignores the blade's contacts with those parts until they leave its
//   footprint.
//
// - Diverter arms (PLAN 2.5): frictionless `kinematicPositionBased` cuboids turned about their
//   pivot to the core's angle (positive swing_deg toward local +z), like the pusher plate.
// - Turntable decks (PLAN 2.5): a belt body whose heading is the scene rotation plus the core's
//   turn angle, re-posed about the deck centre before every step.
// - Parts the core places (PLAN §3.3: held by a gripper, or on a turning deck) become
//   `kinematicPositionBased` and are driven to the core's position (follow); release() makes them
//   dynamic again with the core's velocity.
//
// Frames: geometry.py's convention (x along flow, y up, rotation_deg about +y) is three.js's.
import type * as RapierNs from "@dimforge/rapier3d-compat";
import { BELT_TYPES, type ComponentGeometry, type Vec3 } from "./api";

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

/** Belt edges are rounded by this radius so a part driven across a level seam between two belts
 * (side by side, as under a diverter arm) slides over it instead of catching on the edge. */
const BELT_EDGE_RADIUS = 0.005;

/** A belt's collider: top face at the belt surface, edges rounded by BELT_EDGE_RADIUS. */
function beltCollider(R: Rapier, length: number, width: number): RapierNs.ColliderDesc {
  const r = BELT_EDGE_RADIUS;
  return R.ColliderDesc.roundCuboid(length / 2 - r, BELT_HALF_THICKNESS - r, width / 2 - r, r);
}
const num = (g: ComponentGeometry, key: string, fallback: number) =>
  typeof g[key] === "number" ? (g[key] as number) : fallback;

interface Belt {
  body: RapierNs.RigidBody;
  home: Vec3;
  flow: Vec3;
  speed: number;
  /** Turntable deck: the centre it turns about, its scene rotation, and the core's turn angle. */
  deck?: { centre: Vec3; rotation: number; angle: number };
}

interface Plate {
  body: RapierNs.RigidBody;
  origin: Vec3;
  rotation: number;
}

interface Blade {
  body: RapierNs.RigidBody;
  collider: number;
  origin: Vec3;
  rotation: number;
  width: number;
  height: number;
  thickness: number;
  raised: number; // fraction 0..1 for the next step
  /** Part collider handles over the blade line when it started to rise: contacts ignored. */
  exempt: Set<number>;
}

export class PlantPhysics {
  readonly world: RapierNs.World;
  private readonly R: Rapier;
  private readonly friction: number;
  private readonly events: RapierNs.EventQueue;
  private readonly belts = new Map<string, Belt>();
  private readonly plates = new Map<string, Plate>();
  private readonly arms = new Map<string, { body: RapierNs.RigidBody; g: ComponentGeometry }>();
  private readonly beams = new Map<number, string>(); // sensor collider handle -> eye id
  private readonly inBeam = new Map<string, Set<number>>(); // eye id -> part collider handles
  /** Collider handles of parts: only these block a beam, as in the core's photo eye. */
  private readonly partHandles = new Set<number>();
  private readonly parts = new Map<string, RapierNs.RigidBody>();
  private readonly sizes = new Map<string, Vec3>();
  private readonly blades = new Map<string, Blade>();
  private readonly bladeColliders = new Map<number, Blade>();
  private readonly hooks: RapierNs.PhysicsHooks;
  private readonly reported = new Map<string, boolean>(); // eye id -> last state step() returned

  constructor(R: Rapier, scene: PhysicsScene) {
    this.R = R;
    this.friction = scene.friction;
    this.world = new R.World({ x: 0, y: -GRAVITY, z: 0 });
    this.world.timestep = scene.dt;
    this.events = new R.EventQueue(true);
    for (const g of scene.components) {
      if (BELT_TYPES.has(g.type)) this.addBelt(g);
      else if (g.type === "pusher") this.addPlate(g);
      else if (g.type === "photo_eye") this.addBeam(g);
      else if (g.type === "stopper") this.addBlade(g);
      else if (g.type === "turntable") this.addDeck(g);
      else if (g.type === "diverter_arm") this.addArm(g);
    }
    const blades = this.bladeColliders;
    this.hooks = {
      filterContactPair: (c1, c2) => {
        const blade = blades.get(c1) ?? blades.get(c2);
        const other = blades.has(c1) ? c2 : c1;
        return blade && blade.exempt.has(other) ? R.SolverFlags.EMPTY : R.SolverFlags.COMPUTE_IMPULSE;
      },
      filterIntersectionPair: () => true,
    };
  }

  private addArm(g: ComponentGeometry): void {
    const { R } = this;
    const length = num(g, "length", 0.8);
    const height = num(g, "height", 0.15);
    const clearance = num(g, "clearance", 0.005);
    const thickness = num(g, "thickness", 0.02);
    const body = this.world.createRigidBody(R.RigidBodyDesc.kinematicPositionBased());
    this.world.createCollider(
      R.ColliderDesc.cuboid(length / 2, (height - clearance) / 2, thickness / 2)
        .setFriction(0)
        .setFrictionCombineRule(R.CoefficientCombineRule.Min),
      body,
    );
    this.arms.set(g.id, { body, g });
    this.poseArm(g.id, 0, true);
  }

  private poseArm(id: string, angle: number, now = false): void {
    const arm = this.arms.get(id);
    if (!arm) return;
    const { body, g } = arm;
    const length = num(g, "length", 0.8);
    const height = num(g, "height", 0.15);
    const clearance = num(g, "clearance", 0.005);
    const t = (angle * Math.PI) / 180;
    const centre = toWorld(g.position, g.rotation_deg, [
      (Math.cos(t) * length) / 2, clearance + (height - clearance) / 2, (Math.sin(t) * length) / 2,
    ]);
    const rotation = quatY(g.rotation_deg - angle); // local +x turned toward +z by `angle`
    if (now) {
      body.setTranslation(vec(centre), true);
      body.setRotation(rotation, true);
    } else {
      body.setNextKinematicTranslation(vec(centre));
      body.setNextKinematicRotation(rotation);
    }
  }

  /** Diverter arm angle in degrees from rest (the core's `angle_deg` state), for the next step. */
  setDiverter(id: string, angle: number): void {
    this.poseArm(id, angle);
  }

  private addDeck(g: ComponentGeometry): void {
    const { R } = this;
    const length = num(g, "length", 1);
    const width = num(g, "width", 0.5);
    const home: Vec3 = [g.position[0], g.position[1] - BELT_HALF_THICKNESS, g.position[2]];
    const body = this.world.createRigidBody(
      R.RigidBodyDesc.kinematicVelocityBased().setTranslation(...home).setRotation(quatY(g.rotation_deg)),
    );
    this.world.createCollider(beltCollider(R, length, width).setFriction(this.friction).setRestitution(0), body);
    this.belts.set(g.id, {
      body, home, flow: rotateY([1, 0, 0], g.rotation_deg), speed: 0,
      deck: { centre: g.position, rotation: g.rotation_deg, angle: 0 },
    });
  }

  /** Turntable deck angle in degrees (the core's `angle_deg` state), for the next step. */
  setTurntable(id: string, angle: number): void {
    const deck = this.belts.get(id)?.deck;
    if (deck) deck.angle = angle;
  }

  /** Drive a part to the core's position for the next step (PLAN §3.3); it stops being simulated. */
  follow(id: string, position: Vec3): void {
    const body = this.parts.get(id);
    if (!body) return;
    if (!body.isKinematic()) body.setBodyType(this.R.RigidBodyType.KinematicPositionBased, true);
    body.setNextKinematicTranslation(vec(position));
    body.setNextKinematicRotation({ x: 0, y: 0, z: 0, w: 1 }); // the core keeps parts square to the world axes
  }

  /** Hand a followed part back to the simulation, moving at `velocity` (m/s). */
  release(id: string, velocity: Vec3 = [0, 0, 0]): void {
    const body = this.parts.get(id);
    if (!body || !body.isKinematic()) return;
    body.setBodyType(this.R.RigidBodyType.Dynamic, true);
    body.setLinvel(vec(velocity), true);
  }

  private addBlade(g: ComponentGeometry): void {
    const { R } = this;
    const width = num(g, "width", 0.5);
    const height = num(g, "height", 0.05);
    const thickness = num(g, "thickness", 0.02);
    const body = this.world.createRigidBody(
      R.RigidBodyDesc.kinematicPositionBased()
        .setTranslation(...toWorld(g.position, g.rotation_deg, [thickness / 2, -height / 2, 0]))
        .setRotation(quatY(g.rotation_deg)),
    );
    const collider = this.world.createCollider(
      R.ColliderDesc.cuboid(thickness / 2, height / 2, width / 2)
        .setFriction(0)
        .setFrictionCombineRule(R.CoefficientCombineRule.Min)
        .setActiveHooks(R.ActiveHooks.FILTER_CONTACT_PAIRS),
      body,
    );
    const blade: Blade = {
      body, collider: collider.handle, origin: g.position, rotation: g.rotation_deg,
      width, height, thickness, raised: 0, exempt: new Set(),
    };
    this.blades.set(g.id, blade);
    this.bladeColliders.set(collider.handle, blade);
  }

  private addBelt(g: ComponentGeometry): void {
    const { R } = this;
    const length = num(g, "length", 1);
    const width = num(g, "width", 0.5);
    const home = toWorld(g.position, g.rotation_deg, [length / 2, -BELT_HALF_THICKNESS, 0]);
    const body = this.world.createRigidBody(
      R.RigidBodyDesc.kinematicVelocityBased().setTranslation(...home).setRotation(quatY(g.rotation_deg)),
    );
    const own = typeof g.friction === "number" ? g.friction : this.friction;
    const collider = beltCollider(R, length, width).setFriction(own).setRestitution(0);
    if (own < this.friction) collider.setFrictionCombineRule(R.CoefficientCombineRule.Min);
    if (own > this.friction) collider.setFrictionCombineRule(R.CoefficientCombineRule.Max);
    this.world.createCollider(collider, body);
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
        .setActiveEvents(R.ActiveEvents.COLLISION_EVENTS)
        // Rapier's default leaves out kinematic-vs-fixed pairs, and a part the core places (held,
        // against an arm, on a turning deck, §3.3) is kinematic: without this it is invisible to
        // the beam (the pick eye cleared 8 ticks early the moment the gripper took the box).
        .setActiveCollisionTypes(R.ActiveCollisionTypes.DEFAULT | R.ActiveCollisionTypes.KINEMATIC_FIXED),
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

  /** Stopper blade raised fraction 0..1 (the core's `position` state), for the next step. */
  setStopper(id: string, raised: number): void {
    const blade = this.blades.get(id);
    if (!blade) return;
    if (blade.raised <= 0 && raised > 0) {
      for (const [partId, body] of this.parts) {
        if (this.overBlade(blade, body, this.sizes.get(partId)!)) blade.exempt.add(body.collider(0).handle);
      }
    }
    blade.raised = raised;
    // Fully lowered, the blade is flush with the belt surface, where a part's bottom edge would
    // catch its top corner; the headless model never blocks then, so neither does the collider.
    this.world.getCollider(blade.collider).setEnabled(raised > 0);
    const top = blade.height * raised;
    blade.body.setNextKinematicTranslation(
      vec(toWorld(blade.origin, blade.rotation, [blade.thickness / 2, top - blade.height / 2, 0])),
    );
  }

  /** True if a part's box overlaps the blade's footprint (its line across the belt). */
  private overBlade(blade: Blade, body: RapierNs.RigidBody, size: Vec3): boolean {
    const t = body.translation();
    const d: Vec3 = [t.x - blade.origin[0], 0, t.z - blade.origin[2]];
    const [lx, , lz] = rotateY(d, -blade.rotation);
    const r = (-blade.rotation * Math.PI) / 180;
    const hx = (Math.abs(Math.cos(r)) * size[0] + Math.abs(Math.sin(r)) * size[2]) / 2;
    const hz = (Math.abs(Math.sin(r)) * size[0] + Math.abs(Math.cos(r)) * size[2]) / 2;
    return lx + hx > 0 && lx - hx < blade.thickness && Math.abs(lz) < blade.width / 2 + hz;
  }

  /** Add a dynamic box; `velocity` (m/s) starts it moving as the core's part already is. */
  addPart(id: string, size: Vec3, position: Vec3, velocity: Vec3 = [0, 0, 0]): void {
    if (this.parts.has(id)) return;
    const { R } = this;
    const body = this.world.createRigidBody(
      R.RigidBodyDesc.dynamic().setTranslation(...position).setLinvel(...velocity),
    );
    const collider = this.world.createCollider(
      R.ColliderDesc.cuboid(size[0] / 2, size[1] / 2, size[2] / 2).setFriction(this.friction).setRestitution(0),
      body,
    );
    this.partHandles.add(collider.handle);
    this.parts.set(id, body);
    this.sizes.set(id, size);
  }

  removePart(id: string): void {
    const body = this.parts.get(id);
    if (!body) return;
    for (let i = 0; i < body.numColliders(); i++) {
      const handle = body.collider(i).handle;
      for (const handles of this.inBeam.values()) handles.delete(handle);
      this.partHandles.delete(handle);
    }
    for (const blade of this.blades.values()) blade.exempt.delete(body.collider(0).handle);
    this.world.removeRigidBody(body);
    this.parts.delete(id);
    this.sizes.delete(id);
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
      if (belt.deck) {
        const heading = belt.deck.rotation + belt.deck.angle;
        belt.flow = rotateY([1, 0, 0], heading);
        belt.body.setRotation(quatY(heading), true);
      }
      belt.body.setTranslation(vec(belt.home), true); // never let the belt drift
      belt.body.setLinvel(vec([belt.flow[0] * belt.speed, 0, belt.flow[2] * belt.speed]), true);
    }
    for (const blade of this.blades.values()) {
      for (const handle of blade.exempt) {
        const id = [...this.parts].find(([, b]) => b.collider(0).handle === handle)?.[0];
        if (id === undefined || !this.overBlade(blade, this.parts.get(id)!, this.sizes.get(id)!)) blade.exempt.delete(handle);
      }
    }
    this.world.step(this.events, this.hooks);
    this.events.drainCollisionEvents((h1, h2, started) => {
      const eye = this.beams.get(h1) ?? this.beams.get(h2);
      if (eye === undefined) return;
      const other = this.beams.has(h1) ? h2 : h1;
      // Beams see kinematic bodies too (core-placed parts, below), so ignore plates, blades, arms
      // and belts that cross one: only parts block a beam, as in the core's photo eye.
      if (!this.partHandles.has(other)) return;
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
