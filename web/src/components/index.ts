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
import { drain, heater, junction, pipe, pump, supply, tank, transmitter, valveControl, valveOnoff, vfdMotor } from "./process";
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
  pipe,
  valve_onoff: valveOnoff,
  valve_control: valveControl,
  pump,
  heater,
  transmitter,
  vfd_motor: vfdMotor,
};

export function buildComponent(g: ComponentGeometry, ctx: BuildContext): ComponentView {
  return (BUILDERS[g.type] ?? fallback)(g, ctx);
}

/**
 * How far below its origin each process type is drawn (PLAN 3.2): supply and drain stubs hang
 * 0.3 m below their connection point, a pump's base 0.17 m below its centre; the rest stand on
 * their origin (a tank's bottom, a drive cabinet's base).
 */
const PROCESS_DEPTH: Record<string, number> = {
  tank: 0, supply: 0.3, drain: 0.3, junction: 0, pump: 0.17, valve_onoff: 0, valve_control: 0,
  heater: 0, transmitter: 0, vfd_motor: 0,
};

/**
 * Floor height for a scene: 0.75 m below its lowest conveyor top (or component origin), or under
 * the lowest process component if that is lower: tanks stand on the floor and a raised one on
 * legs, and nothing of a process plant is drawn below it (PLAN 3.2).
 */
export function floorFor(components: ComponentGeometry[]): number {
  const tops = components.filter((c) => BELT_TYPES.has(c.type) || c.type === "turntable" || c.type === "lift").map((c) => c.position[1]);
  const process = components.filter((c) => c.type in PROCESS_DEPTH).map((c) => c.position[1] - PROCESS_DEPTH[c.type]);
  if (process.length && !tops.length) return Math.min(...process);
  const ys = tops.length ? tops : components.map((c) => c.position[1]);
  const floor = Math.min(0, ...ys) - 0.75;
  return process.length ? Math.min(floor, ...process) : floor;
}

export type { BuildContext, ComponentView } from "./types";
