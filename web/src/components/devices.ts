// Mesh builders for photo_eye, pusher, box_emitter, part_remover, and the operator devices
// (pushbutton, pilot_light, selector_switch). Origins follow PLAN §5 "behavior as built".
// Status colors are ISA-101: green while active (beam blocked, plate extending, emitter on),
// grey otherwise.
import * as THREE from "three";
import type { ComponentGeometry } from "../api";
import { ISA101 } from "../isa101";
import { PART_COLORS } from "../parts";
import { NEUTRAL, box, num, placed, setColor, wireBox, zCylinder, type Builder, type DefaultHousing, type HousingPart } from "./types";

/** Photo eye: origin at the beam midpoint; the beam spans local z. */
export const photoEye: Builder = (g) => {
  const beamLength = num(g, "beam_length", 0.5);
  const group = placed(g);
  const eye = new THREE.Group(); // heads and beam; moved along x by a misaligned fault (PLAN 2.6)
  group.add(eye);
  const heads: THREE.Object3D[] = [];
  const parts: HousingPart[] = [];
  for (const side of [-1, 1]) {
    const at: [number, number, number] = [0, 0, side * (beamLength / 2 + 0.02)];
    heads.push(box([0.05, 0.09, 0.04], NEUTRAL.frame, at));
    // each model head faces +z: the far one turns to face the beam too (PLAN 4.2)
    for (const part of ["head", "bracket"]) parts.push({ part, at, turnDeg: side < 0 ? 0 : 180 });
  }
  eye.add(...heads);
  const beam = zCylinder(0.006, beamLength, ISA101.stopped);
  beam.name = "beam";
  eye.add(beam);
  return {
    id: g.id,
    type: g.type,
    object: group,
    labelAnchor: new THREE.Vector3(0, 0.1, -beamLength / 2),
    pickable: [],
    // the heads move with a misaligned fault, so a scene mesh is drawn beside them (2.15), but
    // the default model hangs in the moving group and replaces them
    defaultHousing: { model: "photo_eye", parts, parent: eye, replaces: heads },
    update(state) {
      eye.position.x = typeof state.beam_offset_m === "number" ? state.beam_offset_m : 0;
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
  const body = zCylinder(0.045, bodyLength, NEUTRAL.structure, [0, 0, -0.06 - bodyLength / 2]);
  group.add(body);
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
    housing: [body],
    defaultHousing: pusherModel(bodyLength),
    update(state) {
      setStroke(typeof state.position_m === "number" ? state.position_m : 0);
      setColor(plate, state.extending ? ISA101.running : ISA101.stopped);
    },
  };
};

/**
 * The pneumatic cylinder model (PLAN 4.2, `pusher` in tools/make_models.py) over the primitive
 * body's span, z -0.06 to -0.06 - bodyLength: the front cap (with the rod gland) behind the
 * retracted plate, the rear cap at the far end, the barrel between them, the tie rods from cap
 * to cap, and a mounting foot under each cap.
 */
function pusherModel(bodyLength: number): DefaultHousing {
  const front = -0.06;
  const rear = front - bodyLength + 0.04; // where the 0.04 m deep rear cap starts
  return {
    model: "pusher",
    parts: [
      { part: "front_cap", at: [0, 0, front] },
      { part: "rear_cap", at: [0, 0, rear] },
      { part: "barrel", at: [0, 0, front - 0.04], scale: [1, 1, bodyLength - 0.08] },
      { part: "tie_rods", at: [0, 0, front], scale: [1, 1, bodyLength] },
      { part: "foot", at: [0, 0, front] },
      { part: "foot", at: [0, 0, front - bodyLength], scale: [1, 1, -1] }, // toward the front
    ],
  };
}

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
  const plate = box([0.08, 0.08, 0.02], NEUTRAL.frame, [0, 0, -0.03]);
  group.add(plate);
  const cap = lens(LAMP[String(g.color ?? "green")] ?? ISA101.running);
  cap.position.z = -0.0125;
  group.add(cap);
  return {
    id: g.id,
    type: g.type,
    object: group,
    labelAnchor: new THREE.Vector3(0, 0.07, 0),
    pickable: [cap],
    housing: [plate],
    // the enclosure and guard collar (PLAN 4.2); the cap, which shows the color and moves, stays
    defaultHousing: { model: "pushbutton", parts: [{ part: "panel", at: [0, 0, 0] }] },
    update(state) {
      cap.material.emissiveIntensity = state.pressed ? 0.8 : 0;
      cap.position.z = state.pressed ? -0.02 : -0.0125;
    },
  };
};

/** Pilot light: origin at the lens center; lit while `on`. */
export const pilotLight: Builder = (g) => {
  const group = placed(g);
  const plate = box([0.08, 0.08, 0.02], NEUTRAL.frame, [0, 0, -0.03]);
  group.add(plate);
  const lamp = lens(LAMP[String(g.color ?? "green")] ?? ISA101.running);
  lamp.position.z = -0.0125;
  group.add(lamp);
  return {
    id: g.id,
    type: g.type,
    object: group,
    labelAnchor: new THREE.Vector3(0, 0.07, 0),
    pickable: [],
    housing: [plate],
    // the enclosure and bezel ring (PLAN 4.2); the lens, which lights, stays a primitive
    defaultHousing: { model: "pilot_light", parts: [{ part: "panel", at: [0, 0, 0] }] },
    update(state) {
      lamp.material.emissiveIntensity = state.lit ? 1.2 : 0;
    },
  };
};

// Seven segments a-g of one digit (a top, b top right, c bottom right, d bottom, e bottom left,
// f top left, g middle) and the segments each character lights.
const SEGMENTS: Record<string, string> = {
  "0": "abcdef", "1": "bc", "2": "abdeg", "3": "abcdg", "4": "bcfg", "5": "acdfg",
  "6": "acdefg", "7": "abc", "8": "abcdefg", "9": "abcdfg", "-": "g", " ": "",
};
const DIGIT_W = 0.024;
const DIGIT_H = 0.04;
const SEG_T = 0.004;
const SEGMENT_OFF = 0x2b2f36;

/** Split a display text such as " 4.2" into one cell per digit: its character and its point. */
export function displayCells(text: string, digits: number): { ch: string; dot: boolean }[] {
  const cells: { ch: string; dot: boolean }[] = [];
  for (const ch of text) {
    if (ch === "." && cells.length) cells[cells.length - 1].dot = true;
    else cells.push({ ch, dot: false });
  }
  while (cells.length < digits) cells.unshift({ ch: " ", dot: false });
  return cells.slice(-digits);
}

/** Counter display: origin at the display face centre, facing local +z; seven-segment digits. */
export const counterDisplay: Builder = (g) => {
  const digits = Math.max(1, Math.round(num(g, "digits", 4)));
  const on = LAMP[String(g.color ?? "red")] ?? ISA101.alarmHigh;
  const group = placed(g);
  const pitch = DIGIT_W + 0.012;
  const width = digits * pitch + 0.02;
  const plate = box([width, DIGIT_H + 0.03, 0.02], NEUTRAL.frame, [0, 0, -0.011]);
  group.add(plate);
  const at: Record<string, [number, number, number]> = {
    a: [0, DIGIT_H / 2, 0], b: [DIGIT_W / 2, DIGIT_H / 4, 0], c: [DIGIT_W / 2, -DIGIT_H / 4, 0],
    d: [0, -DIGIT_H / 2, 0], e: [-DIGIT_W / 2, -DIGIT_H / 4, 0], f: [-DIGIT_W / 2, DIGIT_H / 4, 0],
    g: [0, 0, 0],
  };
  const cells: { segments: Record<string, THREE.Mesh>; dot: THREE.Mesh }[] = [];
  for (let i = 0; i < digits; i++) {
    const cx = (i - (digits - 1) / 2) * pitch;
    const segments: Record<string, THREE.Mesh> = {};
    for (const [name, [x, y]] of Object.entries(at)) {
      const across = name === "a" || name === "d" || name === "g";
      const size: [number, number, number] = across
        ? [DIGIT_W, SEG_T, 0.002]
        : [SEG_T, DIGIT_H / 2, 0.002];
      const seg = box(size, SEGMENT_OFF, [cx + x, y, 0]);
      seg.name = `digit${i}.${name}`;
      segments[name] = seg;
      group.add(seg);
    }
    const dot = box([SEG_T, SEG_T, 0.002], SEGMENT_OFF, [cx + DIGIT_W / 2 + 0.005, -DIGIT_H / 2, 0]);
    dot.name = `digit${i}.dp`;
    group.add(dot);
    cells.push({ segments, dot });
  }
  return {
    id: g.id,
    type: g.type,
    object: group,
    labelAnchor: new THREE.Vector3(0, DIGIT_H / 2 + 0.05, 0),
    pickable: [],
    housing: [plate],
    update(state) {
      const shown = displayCells(String(state.text ?? ""), digits);
      shown.forEach((cell, i) => {
        const lit = SEGMENTS[cell.ch] ?? "";
        for (const [name, seg] of Object.entries(cells[i].segments)) {
          setColor(seg, lit.includes(name) ? on : SEGMENT_OFF);
        }
        setColor(cells[i].dot, cell.dot ? on : SEGMENT_OFF);
      });
    },
  };
};

const SELECTOR_ANGLE: Record<string, number> = { a: Math.PI / 4, center: 0, b: -Math.PI / 4 };

/** Selector switch: origin at the switch face center; the handle turns a / center / b. */
export const selectorSwitch: Builder = (g) => {
  const group = placed(g);
  const plate = box([0.08, 0.08, 0.02], NEUTRAL.frame, [0, 0, -0.03]);
  const bezel = zCylinder(0.03, 0.02, NEUTRAL.structure, [0, 0, -0.01]);
  group.add(plate, bezel);
  const handle = box([0.012, 0.05, 0.02], ISA101.normalText, [0, 0, 0.005]);
  group.add(handle);
  return {
    id: g.id,
    type: g.type,
    object: group,
    labelAnchor: new THREE.Vector3(0, 0.07, 0),
    pickable: [handle],
    housing: [plate, bezel],
    // the enclosure, bezel and index marks (PLAN 4.2); the handle, which turns, stays a primitive
    defaultHousing: { model: "selector_switch", parts: [{ part: "panel", at: [0, 0, 0] }] },
    update(state) {
      handle.rotation.z = SELECTOR_ANGLE[String(state.position ?? "a")] ?? 0;
    },
  };
};

/** Any type without a builder still renders, as a grey box at its origin (PLAN G4). */
export const fallback: Builder = (g: ComponentGeometry) => {
  const group = placed(g);
  const marker = box([0.15, 0.15, 0.15], ISA101.stopped);
  group.add(marker);
  return {
    id: g.id,
    type: g.type,
    object: group,
    labelAnchor: new THREE.Vector3(0, 0.12, 0),
    pickable: [],
    housing: [marker],
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
  const actuator = box([0.08, 0.06, 0.12], NEUTRAL.structure, [thickness / 2, -0.11, 0]);
  group.add(actuator);
  return {
    id: g.id,
    type: g.type,
    object: group,
    labelAnchor: new THREE.Vector3(0, height + 0.08, -width / 2),
    pickable: [],
    housing: [actuator],
    // the actuator model (PLAN 4.2) where the primitive box hangs; the blade stays a primitive
    defaultHousing: { model: "stopper", parts: [{ part: "actuator", at: [thickness / 2, -0.11, 0] }] },
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
  const pivot = zCylinder(0.03, 0.06, NEUTRAL.structure, [0, height + 0.03, 0]).rotateX(Math.PI / 2);
  group.add(pivot);
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
    housing: [pivot],
    // the drive model (PLAN 4.2) on the pivot axis, from the paddle's top up; the arm swings
    defaultHousing: { model: "diverter_arm", parts: [{ part: "drive", at: [0, height, 0] }] },
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
  const frame = [box([travel + 0.6, 0.06, 0.08], NEUTRAL.frame, [travel / 2, beamY, 0])];
  const legH = g.position[1] + beamY - ctx.floorY;
  for (const x of [-0.3, travel + 0.3]) {
    frame.push(box([0.06, 0.06, 0.9], NEUTRAL.frame, [x, beamY, 0]));
    for (const z of [-0.42, 0.42]) frame.push(box([0.06, legH, 0.06], NEUTRAL.frame, [x, beamY - legH / 2, z]));
  }
  group.add(...frame);
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
    housing: frame,
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
    housing: [housing],
    update(state) {
      setLength(typeof state.distance_m === "number" ? state.distance_m : reach);
      setColor(beam, state.target ? ISA101.running : ISA101.stopped);
    },
  };
};

/**
 * Color sensor (PLAN 2.7): the distance sensor's housing and beam, drawn to where the beam stops,
 * with a swatch of the taught color on top. ISA-101: the beam is green while the taught color is
 * seen (match), grey otherwise.
 */
export const colorSensor: Builder = (g, ctx) => {
  const view = distanceSensor(g, ctx);
  const down = g.axis === "-y";
  const tone = PART_COLORS[String(g.teach)] ?? NEUTRAL.frame;
  const swatch = box([0.04, 0.012, 0.04], tone, down ? [0, 0.066, 0] : [0, 0.036, -0.03]);
  swatch.name = "teach";
  view.object.add(swatch);
  return {
    ...view,
    update(state) {
      view.update({ ...state, target: Boolean(state.match) });
    },
  };
};
