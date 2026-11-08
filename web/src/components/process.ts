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
