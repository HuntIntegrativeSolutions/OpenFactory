// Process components (PLAN 3.2, ADR-0005): the viewer draws process state only (levels, valve
// positions, pumps turning) from the core's state frames; there is no process physics here, so
// there is nothing to disagree with the core (no parity trace). ISA-101: equipment in neutral
// greys, running green, the liquid a muted blue, overflow in alarm yellow.
import * as THREE from "three";
import { ISA101 } from "../isa101";
import { box, type Builder, NEUTRAL, num, placed, setColor } from "./types";

/** The liquid's colour: a muted blue, so it reads as liquid without competing with status. */
export const LIQUID = 0x4a6fa5;

function shell(geometry: THREE.BufferGeometry): THREE.Mesh {
  return new THREE.Mesh(
    geometry,
    new THREE.MeshStandardMaterial({
      color: NEUTRAL.frame,
      transparent: true,
      opacity: 0.18,
      depthWrite: false,
      roughness: 0.4,
      metalness: 0.2,
    }),
  );
}

/**
 * Tank: origin at the centre of the bottom. A translucent shell, and the liquid inside it at the
 * core's `level_m`; legs to the floor when the tank stands above it. Liquid turns alarm yellow
 * while overflowing.
 */
export const tank: Builder = (g, ctx) => {
  const height = num(g, "height", 2);
  const isBox = g.shape === "box";
  const diameter = num(g, "diameter", 1);
  const length = num(g, "length", 1);
  const width = num(g, "width", 1);
  const group = placed(g);
  const body = isBox
    ? shell(new THREE.BoxGeometry(length, height, width))
    : shell(new THREE.CylinderGeometry(diameter / 2, diameter / 2, height, 40, 1, true));
  body.name = "shell";
  body.position.y = height / 2;
  group.add(body);
  const unit = isBox ? new THREE.BoxGeometry(length * 0.98, 1, width * 0.98) : new THREE.CylinderGeometry(diameter * 0.49, diameter * 0.49, 1, 40);
  const liquid = new THREE.Mesh(unit, new THREE.MeshStandardMaterial({ color: LIQUID, roughness: 0.3, metalness: 0.1 }));
  liquid.name = "liquid";
  group.add(liquid);
  const showLevel = (level: number) => {
    const h = Math.min(Math.max(level, 0), height);
    liquid.visible = h > 1e-4;
    liquid.scale.y = Math.max(h, 1e-4);
    liquid.position.y = h / 2;
  };
  showLevel(num(g, "initial_level", 0));
  // the shell's outline, so an empty tank still reads as a tank: box edges, or a cylinder's
  // top and bottom rims and four seams
  const outline = new THREE.LineBasicMaterial({ color: NEUTRAL.structure });
  if (isBox) {
    const edges = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(length, height, width)), outline);
    edges.position.y = height / 2;
    group.add(edges);
  } else {
    const r = diameter / 2;
    for (const y of [0, height]) {
      const ring = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(
        Array.from({ length: 48 }, (_, i) => new THREE.Vector3(r * Math.cos((i / 48) * 2 * Math.PI), y, r * Math.sin((i / 48) * 2 * Math.PI))),
      ), outline);
      group.add(ring);
    }
    const seams = [0, 1, 2, 3].flatMap((i) => {
      const a = (i / 4) * 2 * Math.PI + Math.PI / 4;
      return [new THREE.Vector3(r * Math.cos(a), 0, r * Math.sin(a)), new THREE.Vector3(r * Math.cos(a), height, r * Math.sin(a))];
    });
    group.add(new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(seams), outline));
  }
  const drop = g.position[1] - ctx.floorY;
  if (drop > 0.02) {
    const half = (isBox ? Math.min(length, width) : diameter) * 0.35;
    for (const [x, z] of [[half, half], [-half, half], [half, -half], [-half, -half]]) {
      group.add(box([0.06, drop, 0.06], NEUTRAL.frame, [x, -drop / 2, z]));
    }
  }
  return {
    id: g.id,
    type: g.type,
    object: group,
    labelAnchor: new THREE.Vector3(0, height + 0.12, 0),
    pickable: [body],
    update(state) {
      showLevel(Number(state.level_m ?? 0));
      setColor(liquid, state.overflowing ? ISA101.alarmMedium : LIQUID);
    },
  };
};

/** A short flanged stub hanging below its connection point (the flange), for supplies and drains. */
function stub(color: number): THREE.Group {
  const g = new THREE.Group();
  const pipe = new THREE.Mesh(new THREE.CylinderGeometry(0.05, 0.05, 0.3, 20), new THREE.MeshStandardMaterial({ color, roughness: 0.5, metalness: 0.3 }));
  pipe.position.y = -0.15;
  const flange = new THREE.Mesh(new THREE.CylinderGeometry(0.09, 0.09, 0.03, 24), new THREE.MeshStandardMaterial({ color: NEUTRAL.frame, roughness: 0.6 }));
  flange.position.y = 0;
  g.add(pipe, flange);
  return g;
}

/** Supply: origin at the connection point; a flanged stub, green while it delivers. */
export const supply: Builder = (g) => {
  const group = placed(g);
  const body = stub(NEUTRAL.structure);
  group.add(body);
  return {
    id: g.id,
    type: g.type,
    object: group,
    labelAnchor: new THREE.Vector3(0, 0.15, 0),
    pickable: [],
    update(state) {
      setColor(body.children[0], Number(state.flow_m3h ?? 0) > 1e-6 ? ISA101.running : NEUTRAL.structure);
    },
  };
};

/** Drain: origin at the connection point; a stub under a funnel, green while it takes flow. */
export const drain: Builder = (g) => {
  const group = placed(g);
  const body = stub(NEUTRAL.structure);
  const funnel = new THREE.Mesh(new THREE.CylinderGeometry(0.16, 0.05, 0.14, 24, 1, true), new THREE.MeshStandardMaterial({ color: NEUTRAL.frame, side: THREE.DoubleSide, roughness: 0.6 }));
  funnel.position.y = 0.07;
  group.add(body, funnel);
  return {
    id: g.id,
    type: g.type,
    object: group,
    labelAnchor: new THREE.Vector3(0, 0.25, 0),
    pickable: [],
    update(state) {
      setColor(body.children[0], Number(state.flow_m3h ?? 0) > 1e-6 ? ISA101.running : NEUTRAL.structure);
    },
  };
};

/** Junction: origin at the junction point; a small fitting. */
export const junction: Builder = (g) => {
  const group = placed(g);
  const fitting = new THREE.Mesh(new THREE.SphereGeometry(0.07, 16, 12), new THREE.MeshStandardMaterial({ color: NEUTRAL.structure, roughness: 0.5, metalness: 0.3 }));
  group.add(fitting);
  return { id: g.id, type: g.type, object: group, labelAnchor: new THREE.Vector3(0, 0.15, 0), pickable: [], update() {} };
};

/**
 * Pipe: drawn through the core's `points` (scene coordinates, its ends on the components it
 * joins), one cylinder per segment with a fitting at each bend. Neutral grey: a pipe carries
 * no status of its own.
 */
/** A link's drawn points (scene coordinates), or a 1 m stub from its origin without them. */
function linkPoints(g: Parameters<Builder>[0]): THREE.Vector3[] {
  const raw = Array.isArray(g.points) ? (g.points as number[][]) : [];
  return (raw.length >= 2 ? raw : [g.position, [g.position[0] + 1, g.position[1], g.position[2]]]).map((p) => new THREE.Vector3(p[0], p[1], p[2]));
}

/** Pipe segments through `points` into `group`, a fitting at each bend. */
function runs(group: THREE.Group, points: THREE.Vector3[], radius: number): void {
  const material = new THREE.MeshStandardMaterial({ color: NEUTRAL.structure, roughness: 0.5, metalness: 0.3 });
  for (let i = 0; i + 1 < points.length; i++) {
    const a = points[i], b = points[i + 1];
    const length = a.distanceTo(b);
    if (length < 1e-6) continue;
    const segment = new THREE.Mesh(new THREE.CylinderGeometry(radius, radius, length, 16), material);
    segment.name = `segment${i}`;
    segment.position.copy(a).add(b).multiplyScalar(0.5);
    segment.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), b.clone().sub(a).normalize());
    group.add(segment);
    if (i > 0) {
      const bend = new THREE.Mesh(new THREE.SphereGeometry(radius * 1.05, 12, 8), material);
      bend.position.copy(a);
      group.add(bend);
    }
  }
}

export const pipe: Builder = (g) => {
  const group = new THREE.Group();
  group.name = g.id;
  const points = linkPoints(g);
  runs(group, points, Math.max(num(g, "diameter", 0.05) / 2, 0.015));
  const mid = points[Math.floor(points.length / 2)];
  return { id: g.id, type: g.type, object: group, labelAnchor: mid.clone().add(new THREE.Vector3(0, 0.12, 0)), pickable: [], update() {} };
};

/**
 * A valve drawn at its origin, joined to its ends by short runs: a body and, on top, a handle
 * that turns with the opening (0 to 1: across the line to along it), green fully open.
 */
function valve(g: Parameters<Builder>[0], actuator: number): { group: THREE.Group; handle: THREE.Mesh; update(opening: number): void } {
  const group = new THREE.Group();
  group.name = g.id;
  runs(group, linkPoints(g), 0.025);
  const body = box([0.16, 0.12, 0.12], NEUTRAL.structure, [g.position[0], g.position[1], g.position[2]]);
  body.name = "body";
  const stem = new THREE.Mesh(new THREE.CylinderGeometry(0.05, 0.05, actuator, 16), new THREE.MeshStandardMaterial({ color: NEUTRAL.frame, roughness: 0.6 }));
  stem.position.set(g.position[0], g.position[1] + 0.06 + actuator / 2, g.position[2]);
  const handle = box([0.2, 0.02, 0.04], ISA101.stopped, [g.position[0], g.position[1] + 0.07 + actuator, g.position[2]]);
  handle.name = "handle";
  group.add(body, stem, handle);
  return {
    group,
    handle,
    update(opening: number) {
      const along = THREE.MathUtils.degToRad(g.rotation_deg);
      handle.rotation.y = along + (1 - Math.min(Math.max(opening, 0), 1)) * (Math.PI / 2);
      setColor(handle, opening >= 0.999 ? ISA101.running : ISA101.stopped);
    },
  };
}

/** On/off valve: body, actuator, and a handle across the line shut, along it open. */
export const valveOnoff: Builder = (g) => {
  const v = valve(g, 0.14);
  v.update(0);
  return {
    id: g.id,
    type: g.type,
    object: v.group,
    labelAnchor: new THREE.Vector3(g.position[0], g.position[1] + 0.35, g.position[2]),
    pickable: [],
    update(state) {
      v.update(Number(state.opening ?? 0));
    },
  };
};

/** Control valve: body, a taller actuator, and a handle that turns with the stem position. */
export const valveControl: Builder = (g) => {
  const v = valve(g, 0.24);
  v.update(0);
  return {
    id: g.id,
    type: g.type,
    object: v.group,
    labelAnchor: new THREE.Vector3(g.position[0], g.position[1] + 0.45, g.position[2]),
    pickable: [],
    update(state) {
      v.update(Number(state.position ?? 0) / 100);
    },
  };
};
