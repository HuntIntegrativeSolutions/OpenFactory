// Bulk solids (PLAN 5.7a, ADR-0006): the viewer draws contents, not grains, from the core's
// state frames: a bin's fill as a solid at its level, a screw's load as a ridge along its length,
// a gate's blade where it stands. No particles and no physics here; the core is authoritative,
// as for liquids. ISA-101: equipment in neutral greys, running green, the material a muted
// tan, a bridge or a blocked outlet in alarm yellow.
import * as THREE from "three";
import { ISA101 } from "../isa101";
import { linkPoints, runs } from "./process";
import { asStatus, box, type Builder, NEUTRAL, num, placed, setColor, showStatus } from "./types";

/** The material's colour: a muted tan, so it reads as a bulk solid without competing with status. */
export const MATERIAL = 0xb8a88a;

function shellMaterial(): THREE.MeshStandardMaterial {
  return new THREE.MeshStandardMaterial({ color: NEUTRAL.frame, transparent: true, opacity: 0.18, depthWrite: false, roughness: 0.4, metalness: 0.2, side: THREE.DoubleSide });
}

/** A frustum `h` tall from an outlet `bottom` across to `top` across, its base at y = 0: round,
 * or square (four sides, turned so its faces lie along x and z) when `square`. */
function frustum(bottom: number, top: number, h: number, square: boolean, open = false): THREE.CylinderGeometry {
  const k = square ? Math.SQRT1_2 : 0.5;
  const geo = new THREE.CylinderGeometry(top * k, bottom * k, Math.max(h, 1e-4), square ? 4 : 40, 1, open);
  if (square) geo.rotateY(Math.PI / 4);
  geo.translate(0, Math.max(h, 1e-4) / 2, 0);
  return geo;
}

/**
 * Bin: origin at the centre of the outlet. A translucent shell (a cylinder or box over a cone or
 * pyramid), legs to the floor, and the material inside at the core's `level_m`, its surface flat.
 * The fill turns alarm yellow while the material bridges over the outlet.
 */
export const bin: Builder = (g, ctx) => {
  const isBox = g.shape === "box";
  const diameter = num(g, "diameter", 2);
  const length = isBox ? num(g, "length", 1) : diameter;
  const width = isBox ? num(g, "width", 1) : diameter;
  const height = num(g, "height", 3);
  const cone = num(g, "cone_height", 0);
  const outlet = num(g, "outlet_diameter", 0.3);
  const group = placed(g);
  // the shell: the hopper section, then the wall above it
  const shellMat = shellMaterial();
  const parts: THREE.Mesh[] = [];
  if (cone > 0) {
    const hopper = new THREE.Mesh(frustum(outlet / length, 1, cone, isBox, true), shellMat);
    hopper.scale.set(length, 1, width);
    parts.push(hopper);
  }
  const wall = new THREE.Mesh(isBox ? new THREE.BoxGeometry(length, height, width) : new THREE.CylinderGeometry(diameter / 2, diameter / 2, height, 40, 1, true), shellMat);
  wall.position.y = cone + height / 2;
  parts.push(wall);
  for (const p of parts) {
    p.name = "shell";
    group.add(p);
  }
  const outline = new THREE.LineBasicMaterial({ color: NEUTRAL.structure });
  for (const y of [cone, cone + height]) {
    const ring = isBox
      ? new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.PlaneGeometry(length, width).rotateX(-Math.PI / 2)), outline)
      : new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(Array.from({ length: 48 }, (_, i) => new THREE.Vector3((diameter / 2) * Math.cos((i / 48) * 2 * Math.PI), 0, (diameter / 2) * Math.sin((i / 48) * 2 * Math.PI)))), outline);
    ring.position.y = y;
    group.add(ring);
  }
  // the material: the hopper's fill (rebuilt as the level moves in it) and the wall's (scaled)
  const fillMat = new THREE.MeshStandardMaterial({ color: MATERIAL, roughness: 0.9, metalness: 0 });
  const coneFill = new THREE.Mesh(new THREE.BufferGeometry(), fillMat);
  coneFill.name = "fill";
  const wallFill = new THREE.Mesh(isBox ? new THREE.BoxGeometry(length * 0.98, 1, width * 0.98) : new THREE.CylinderGeometry(diameter * 0.49, diameter * 0.49, 1, 40), fillMat);
  wallFill.name = "fill";
  group.add(coneFill, wallFill);
  let shownCone = -1;
  const showLevel = (level: number) => {
    const y = Math.min(Math.max(level, 0), cone + height);
    const inCone = cone > 0 ? Math.min(y, cone) : 0;
    if (Math.abs(inCone - shownCone) > 1e-3) {
      shownCone = inCone;
      coneFill.geometry.dispose();
      const t = inCone / cone;
      const topRel = outlet / length + (1 - outlet / length) * t; // across at the surface, as a share of `length`
      coneFill.geometry = inCone > 1e-4 ? frustum(outlet / length, topRel, inCone, isBox) : new THREE.BufferGeometry();
      coneFill.scale.set(length * 0.98, 1, width * 0.98);
    }
    const inWall = Math.max(y - cone, 0);
    wallFill.visible = inWall > 1e-4;
    wallFill.scale.y = Math.max(inWall, 1e-4);
    wallFill.position.y = cone + inWall / 2;
  };
  showLevel(0);
  const drop = g.position[1] - ctx.floorY;
  if (drop > 0.02) {
    const half = Math.min(length, width) * 0.35;
    for (const [x, z] of [[half, half], [-half, half], [half, -half], [-half, -half]]) {
      group.add(box([0.08, drop + cone, 0.08], NEUTRAL.frame, [x, (cone - drop) / 2, z]));
    }
  }
  return {
    id: g.id,
    type: g.type,
    object: group,
    labelAnchor: new THREE.Vector3(0, cone + height + 0.15, 0),
    pickable: parts,
    update(state) {
      showLevel(Number(state.level_m ?? 0));
      const bridged = Boolean(state.bridged) && Number(state.mass_kg ?? 0) > 0;
      showStatus(coneFill, bridged ? ISA101.alarmMedium : null);
      showStatus(wallFill, bridged ? ISA101.alarmMedium : null);
    },
  };
};

/** Bulk supply: origin where it gives; a flanged inlet stub, green while it gives. */
export const bulkSupply: Builder = (g) => {
  const group = placed(g);
  const pipe = asStatus(new THREE.Mesh(new THREE.CylinderGeometry(0.08, 0.08, 0.4, 20), new THREE.MeshStandardMaterial({ color: ISA101.stopped, roughness: 0.5, metalness: 0.3 })));
  pipe.position.y = 0.2;
  pipe.name = "line";
  group.add(pipe, box([0.22, 0.03, 0.22], NEUTRAL.frame, [0, 0, 0]));
  return {
    id: g.id,
    type: g.type,
    object: group,
    labelAnchor: new THREE.Vector3(0, 0.5, 0),
    pickable: [pipe],
    update(state) {
      setColor(pipe, Number(state.rate_kg_s ?? 0) > 1e-9 ? ISA101.running : ISA101.stopped);
    },
  };
};

/** Bulk discharge: origin where it takes; a hopper mouth over a stub, green while it takes. */
export const bulkDischarge: Builder = (g) => {
  const group = placed(g);
  const mouth = asStatus(new THREE.Mesh(new THREE.CylinderGeometry(0.22, 0.08, 0.2, 24, 1, true), new THREE.MeshStandardMaterial({ color: ISA101.stopped, side: THREE.DoubleSide, roughness: 0.6 })));
  mouth.position.y = -0.1;
  mouth.name = "mouth";
  const stub = new THREE.Mesh(new THREE.CylinderGeometry(0.08, 0.08, 0.2, 20), new THREE.MeshStandardMaterial({ color: NEUTRAL.structure, roughness: 0.5 }));
  stub.position.y = -0.3;
  group.add(mouth, stub);
  return {
    id: g.id,
    type: g.type,
    object: group,
    labelAnchor: new THREE.Vector3(0, 0.15, 0),
    pickable: [mouth],
    update(state) {
      setColor(mouth, Number(state.rate_kg_s ?? 0) > 1e-9 ? ISA101.running : ISA101.stopped);
    },
  };
};

/** The chutes of a bulk link: from its `from` end's outlet to its inlet, and from its outlet to
 * its `to` end's inlet (the core's `points`). */
function chutes(group: THREE.Group, g: Parameters<Builder>[0]): void {
  const p = linkPoints(g);
  if (p.length >= 4) {
    runs(group, [p[0], p[1]], 0.06);
    runs(group, [p[2], p[3]], 0.06);
  }
}

/** A small alarm lamp on a link: yellow while its outlet is blocked (its plug switch). */
function plugLamp(at: THREE.Vector3): THREE.Mesh {
  const lamp = asStatus(new THREE.Mesh(new THREE.SphereGeometry(0.05, 12, 8), new THREE.MeshStandardMaterial({ color: ISA101.stopped, roughness: 0.4 })));
  lamp.position.copy(at);
  lamp.name = "plug";
  return lamp;
}

/** Rotary airlock: a housing under its bin with a rotor, green while it runs, turning with its rate. */
export const rotaryAirlock: Builder = (g) => {
  const group = new THREE.Group();
  group.name = g.id;
  chutes(group, g);
  const size = num(g, "size", 0.35);
  const at = new THREE.Vector3(g.position[0], g.position[1], g.position[2]);
  const housing = asStatus(new THREE.Mesh(new THREE.CylinderGeometry(size / 2, size / 2, size, 24), new THREE.MeshStandardMaterial({ color: ISA101.stopped, roughness: 0.5, metalness: 0.3 })));
  housing.rotation.z = Math.PI / 2;
  housing.position.copy(at);
  housing.name = "housing";
  const rotor = box([size * 1.02, 0.04, size * 0.9], NEUTRAL.structure, [at.x, at.y, at.z]);
  rotor.name = "rotor";
  const motor = box([0.16, 0.16, 0.2], NEUTRAL.frame, [at.x, at.y, at.z + size * 0.5 + 0.1]);
  const lamp = plugLamp(at.clone().add(new THREE.Vector3(0, size / 2 + 0.06, 0)));
  group.add(housing, rotor, motor, lamp);
  return {
    id: g.id,
    type: g.type,
    object: group,
    labelAnchor: at.clone().add(new THREE.Vector3(0, size + 0.15, 0)),
    pickable: [housing],
    update(state) {
      setColor(housing, state.running ? ISA101.running : ISA101.stopped);
      rotor.rotation.x += Math.min(Number(state.rate_kg_s ?? 0), 20) * 0.02;
      setColor(lamp, state.plugged ? ISA101.alarmMedium : ISA101.stopped);
    },
  };
};

/**
 * Screw conveyor: origin at the centre of the inlet end, running `length` along local +x and
 * rising `incline_deg`. A translucent trough, a motor at the outlet end (green while it turns),
 * and the load along it: one ridge segment per slot group, its height the fill there.
 */
export const screwConveyor: Builder = (g) => {
  const group = new THREE.Group();
  group.name = g.id;
  chutes(group, g);
  const length = num(g, "length", 3);
  const d = num(g, "diameter", 0.25);
  const body = placed(g);
  const tilt = new THREE.Group();
  tilt.rotation.z = THREE.MathUtils.degToRad(num(g, "incline_deg", 0));
  body.add(tilt);
  const trough = new THREE.Mesh(new THREE.CylinderGeometry(d / 2, d / 2, length, 24, 1, true), shellMaterial());
  trough.rotation.z = Math.PI / 2;
  trough.position.x = length / 2;
  trough.name = "trough";
  const motor = asStatus(new THREE.Mesh(new THREE.CylinderGeometry(d * 0.45, d * 0.45, 0.3, 20), new THREE.MeshStandardMaterial({ color: ISA101.stopped, roughness: 0.5, metalness: 0.3 })));
  motor.rotation.z = Math.PI / 2;
  motor.position.x = length + 0.15;
  motor.name = "motor";
  const flight = new THREE.Mesh(new THREE.CylinderGeometry(0.02, 0.02, length, 8), new THREE.MeshStandardMaterial({ color: NEUTRAL.structure, roughness: 0.5 }));
  flight.rotation.z = Math.PI / 2;
  flight.position.x = length / 2;
  tilt.add(trough, motor, flight);
  const segments = 16;
  const fillMat = new THREE.MeshStandardMaterial({ color: MATERIAL, roughness: 0.9, metalness: 0 });
  const ridge: THREE.Mesh[] = [];
  for (let i = 0; i < segments; i++) {
    const seg = new THREE.Mesh(new THREE.BoxGeometry(length / segments * 0.96, 1, d * 0.8), fillMat);
    seg.position.x = (i + 0.5) * (length / segments);
    seg.visible = false;
    seg.name = "load";
    ridge.push(seg);
    tilt.add(seg);
  }
  const lamp = plugLamp(new THREE.Vector3(length, d / 2 + 0.08, 0));
  tilt.add(lamp);
  group.add(body);
  return {
    id: g.id,
    type: g.type,
    object: group,
    labelAnchor: new THREE.Vector3(g.position[0], g.position[1] + d + 0.3, g.position[2]),
    pickable: [trough],
    update(state) {
      setColor(motor, state.turning ? ISA101.running : ISA101.stopped);
      const load = Array.isArray(state.load) ? (state.load as number[]) : [];
      ridge.forEach((seg, i) => {
        const fill = Math.min(Math.max(Number(load[i] ?? 0), 0), 1);
        seg.visible = fill > 1e-3;
        const h = Math.max(fill * d * 0.8, 1e-4);
        seg.scale.y = h;
        seg.position.y = -d * 0.4 + h / 2;
      });
      setColor(lamp, state.plugged ? ISA101.alarmMedium : ISA101.stopped);
    },
  };
};

/** Slide gate: a frame under its bin and a blade that slides open, green fully open. */
export const slideGate: Builder = (g) => {
  const group = new THREE.Group();
  group.name = g.id;
  chutes(group, g);
  const size = num(g, "size", 0.35);
  const at = new THREE.Vector3(g.position[0], g.position[1], g.position[2]);
  const frame = box([size * 2.2, 0.08, size * 1.2], NEUTRAL.frame, [at.x + size * 0.6, at.y, at.z]);
  frame.name = "frame";
  const blade = asStatus(box([size, 0.04, size], ISA101.stopped, [at.x, at.y + 0.02, at.z]));
  blade.name = "blade";
  const cylinder = new THREE.Mesh(new THREE.CylinderGeometry(0.04, 0.04, size * 1.2, 12), new THREE.MeshStandardMaterial({ color: NEUTRAL.structure, roughness: 0.5 }));
  cylinder.rotation.z = Math.PI / 2;
  cylinder.position.set(at.x + size * 1.4, at.y + 0.06, at.z);
  const lamp = plugLamp(at.clone().add(new THREE.Vector3(-size * 0.7, 0.08, 0)));
  group.add(frame, blade, cylinder, lamp);
  return {
    id: g.id,
    type: g.type,
    object: group,
    labelAnchor: at.clone().add(new THREE.Vector3(0, 0.3, 0)),
    pickable: [frame],
    update(state) {
      const opening = Math.min(Math.max(Number(state.opening ?? 0), 0), 1);
      blade.position.x = at.x + opening * size; // slides off the outlet as it opens
      setColor(blade, opening >= 0.999 ? ISA101.running : ISA101.stopped);
      setColor(lamp, state.plugged ? ISA101.alarmMedium : ISA101.stopped);
    },
  };
};
