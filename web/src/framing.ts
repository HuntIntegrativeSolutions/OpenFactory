// Initial camera framing: where the viewer puts the camera when a scene loads. The camera looks
// at the centre of the components' bounds from a fixed direction (up and to the right, toward
// -z), at 2.1 times the bounds' radius, which suits a line of conveyors, or further back if some
// corner of the bounds would fall above or below the view (a tall, narrow panel of devices).
// Only the vertical field of view is fitted: on a landscape screen every conveyor scene already
// fits across at 2.1 radii, and fitting across on a portrait phone would pull every scene back
// about three times (a change of its own, not made here).
// The bounds are the components' own: conveyor legs and pedestals already reach the floor, and a
// scene of devices alone (no conveyor) has only a guessed floor, 0.75 m below its lowest device,
// so the floor is not added to them (it pushed two panel devices off the top of the screen).
import * as THREE from "three";

export const VIEW_DIRECTION = new THREE.Vector3(0.25, 0.75, 1.0).normalize();
/** Camera distance in bounds radii for a scene that fits at it (every conveyor scene so far). */
export const RADII = 2.1;
/** Fraction of the half field of view a corner may use, so labels just above it still show. */
export const FILL = 0.85;

/** The bounds the camera frames: the components' own objects, nothing else (no floor point). */
export function framedBounds(objects: THREE.Object3D[]): THREE.Box3 {
  const bounds = new THREE.Box3();
  for (const object of objects) {
    object.updateMatrixWorld(true);
    bounds.expandByObject(object);
  }
  return bounds;
}

export interface CameraPose {
  position: THREE.Vector3;
  target: THREE.Vector3;
}

/**
 * The camera pose that frames `bounds` for a camera with vertical field of view `fovDeg`: RADII
 * times the radius from the centre, or further back along the same direction until every corner
 * is within FILL of the half field of view above and below the centre.
 */
export function framePose(bounds: THREE.Box3, fovDeg: number): CameraPose {
  const target = bounds.getCenter(new THREE.Vector3());
  const radius = bounds.getSize(new THREE.Vector3()).length() / 2;
  const toCamera = VIEW_DIRECTION;
  const forward = toCamera.clone().negate();
  const right = new THREE.Vector3().crossVectors(forward, new THREE.Vector3(0, 1, 0)).normalize();
  const up = new THREE.Vector3().crossVectors(right, forward);
  const tanV = Math.tan(THREE.MathUtils.degToRad(fovDeg) / 2) * FILL;
  let distance = radius * RADII;
  for (const x of [bounds.min.x, bounds.max.x]) {
    for (const y of [bounds.min.y, bounds.max.y]) {
      for (const z of [bounds.min.z, bounds.max.z]) {
        const q = new THREE.Vector3(x, y, z).sub(target);
        // depth in front of a camera `distance` back is distance - q.toCamera
        const along = q.dot(toCamera);
        distance = Math.max(distance, along + Math.abs(q.dot(up)) / tanV);
      }
    }
  }
  return { position: target.clone().addScaledVector(toCamera, distance), target };
}
