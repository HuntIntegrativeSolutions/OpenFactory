// belt_conveyor and roller_conveyor meshes (PLAN §5). Origin: center of the infeed end at belt-top height; flow is
// local +x. The belt surface shows status: running green, stopped grey (ISA-101). Slats across
// the belt move with the core's travel_m, so a running belt visibly runs even with no parts.
// The mesh is always drawn at the scene position ("home"); viewer physics (PLAN 1.12) moves its
// own kinematic body, never this mesh.
import * as THREE from "three";
import { ISA101 } from "../isa101";
import { NEUTRAL, box, num, placed, setColor, zCylinder, type Builder } from "./types";

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
  for (const side of [-1, 1]) {
    group.add(box([length + 0.08, RAIL_H, 0.03], NEUTRAL.frame, [length / 2, -RAIL_H / 2 + 0.01, side * (width / 2 + 0.015)]));
  }
  for (const x of [0, length]) group.add(zCylinder(0.04, width, NEUTRAL.structure, [x, -0.045, 0]));

  const legHeight = g.position[1] - RAIL_H - ctx.floorY;
  if (legHeight > 0.01) {
    for (const x of [0.15, length - 0.15]) {
      for (const side of [-1, 1]) {
        group.add(box([0.05, legHeight, 0.05], NEUTRAL.frame, [x, -RAIL_H - legHeight / 2, side * (width / 2 + 0.015)]));
      }
    }
  }

  const slats: THREE.Mesh[] = [];
  const count = Math.max(1, Math.floor(length / SLAT_PITCH));
  for (let i = 0; i < count; i++) {
    const slat = box([0.015, 0.003, width * 0.96], NEUTRAL.slat, [i * SLAT_PITCH, 0.0015, 0]);
    slats.push(slat);
    group.add(slat);
  }

  return {
    id: g.id,
    type: g.type,
    object: group,
    labelAnchor: new THREE.Vector3(0, 0.12, -width / 2),
    pickable: [],
    update(state) {
      setColor(belt, state.running ? ISA101.running : ISA101.stopped);
      const travel = typeof state.travel_m === "number" ? state.travel_m : 0;
      const phase = ((travel % SLAT_PITCH) + SLAT_PITCH) % SLAT_PITCH;
      slats.forEach((slat, i) => {
        slat.position.x = (i * SLAT_PITCH + phase) % (count * SLAT_PITCH);
      });
    },
  };
};

const ROLLER_R = 0.025;

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
  for (const side of [-1, 1]) {
    group.add(box([length + 0.08, RAIL_H, 0.03], NEUTRAL.frame, [length / 2, -RAIL_H / 2 + 0.01, side * (width / 2 + 0.015)]));
  }
  const legHeight = g.position[1] - RAIL_H - ctx.floorY;
  if (legHeight > 0.01) {
    for (const x of [0.15, length - 0.15]) {
      for (const side of [-1, 1]) {
        group.add(box([0.05, legHeight, 0.05], NEUTRAL.frame, [x, -RAIL_H - legHeight / 2, side * (width / 2 + 0.015)]));
      }
    }
  }
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
  return {
    id: g.id,
    type: g.type,
    object: group,
    labelAnchor: new THREE.Vector3(0, 0.12, -width / 2),
    pickable: [],
    update(state) {
      const color = state.running ? ISA101.running : ISA101.stopped;
      const travel = typeof state.travel_m === "number" ? state.travel_m : 0;
      for (const roller of rollers) {
        roller.material.color.setHex(color); // the roller only, not its stripe
        roller.rotation.y = -travel / ROLLER_R; // about the roller's own axis
      }
    },
  };
};
