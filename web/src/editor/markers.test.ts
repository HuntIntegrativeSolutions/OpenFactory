// PLAN 4.1d: snap markers are edit overlays (no shadows, even when the roles are set over the whole
// scene) and use no ISA-101 status color or one within 40 levels per channel of it.
import * as THREE from "three";
import { describe, expect, it } from "vitest";
import { ISA101 } from "../isa101";
import { applyShadowRoles } from "../lighting";
import { STATUS_MARGIN } from "./gizmo";
import { SNAP_MARKER_COLOR, SnapMarker } from "./markers";

const channels = (hex: number) => [(hex >> 16) & 255, (hex >> 8) & 255, hex & 255];
const tooClose = (a: number, b: number) => channels(a).every((v, i) => Math.abs(v - channels(b)[i]) <= STATUS_MARGIN);

describe("snap markers (PLAN 4.1d)", () => {
  it("cast and receive no shadows and use no status color", () => {
    const marker = new SnapMarker();
    marker.show([1, 0.8, 0], [1.05, 0.8, 0]);
    const scene = new THREE.Scene();
    scene.add(marker.object);
    applyShadowRoles(scene);
    let meshes = 0;
    marker.object.traverse((o) => {
      expect([o.castShadow, o.receiveShadow], o.name || o.type).toEqual([false, false]);
      const m = (o as THREE.Mesh).material as THREE.MeshBasicMaterial | undefined;
      if (!m) return;
      meshes += 1;
      expect(m.color.getHex()).toBe(SNAP_MARKER_COLOR);
      for (const [name, s] of Object.entries(ISA101)) expect(tooClose(m.color.getHex(), s), name).toBe(false);
    });
    expect(meshes).toBe(2);
    expect(marker.object.visible).toBe(true);
    marker.hide();
    expect(marker.object.visible).toBe(false);
  });
});
