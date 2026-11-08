import * as THREE from "three";
import { describe, expect, test } from "vitest";
import { buildComponent, floorFor } from "./components";
import type { ComponentGeometry } from "./api";
import { FILL, framedBounds, framePose, RADII } from "./framing";

const FOV = 45;

/** The bounds the viewer frames for a scene of `components` (as main.ts builds them). */
function boundsOf(components: ComponentGeometry[]): THREE.Box3 {
  const floorY = floorFor(components);
  return framedBounds(components.map((g) => buildComponent(g, { floorY }).object));
}

/** Angle in degrees between the camera's view axis and the direction to `point`, vertically. */
function verticalAngle(pose: { position: THREE.Vector3; target: THREE.Vector3 }, point: THREE.Vector3): number {
  const camera = new THREE.PerspectiveCamera(FOV, 16 / 9, 0.05, 200);
  camera.position.copy(pose.position);
  camera.lookAt(pose.target);
  camera.updateMatrixWorld(true);
  const ndc = point.clone().project(camera);
  return THREE.MathUtils.radToDeg(Math.atan(ndc.y * Math.tan(THREE.MathUtils.degToRad(FOV / 2))));
}

const device = (id: string, type: string, position: [number, number, number]): ComponentGeometry =>
  ({ id, type, position, rotation_deg: 0, color: "green", io: {} }) as unknown as ComponentGeometry;

describe("initial camera framing", () => {
  test("a pushbutton and a pilot light alone, at panel height, are both in view", () => {
    // Before: the guessed floor 0.75 m below them was added to the bounds (2 m tall), and at
    // 2.1 radii both sat 27 degrees above the view axis, past the 22.5 degree half field of view.
    const devices = [device("PB_01", "pushbutton", [0, 1.2, 0]), device("PL_01", "pilot_light", [0.12, 1.2, 0])];
    const pose = framePose(boundsOf(devices), FOV);
    for (const d of devices) {
      const angle = Math.abs(verticalAngle(pose, new THREE.Vector3(...d.position)));
      expect(angle).toBeLessThan(FOV / 2);
    }
    // and close enough that two 0.08 m faces are more than specks (framed with the floor, the
    // camera would stand back about 3 m)
    expect(pose.position.distanceTo(pose.target)).toBeLessThan(0.5);
  });

  test("a tall panel (devices stacked 0.5 m) is pulled back until its top and bottom fit", () => {
    const devices = [device("PB_01", "pushbutton", [0, 1.0, 0]), device("PB_02", "pushbutton", [0, 1.5, 0])];
    const bounds = boundsOf(devices);
    const pose = framePose(bounds, FOV);
    const radius = bounds.getSize(new THREE.Vector3()).length() / 2;
    expect(pose.position.distanceTo(pose.target)).toBeGreaterThan(radius * RADII);
    for (const y of [bounds.min.y, bounds.max.y]) {
      const angle = Math.abs(verticalAngle(pose, new THREE.Vector3(0, y, 0)));
      expect(angle).toBeLessThanOrEqual(THREE.MathUtils.radToDeg(Math.atan(Math.tan(THREE.MathUtils.degToRad(FOV / 2)) * FILL)) + 1e-6);
    }
  });

  test("a line of conveyors stays at 2.1 radii, as before the fix", () => {
    const belt = {
      id: "CONV_01", type: "belt_conveyor", position: [0, 0, 0], rotation_deg: 0,
      length: 4, width: 0.5, speed_mps: 0.5, io: {},
    } as unknown as ComponentGeometry;
    const bounds = boundsOf([belt]);
    const pose = framePose(bounds, FOV);
    const radius = bounds.getSize(new THREE.Vector3()).length() / 2;
    expect(pose.position.distanceTo(pose.target)).toBeCloseTo(radius * RADII, 9);
    expect(bounds.min.y).toBeCloseTo(floorFor([belt]), 6); // legs reach the floor on their own
  });
});
