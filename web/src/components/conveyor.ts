// belt_conveyor and roller_conveyor meshes (PLAN §5). Origin: center of the infeed end at belt-top height; flow is
// local +x. The belt surface shows status: running green, stopped grey (ISA-101). Slats across
// the belt move with the core's travel_m, so a running belt visibly runs even with no parts.
// The mesh is always drawn at the scene position ("home"); viewer physics (PLAN 1.12) moves its
// own kinematic body, never this mesh.
import * as THREE from "three";
import type { ComponentGeometry } from "../api";
import { ISA101 } from "../isa101";
import { NEUTRAL, box, num, placed, setColor, zCylinder, type BuildContext, type Builder } from "./types";

const BELT_T = 0.02;
const RAIL_H = 0.08;
const SLAT_PITCH = 0.25;

export const beltConveyor: Builder = (g, ctx) => {
  const length = num(g, "length", 1);
  const width = num(g, "width", 0.5);
  const group = placed(g);

  const belt = box([length, BELT_T, width], ISA101.stopped, [length / 2, -BELT_T / 2, 0]);
  belt.name = "belt";
  group.add(belt);
  const frame = railsAndLegs(g, ctx, length, width);
  for (const x of [0, length]) frame.push(zCylinder(0.04, width, NEUTRAL.structure, [x, -0.045, 0]));
  group.add(...frame);

  // One instanced mesh for all of a belt's slats: one draw call instead of one per slat (PLAN 2.9).
  const count = Math.max(1, Math.floor(length / SLAT_PITCH));
  const slats = new THREE.InstancedMesh(
    new THREE.BoxGeometry(0.015, 0.003, width * 0.96),
    new THREE.MeshStandardMaterial({ color: NEUTRAL.slat, roughness: 0.7, metalness: 0.1 }),
    count,
  );
  slats.name = "slats";
  const slatAt = new THREE.Matrix4();
  const placeSlats = (phase: number) => {
    for (let i = 0; i < count; i++) {
      slats.setMatrixAt(i, slatAt.makeTranslation((i * SLAT_PITCH + phase) % (count * SLAT_PITCH), 0.0015, 0));
    }
    slats.instanceMatrix.needsUpdate = true;
    slats.computeBoundingSphere(); // three culls an instanced mesh by the instances' bounds
  };
  placeSlats(0);
  group.add(slats);

  const arrow = directionArrow(g, length, width);
  if (arrow) group.add(arrow.mesh);

  return {
    id: g.id,
    type: g.type,
    object: group,
    labelAnchor: new THREE.Vector3(0, 0.12, -width / 2),
    pickable: [],
    housing: frame,
    update(state) {
      arrow?.update(state);
      setColor(belt, state.running ? ISA101.running : ISA101.stopped);
      const travel = typeof state.travel_m === "number" ? state.travel_m : 0;
      placeSlats(((travel % SLAT_PITCH) + SLAT_PITCH) % SLAT_PITCH);
    },
  };
};

const ROLLER_R = 0.025;

/**
 * Direction arrow of a bidirectional conveyor (PLAN 2.5b): a flat arrowhead above the near side
 * rail at mid-length, pointing along the flow (+x) or, while the core reports `reverse`, back
 * toward the infeed end; green while running, grey when stopped. Null for a one-way conveyor.
 */
function directionArrow(g: ComponentGeometry, length: number, width: number) {
  if (g.bidirectional !== true) return null;
  const shape = new THREE.Shape();
  shape.moveTo(0.12, 0);
  shape.lineTo(-0.08, 0.07);
  shape.lineTo(-0.04, 0);
  shape.lineTo(-0.08, -0.07);
  shape.closePath();
  const arrow = new THREE.Mesh(
    new THREE.ShapeGeometry(shape),
    new THREE.MeshStandardMaterial({ color: ISA101.stopped, side: THREE.DoubleSide }),
  );
  arrow.name = "direction";
  arrow.rotation.x = -Math.PI / 2; // lie flat, the shape's +y toward -z
  arrow.position.set(length / 2, 0.03, width / 2 + 0.015);
  return {
    mesh: arrow,
    update(state: Record<string, unknown>) {
      arrow.rotation.z = state.reverse ? Math.PI : 0;
      setColor(arrow, state.running ? ISA101.running : ISA101.stopped);
    },
  };
}

/** Side rails and floor legs of a straight conveyor: its fixed frame (PLAN 2.15 housing). */
function railsAndLegs(g: ComponentGeometry, ctx: BuildContext, length: number, width: number): THREE.Mesh[] {
  const parts: THREE.Mesh[] = [];
  for (const side of [-1, 1]) {
    parts.push(box([length + 0.08, RAIL_H, 0.03], NEUTRAL.frame, [length / 2, -RAIL_H / 2 + 0.01, side * (width / 2 + 0.015)]));
  }
  const legHeight = g.position[1] - RAIL_H - ctx.floorY;
  if (legHeight > 0.01) {
    for (const x of [0.15, length - 0.15]) {
      for (const side of [-1, 1]) {
        parts.push(box([0.05, legHeight, 0.05], NEUTRAL.frame, [x, -RAIL_H - legHeight / 2, side * (width / 2 + 0.015)]));
      }
    }
  }
  return parts;
}

/**
 * roller_conveyor: same frame and status colors as the belt, drawn as rollers across the flow every
 * `roller_pitch` m. The rollers turn with the core's travel_m (angle = travel / radius) and show
 * status: running green, stopped grey.
 */
export const rollerConveyor: Builder = (g, ctx) => {
  const length = num(g, "length", 1);
  const width = num(g, "width", 0.5);
  const pitch = num(g, "roller_pitch", 0.1);
  const group = placed(g);
  const frame = railsAndLegs(g, ctx, length, width);
  group.add(...frame);
  const rollers: ReturnType<typeof zCylinder>[] = [];
  const count = Math.max(2, Math.floor(length / pitch) + 1);
  for (let i = 0; i < count; i++) {
    const x = Math.min(length, i * pitch);
    const roller = zCylinder(ROLLER_R, width, ISA101.stopped, [x, -ROLLER_R, 0]);
    // a dark stripe along each roller makes its rotation visible
    roller.add(box([0.006, width * 0.98, 0.006], NEUTRAL.slat, [ROLLER_R, 0, 0]));
    rollers.push(roller);
    group.add(roller);
  }
  const arrow = directionArrow(g, length, width);
  if (arrow) group.add(arrow.mesh);
  return {
    id: g.id,
    type: g.type,
    object: group,
    labelAnchor: new THREE.Vector3(0, 0.12, -width / 2),
    pickable: [],
    housing: frame,
    update(state) {
      arrow?.update(state);
      const color = state.running ? ISA101.running : ISA101.stopped;
      const travel = typeof state.travel_m === "number" ? state.travel_m : 0;
      for (const roller of rollers) {
        roller.material.color.setHex(color); // the roller only, not its stripe
        roller.rotation.y = -travel / ROLLER_R; // about the roller's own axis
      }
    },
  };
};

/**
 * turntable: origin at the deck centre (the pivot) at belt-top height. A round platform on a
 * pedestal carries the deck belt, which turns about +y with the core's angle_deg (0: flow along
 * local +x). The belt shows status like belt_conveyor; the platform rim is green while turning.
 */
export const turntable: Builder = (g, ctx) => {
  const length = num(g, "length", 1);
  const width = num(g, "width", 0.5);
  const group = placed(g);
  const radius = Math.hypot(length, width) / 2 + 0.03;
  const platform = new THREE.Mesh(
    new THREE.CylinderGeometry(radius, radius, 0.04, 40),
    new THREE.MeshStandardMaterial({ color: ISA101.stopped, roughness: 0.7 }),
  );
  platform.name = "platform";
  platform.position.y = -RAIL_H - 0.02;
  group.add(platform);
  const pedestal = g.position[1] - RAIL_H - 0.04 - ctx.floorY;
  const fixed: THREE.Mesh[] = [];
  if (pedestal > 0.01) fixed.push(box([0.2, pedestal, 0.2], NEUTRAL.frame, [0, -RAIL_H - 0.04 - pedestal / 2, 0]));
  if (fixed.length) group.add(...fixed); // add() with no argument logs an error
  const deck = new THREE.Group();
  deck.name = "deck";
  group.add(deck);
  const belt = box([length, BELT_T, width], ISA101.stopped, [0, -BELT_T / 2, 0]);
  belt.name = "belt";
  deck.add(belt);
  for (const side of [-1, 1]) {
    deck.add(box([length, RAIL_H, 0.03], NEUTRAL.frame, [0, -RAIL_H / 2 + 0.01, side * (width / 2 + 0.015)]));
  }
  const slats: THREE.Mesh[] = [];
  const count = Math.max(1, Math.floor(length / SLAT_PITCH));
  for (let i = 0; i < count; i++) {
    const slat = box([0.015, 0.003, width * 0.96], NEUTRAL.slat, [i * SLAT_PITCH - length / 2, 0.0015, 0]);
    slats.push(slat);
    deck.add(slat);
  }
  return {
    id: g.id,
    type: g.type,
    object: group,
    labelAnchor: new THREE.Vector3(0, 0.15, -radius),
    pickable: [],
    housing: fixed,
    update(state) {
      deck.rotation.y = THREE.MathUtils.degToRad(typeof state.angle_deg === "number" ? state.angle_deg : 0);
      setColor(belt, state.running ? ISA101.running : ISA101.stopped);
      setColor(platform, state.turning ? ISA101.running : ISA101.stopped);
      const travel = typeof state.travel_m === "number" ? state.travel_m : 0;
      const phase = ((travel % SLAT_PITCH) + SLAT_PITCH) % SLAT_PITCH;
      slats.forEach((slat, i) => {
        slat.position.x = ((i * SLAT_PITCH + phase) % (count * SLAT_PITCH)) - length / 2;
      });
    },
  };
};

/**
 * lift: origin at the centre of the deck's infeed end, at deck-belt top height with the deck at the
 * bottom (PLAN 2.5b). Four guide posts stand from the floor past the top; the deck (belt, rails,
 * slats) rises with the core's height_m. The belt shows status like belt_conveyor; the posts are
 * green while the deck moves.
 */
export const lift: Builder = (g, ctx) => {
  const length = num(g, "length", 1);
  const width = num(g, "width", 0.5);
  const rise = num(g, "lift_height", 0.5);
  const group = placed(g);
  const postTop = rise + 0.15;
  const postBottom = ctx.floorY - g.position[1];
  const posts: THREE.Mesh[] = [];
  for (const x of [0.05, length - 0.05]) {
    for (const side of [-1, 1]) {
      const h = postTop - postBottom;
      const post = box([0.05, h, 0.05], NEUTRAL.frame, [x, postBottom + h / 2, side * (width / 2 + 0.06)]);
      post.name = "post";
      posts.push(post);
    }
  }
  group.add(...posts);
  const deck = new THREE.Group();
  deck.name = "deck";
  group.add(deck);
  const belt = box([length, BELT_T, width], ISA101.stopped, [length / 2, -BELT_T / 2, 0]);
  belt.name = "belt";
  deck.add(belt);
  for (const side of [-1, 1]) {
    deck.add(box([length, RAIL_H, 0.03], NEUTRAL.frame, [length / 2, -RAIL_H / 2 + 0.01, side * (width / 2 + 0.015)]));
  }
  const slats: THREE.Mesh[] = [];
  const count = Math.max(1, Math.floor(length / SLAT_PITCH));
  for (let i = 0; i < count; i++) {
    const slat = box([0.015, 0.003, width * 0.96], NEUTRAL.slat, [i * SLAT_PITCH, 0.0015, 0]);
    slats.push(slat);
    deck.add(slat);
  }
  return {
    id: g.id,
    type: g.type,
    object: group,
    labelAnchor: new THREE.Vector3(0, postTop, -width / 2),
    pickable: [],
    housing: posts,
    update(state) {
      deck.position.y = typeof state.height_m === "number" ? state.height_m : 0;
      setColor(belt, state.running ? ISA101.running : ISA101.stopped);
      for (const post of posts) setColor(post, state.moving ? ISA101.running : NEUTRAL.frame);
      const travel = typeof state.travel_m === "number" ? state.travel_m : 0;
      const phase = ((travel % SLAT_PITCH) + SLAT_PITCH) % SLAT_PITCH;
      slats.forEach((slat, i) => {
        slat.position.x = (i * SLAT_PITCH + phase) % (count * SLAT_PITCH);
      });
    },
  };
};
