// Mesh builders for photo_eye, pusher, box_emitter, part_remover, and the operator devices
// (pushbutton, pilot_light, selector_switch). Origins follow PLAN §5 "behavior as built".
// Status colors are ISA-101: green while active (beam blocked, plate extending, emitter on),
// grey otherwise.
import * as THREE from "three";
import type { ComponentGeometry } from "../api";
import { ISA101 } from "../isa101";
import { NEUTRAL, box, num, placed, setColor, wireBox, zCylinder, type Builder } from "./types";

/** Photo eye: origin at the beam midpoint; the beam spans local z. */
export const photoEye: Builder = (g) => {
  const beamLength = num(g, "beam_length", 0.5);
  const group = placed(g);
  for (const side of [-1, 1]) {
    group.add(box([0.05, 0.09, 0.04], NEUTRAL.frame, [0, 0, side * (beamLength / 2 + 0.02)]));
  }
  const beam = zCylinder(0.006, beamLength, ISA101.stopped);
  beam.name = "beam";
  group.add(beam);
  return {
    id: g.id,
    type: g.type,
    object: group,
    labelAnchor: new THREE.Vector3(0, 0.1, -beamLength / 2),
    pickable: [],
    update(state) {
      setColor(beam, state.detect ? ISA101.running : ISA101.stopped);
      beam.scale.set(state.detect ? 2 : 1, 1, state.detect ? 2 : 1);
    },
  };
};

/** Pusher: origin at the center of the retracted plate face; the plate extends along local +z. */
export const pusher: Builder = (g) => {
  const stroke = num(g, "stroke", 0.5);
  const plateW = num(g, "plate_width", 0.3);
  const plateH = num(g, "plate_height", 0.15);
  const group = placed(g);
  const bodyLength = stroke + 0.1;
  group.add(zCylinder(0.045, bodyLength, NEUTRAL.structure, [0, 0, -0.06 - bodyLength / 2]));
  const rod = zCylinder(0.012, 1, NEUTRAL.structure);
  group.add(rod);
  const plate = box([plateW, plateH, 0.02], ISA101.stopped, [0, 0, -0.01]);
  plate.name = "plate";
  group.add(plate);
  const setStroke = (z: number) => {
    plate.position.z = z - 0.01;
    rod.scale.y = z + 0.06; // cylinder height runs along local y before the x rotation
    rod.position.z = (z - 0.06) / 2;
  };
  setStroke(0);
  return {
    id: g.id,
    type: g.type,
    object: group,
    labelAnchor: new THREE.Vector3(0, plateH / 2 + 0.08, -0.1),
    pickable: [],
    update(state) {
      setStroke(typeof state.position_m === "number" ? state.position_m : 0);
      setColor(plate, state.extending ? ISA101.running : ISA101.stopped);
    },
  };
};

/** Box emitter: origin at the spawn point; drawn as an open hopper outline around it. */
export const boxEmitter: Builder = (g) => {
  const parts = (g.parts as { size: number[] }[] | undefined) ?? [];
  const max = [0, 1, 2].map((i) => Math.max(0.2, ...parts.map((p) => p.size[i] ?? 0)) * 1.15);
  const group = placed(g);
  const hopper = wireBox([max[0], max[1], max[2]], ISA101.stopped, 0.06);
  group.add(hopper);
  return {
    id: g.id,
    type: g.type,
    object: group,
    labelAnchor: new THREE.Vector3(0, max[1] / 2 + 0.05, 0),
    pickable: [],
    update(state) {
      setColor(hopper, state.enabled ? ISA101.running : ISA101.stopped);
    },
  };
};

/** Part remover: origin at the center of its volume; a translucent grey box. */
export const partRemover: Builder = (g) => {
  const size = (g.size as number[] | undefined) ?? [0.3, 0.3, 0.3];
  const group = placed(g);
  group.add(wireBox([size[0], size[1], size[2]], ISA101.stopped, 0.1));
  return {
    id: g.id,
    type: g.type,
    object: group,
    labelAnchor: new THREE.Vector3(0, size[1] / 2 + 0.05, 0),
    pickable: [],
    update() {},
  };
};

const LAMP: Record<string, number> = {
  green: ISA101.running,
  red: ISA101.alarmHigh,
  amber: ISA101.alarmMedium,
  white: ISA101.normalText,
  blue: ISA101.highlight,
  black: 0x111827,
};

function lens(color: number): THREE.Mesh<THREE.CylinderGeometry, THREE.MeshStandardMaterial> {
  const mesh = zCylinder(0.03, 0.025, color);
  mesh.material.emissive.setHex(color);
  mesh.material.emissiveIntensity = 0;
  return mesh;
}

/** Pushbutton: origin at the button face center, facing local +z. Click to press. */
export const pushbutton: Builder = (g) => {
  const group = placed(g);
  group.add(box([0.08, 0.08, 0.02], NEUTRAL.frame, [0, 0, -0.03]));
  const cap = lens(LAMP[String(g.color ?? "green")] ?? ISA101.running);
  cap.position.z = -0.0125;
  group.add(cap);
  return {
    id: g.id,
    type: g.type,
    object: group,
    labelAnchor: new THREE.Vector3(0, 0.07, 0),
    pickable: [cap],
    update(state) {
      cap.material.emissiveIntensity = state.pressed ? 0.8 : 0;
      cap.position.z = state.pressed ? -0.02 : -0.0125;
    },
  };
};

/** Pilot light: origin at the lens center; lit while `on`. */
export const pilotLight: Builder = (g) => {
  const group = placed(g);
  group.add(box([0.08, 0.08, 0.02], NEUTRAL.frame, [0, 0, -0.03]));
  const lamp = lens(LAMP[String(g.color ?? "green")] ?? ISA101.running);
  lamp.position.z = -0.0125;
  group.add(lamp);
  return {
    id: g.id,
    type: g.type,
    object: group,
    labelAnchor: new THREE.Vector3(0, 0.07, 0),
    pickable: [],
    update(state) {
      lamp.material.emissiveIntensity = state.lit ? 1.2 : 0;
    },
  };
};

const SELECTOR_ANGLE: Record<string, number> = { a: Math.PI / 4, center: 0, b: -Math.PI / 4 };

/** Selector switch: origin at the switch face center; the handle turns a / center / b. */
export const selectorSwitch: Builder = (g) => {
  const group = placed(g);
  group.add(box([0.08, 0.08, 0.02], NEUTRAL.frame, [0, 0, -0.03]));
  group.add(zCylinder(0.03, 0.02, NEUTRAL.structure, [0, 0, -0.01]));
  const handle = box([0.012, 0.05, 0.02], ISA101.normalText, [0, 0, 0.005]);
  group.add(handle);
  return {
    id: g.id,
    type: g.type,
    object: group,
    labelAnchor: new THREE.Vector3(0, 0.07, 0),
    pickable: [handle],
    update(state) {
      handle.rotation.z = SELECTOR_ANGLE[String(state.position ?? "a")] ?? 0;
    },
  };
};

/** Any type without a builder still renders, as a grey box at its origin (PLAN G4). */
export const fallback: Builder = (g: ComponentGeometry) => {
  const group = placed(g);
  group.add(box([0.15, 0.15, 0.15], ISA101.stopped));
  return {
    id: g.id,
    type: g.type,
    object: group,
    labelAnchor: new THREE.Vector3(0, 0.12, 0),
    pickable: [],
    update() {},
  };
};

/**
 * Stopper: origin at the center of the blade's upstream face at belt-top height; the blade spans
 * local z and rises to `height` above the belt with the core's raised fraction. ISA-101: green
 * while raised or rising, grey when lowered.
 */
export const stopper: Builder = (g) => {
  const width = num(g, "width", 0.5);
  const height = num(g, "height", 0.05);
  const thickness = num(g, "thickness", 0.02);
  const group = placed(g);
  const blade = box([thickness, height, width], ISA101.stopped, [thickness / 2, -height / 2, 0]);
  blade.name = "blade";
  group.add(blade);
  // actuator housing under the belt, so a lowered stopper is still visible
  group.add(box([0.08, 0.06, 0.12], NEUTRAL.structure, [thickness / 2, -0.11, 0]));
  return {
    id: g.id,
    type: g.type,
    object: group,
    labelAnchor: new THREE.Vector3(0, height + 0.08, -width / 2),
    pickable: [],
    update(state) {
      const raised = typeof state.position === "number" ? state.position : 0;
      blade.position.y = height * raised - height / 2;
      setColor(blade, state.raising || raised > 0 ? ISA101.running : ISA101.stopped);
    },
  };
};

/**
 * Diverter arm: origin at the pivot at belt-top height. At rest the arm lies along local +x and it
 * swings toward local +z by the core's angle_deg (positive swing_deg). ISA-101: green while
 * extending or out, grey retracted.
 */
export const diverterArm: Builder = (g) => {
  const length = num(g, "length", 0.8);
  const height = num(g, "height", 0.15);
  const clearance = num(g, "clearance", 0.005);
  const thickness = num(g, "thickness", 0.02);
  const group = placed(g);
  group.add(zCylinder(0.03, 0.06, NEUTRAL.structure, [0, height + 0.03, 0]).rotateX(Math.PI / 2));
  const swing = new THREE.Group();
  swing.name = "arm";
  group.add(swing);
  const paddle = box([length, height - clearance, thickness], ISA101.stopped, [length / 2, clearance + (height - clearance) / 2, 0]);
  paddle.name = "paddle";
  swing.add(paddle);
  return {
    id: g.id,
    type: g.type,
    object: group,
    labelAnchor: new THREE.Vector3(0, height + 0.12, 0),
    pickable: [],
    update(state) {
      const angle = typeof state.angle_deg === "number" ? state.angle_deg : 0;
      swing.rotation.y = -THREE.MathUtils.degToRad(angle); // local +x turned toward +z
      setColor(paddle, state.extending || angle !== 0 ? ISA101.running : ISA101.stopped);
    },
  };
};

/**
 * Pick and place: origin at the bottom centre of the raised gripper pad over the pick point. A
 * gantry beam spans the traverse along local +x on two portal frames; the carriage moves with the
 * core's x_m and the pad drops by y_m. ISA-101: the pad is green while gripping, grey otherwise.
 */
export const pickAndPlace: Builder = (g, ctx) => {
  const travel = num(g, "travel", 1);
  const grip = (g.grip_size as number[] | undefined) ?? [0.25, 0.05, 0.25];
  const beamY = 0.35;
  const group = placed(g);
  group.add(box([travel + 0.6, 0.06, 0.08], NEUTRAL.frame, [travel / 2, beamY, 0]));
  const legH = g.position[1] + beamY - ctx.floorY;
  for (const x of [-0.3, travel + 0.3]) {
    group.add(box([0.06, 0.06, 0.9], NEUTRAL.frame, [x, beamY, 0]));
    for (const z of [-0.42, 0.42]) group.add(box([0.06, legH, 0.06], NEUTRAL.frame, [x, beamY - legH / 2, z]));
  }
  const carriage = new THREE.Group();
  carriage.name = "carriage";
  group.add(carriage);
  carriage.add(box([0.14, 0.1, 0.14], NEUTRAL.structure, [0, beamY - 0.06, 0]));
  const rod = box([0.03, 1, 0.03], NEUTRAL.structure);
  carriage.add(rod);
  const pad = box([grip[0], grip[1], grip[2]], ISA101.stopped);
  pad.name = "pad";
  carriage.add(pad);
  const setPose = (x: number, y: number) => {
    carriage.position.x = x;
    pad.position.y = -y + grip[1] / 2;
    const top = beamY - 0.11;
    const bottom = -y + grip[1];
    rod.scale.y = Math.max(0.01, top - bottom);
    rod.position.y = (top + bottom) / 2;
  };
  setPose(0, 0);
  return {
    id: g.id,
    type: g.type,
    object: group,
    labelAnchor: new THREE.Vector3(travel / 2, beamY + 0.1, 0),
    pickable: [],
    update(state) {
      setPose(typeof state.x_m === "number" ? state.x_m : 0, typeof state.y_m === "number" ? state.y_m : 0);
      setColor(pad, state.gripping ? ISA101.running : ISA101.stopped);
    },
  };
};

/**
 * Distance sensor: origin at the sensor face. The beam runs along local +z (axis "+z") or straight
 * down (axis "-y") and is drawn to the core's measured distance_m. ISA-101: green while a part is
 * in range, grey when the beam is clear. The reading is core-only (no physics body, PLAN §3.3).
 */
export const distanceSensor: Builder = (g) => {
  const reach = num(g, "beam_length", 1);
  const down = g.axis === "-y";
  const group = placed(g);
  const housing = box([0.06, 0.06, 0.06], NEUTRAL.frame, down ? [0, 0.03, 0] : [0, 0, -0.03]);
  group.add(housing);
  const beam = zCylinder(0.005, 1, ISA101.stopped);
  beam.name = "beam";
  const aim = new THREE.Group(); // beam along local +z, or turned to point down
  if (down) aim.rotation.x = Math.PI / 2;
  aim.add(beam);
  group.add(aim);
  const setLength = (d: number) => {
    const length = Math.max(0.001, d);
    beam.scale.y = length; // cylinder height runs along local y before its x rotation
    beam.position.z = length / 2;
  };
  setLength(reach);
  return {
    id: g.id,
    type: g.type,
    object: group,
    labelAnchor: new THREE.Vector3(0, 0.1, 0),
    pickable: [],
    update(state) {
      setLength(typeof state.distance_m === "number" ? state.distance_m : reach);
      setColor(beam, state.target ? ISA101.running : ISA101.stopped);
    },
  };
};
