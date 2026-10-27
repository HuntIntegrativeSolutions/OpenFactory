// Mesh builder registry: scene `type` -> builder (PLAN §5). Unknown types get a grey fallback box,
// so any valid scene renders with no scene-specific code (PLAN G4).
import { BELT_TYPES, type ComponentGeometry } from "../api";
import { beltConveyor, rollerConveyor, turntable } from "./conveyor";
import {
  boxEmitter,
  distanceSensor,
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
  diverter_arm: diverterArm,
  pick_and_place: pickAndPlace,
  distance_sensor: distanceSensor,
};

export function buildComponent(g: ComponentGeometry, ctx: BuildContext): ComponentView {
  return (BUILDERS[g.type] ?? fallback)(g, ctx);
}

/** Floor height for a scene: 0.75 m below its lowest conveyor top (or component origin). */
export function floorFor(components: ComponentGeometry[]): number {
  const tops = components.filter((c) => BELT_TYPES.has(c.type) || c.type === "turntable").map((c) => c.position[1]);
  const ys = tops.length ? tops : components.map((c) => c.position[1]);
  return Math.min(0, ...ys) - 0.75;
}

export type { BuildContext, ComponentView } from "./types";
