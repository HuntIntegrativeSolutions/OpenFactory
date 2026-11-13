// PLAN 4.1c fix 2: no gizmo handle uses an ISA-101 color or one within STATUS_MARGIN levels per
// channel of one.
import * as THREE from "three";
import { TransformControls } from "three/addons/controls/TransformControls.js";
import { describe, expect, it } from "vitest";
import { ISA101 } from "../isa101";
import { GIZMO_COLORS, gizmoColors, recolorGizmo, STATUS_MARGIN } from "./gizmo";

const element = { addEventListener() {}, removeEventListener() {}, style: {}, ownerDocument: { pointerLockElement: null } };

function controls(): TransformControls {
  const c = new TransformControls(new THREE.PerspectiveCamera(), element as unknown as HTMLElement);
  c.attach(new THREE.Object3D());
  return c;
}

const channels = (hex: number) => [(hex >> 16) & 255, (hex >> 8) & 255, hex & 255];
/** True when every channel of `a` is within STATUS_MARGIN of `b`'s: too close to tell apart. */
const tooClose = (a: number, b: number) => channels(a).every((v, i) => Math.abs(v - channels(b)[i]) <= STATUS_MARGIN);
const hex = (n: number) => `#${n.toString(16).padStart(6, "0")}`;

describe("gizmo colors (PLAN 4.1c fix 2)", () => {
  it("three's own gizmo does use status-like colors, so the check below has teeth", () => {
    const close = gizmoColors(controls()).filter((g) => Object.values(ISA101).some((s) => tooClose(g, s)));
    expect(close.map(hex)).toContain("#ffffff"); // within 40 of normalText #E5E7EB
  });

  it("after recoloring, every gizmo color keeps more than 40 levels in some channel from every ISA-101 color", () => {
    const c = controls();
    recolorGizmo(c);
    const colors = gizmoColors(c);
    expect(new Set(colors)).toEqual(new Set(Object.values(GIZMO_COLORS))); // the table is all of them
    for (const g of colors) {
      expect(Object.values(GIZMO_COLORS), hex(g)).toContain(g);
      for (const [name, s] of Object.entries(ISA101)) {
        expect(tooClose(g, s), `${hex(g)} vs ${name} ${hex(s)}`).toBe(false);
      }
    }
  });

  it("every mode's handles are recolored (move, turn and the modes the editor hides)", () => {
    const c = controls();
    recolorGizmo(c);
    for (const mode of ["translate", "rotate", "scale"] as const) {
      c.setMode(mode);
      c.getHelper().updateMatrixWorld(true);
      for (const g of gizmoColors(c)) expect(Object.values(GIZMO_COLORS), `${mode} ${hex(g)}`).toContain(g);
    }
  });
});
