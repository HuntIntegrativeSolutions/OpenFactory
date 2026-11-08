// Mesh builder registry: scene `type` -> builder (PLAN §5). Unknown types get a grey fallback box,
// so any valid scene renders with no scene-specific code (PLAN G4).
import { BELT_TYPES, type ComponentGeometry } from "../api";
import { beltConveyor, lift, rollerConveyor, turntable } from "./conveyor";
import {
  boxEmitter,
  distanceSensor,
  colorSensor,
  counterDisplay,
  diverterArm,
  fallback,
  partRemover,
  photoEye,
  pickAndPlace,
  pilotLight,
  pushbutton,
  pusher,
  selectorSwitch,
  stopper,
} from "./devices";
import { drain, junction, supply, tank } from "./process";
import type { BuildContext, Builder, ComponentView } from "./types";

export const BUILDERS: Record<string, Builder> = {
  belt_conveyor: beltConveyor,
  roller_conveyor: rollerConveyor,
  photo_eye: photoEye,
  pusher,
  box_emitter: boxEmitter,
  part_remover: partRemover,
  pushbutton,
  pilot_light: pilotLight,
  selector_switch: selectorSwitch,
  stopper,
  turntable,
  lift,
  diverter_arm: diverterArm,
  pick_and_place: pickAndPlace,
  distance_sensor: distanceSensor,
  color_sensor: colorSensor,
  counter_display: counterDisplay,
  tank,
  supply,
  drain,
  junction,
};

export function buildComponent(g: ComponentGeometry, ctx: BuildContext): ComponentView {
  return (BUILDERS[g.type] ?? fallback)(g, ctx);
}

/**
 * Floor height for a scene: 0.75 m below its lowest conveyor top (or component origin), or the
 * lowest tank bottom if that is lower: tanks stand on the floor (PLAN 3.2), a raised one on legs.
 */
export function floorFor(components: ComponentGeometry[]): number {
  const tops = components.filter((c) => BELT_TYPES.has(c.type) || c.type === "turntable" || c.type === "lift").map((c) => c.position[1]);
  const tanks = components.filter((c) => c.type === "tank").map((c) => c.position[1]);
  if (tanks.length && !tops.length) return Math.min(...tanks);
  const ys = tops.length ? tops : components.map((c) => c.position[1]);
  const floor = Math.min(0, ...ys) - 0.75;
  return tanks.length ? Math.min(floor, ...tanks) : floor;
}

export type { BuildContext, ComponentView } from "./types";
