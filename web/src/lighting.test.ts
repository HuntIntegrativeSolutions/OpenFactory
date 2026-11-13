// PLAN 4.14: status materials are exact at every level, only solid housings and parts cast
// shadows (a walk of the scene graph), the light rig per quality level, and the fitted shadow camera.
import * as THREE from "three";
import { describe, expect, it } from "vitest";
import type { ComponentGeometry } from "./api";
import { BUILDERS, buildComponent } from "./components";
import { dimmed } from "./components/devices";
import { ISA101 } from "./isa101";
import { TransformControls } from "three/addons/controls/TransformControls.js";
import { applyShadowRoles, buildStage, editOverlay, Lighting, LIT, LOW_LIGHTS, SHADOW, type RendererSettings } from "./lighting";
import { PartMeshes } from "./parts";

const ctx = { floorY: -0.75 };

/** Every status color in the style table (normalText and background are not status colors). */
const STATUS = new Set<number>([ISA101.running, ISA101.stopped, ISA101.alarmHigh, ISA101.alarmMedium, ISA101.highlight]);

/** Extra geometry so every type builds; the process types drawn in scene coordinates get points. */
const EXTRA: Record<string, Record<string, unknown>> = {
  pipe: { points: [[0, 0, 0], [0, 2, 0], [3, 2, 0]], diameter: 0.05 },
  valve_onoff: { position: [1, 0, 0], rotation_deg: 0, points: [[0, 0, 0], [1, 0, 0], [2, 0, 0]] },
  valve_control: { position: [1, 0, 0], rotation_deg: 0 },
  pump: { position: [1, 0.5, 0] },
  tank: { height: 2, diameter: 1, shape: "vertical_cylinder" },
  counter_display: { digits: 4, color: "red" },
  pilot_light: { color: "red" },
};

function geom(type: string): ComponentGeometry {
  return {
    id: `${type.toUpperCase()}_01`, type, description: type, position: [1, 0.2, -0.3], rotation_deg: 90,
    length: 2, width: 0.5, beam_length: 0.6, stroke: 0.5, size: [0.3, 1, 1],
    ...EXTRA[type],
  } as ComponentGeometry;
}

/** Everything that can make a component show a status color, at once. */
const ACTIVE = {
  running: true, detect: true, extending: true, enabled: true, lit: true, pressed: true, moving: true,
  turning: true, raising: true, gripping: true, target: true, flow_m3h: 1, overflowing: true,
  power_kw: 1, fault: true, opening: 1, text: "88.8", travel_m: 0.1, level_m: 1,
};

function meshes(root: THREE.Object3D): THREE.Mesh[] {
  const out: THREE.Mesh[] = [];
  root.traverse((o) => {
    if ((o as THREE.Mesh).isMesh) out.push(o as THREE.Mesh);
  });
  return out;
}

function colorOf(mesh: THREE.Mesh): number | null {
  const m = mesh.material as THREE.MeshBasicMaterial;
  return m.color ? m.color.getHex() : null;
}

describe("status materials (PLAN 4.14 item 1)", () => {
  it("every mesh showing a status color is unlit and not tone mapped, idle and active, for every type", () => {
    const shown = new Set<string>();
    for (const type of Object.keys(BUILDERS)) {
      const view = buildComponent(geom(type), ctx);
      for (const state of [{}, ACTIVE]) {
        view.update(state);
        for (const mesh of meshes(view.object)) {
          const hex = colorOf(mesh);
          if (hex === null || !STATUS.has(hex)) continue;
          // a released pushbutton's cap is the button's paint, shaded; pressed, it is a status
          // (ruled 2026-11-12; the test below)
          if (type === "pushbutton" && state !== ACTIVE) continue;
          const material = mesh.material as THREE.Material;
          expect(material, `${type} ${mesh.name || mesh.type}`).toBeInstanceOf(THREE.MeshBasicMaterial);
          expect(material.toneMapped, `${type} ${mesh.name}`).toBe(false);
          shown.add(type);
        }
      }
    }
    // the check is not empty: these types show a status color somewhere
    for (const type of ["belt_conveyor", "photo_eye", "pusher", "pilot_light", "stopper", "pump", "vfd_motor", "lift", "supply", "tank"]) {
      expect(shown, type).toContain(type);
    }
  });

  it("a lit pilot lamp is its color's hex; an unlit one is a dark tint that is not a status color", () => {
    const view = buildComponent(geom("pilot_light"), ctx);
    const lamp = meshes(view.object).find((m) => m.userData.status)!;
    expect(colorOf(lamp)).toBe(dimmed(ISA101.alarmHigh));
    expect(STATUS.has(dimmed(ISA101.alarmHigh))).toBe(false);
    view.update({ lit: true });
    expect(colorOf(lamp)).toBe(ISA101.alarmHigh);
    view.update({ lit: false });
    expect(colorOf(lamp)).toBe(dimmed(ISA101.alarmHigh));
  });

  it("an unlit lamp follows one rule for every color: sRGB(0.2 x linear(lit)), as docs/components.md lists", () => {
    const listed: Record<string, [number, number]> = {
      green: [0x22c55e, 0x0b5e29], red: [0xef4444, 0x731c1c], amber: [0xfacc15, 0x796105],
      white: [0xe5e7eb, 0x6e6f71], blue: [0x60a5fa, 0x2a4e79], black: [0x111827, 0x04060d],
    };
    const linear = (c: number) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
    const srgb = (l: number) => (l <= 0.0031308 ? 12.92 * l : 1.055 * l ** (1 / 2.4) - 0.055);
    const rule = (hex: number) =>
      [16, 8, 0].reduce((out, s) => out | (Math.round(255 * srgb(0.2 * linear(((hex >> s) & 255) / 255))) << s), 0);
    for (const [color, [lit, unlit]] of Object.entries(listed)) {
      const view = buildComponent({ ...geom("pilot_light"), color } as ComponentGeometry, ctx);
      const lamp = meshes(view.object).find((m) => m.userData.status)!;
      expect(colorOf(lamp), color).toBe(unlit);
      expect(rule(lit), color).toBe(unlit);
      view.update({ lit: true });
      expect(colorOf(lamp), color).toBe(lit);
    }
  });

  it("a pressed pushbutton shows its color exactly; released, its cap is shaded paint", () => {
    const view = buildComponent({ ...geom("pushbutton"), color: "green" } as ComponentGeometry, ctx);
    const cap = view.pickable[0] as THREE.Mesh;
    const paint = cap.material;
    expect(paint).toBeInstanceOf(THREE.MeshStandardMaterial);
    view.update({ pressed: true });
    expect(cap.material).toBeInstanceOf(THREE.MeshBasicMaterial);
    expect((cap.material as THREE.Material).toneMapped).toBe(false);
    expect(colorOf(cap)).toBe(ISA101.running);
    view.update({ pressed: false });
    expect(cap.material).toBe(paint);
  });

  it("equipment that shows a status color only while active keeps its lit material otherwise", () => {
    const view = buildComponent(geom("supply"), ctx);
    const pipe = meshes(view.object).find((m) => (m.material as THREE.Material).type === "MeshStandardMaterial" && colorOf(m) === 0x6b7280)!;
    const lit = pipe.material;
    view.update({ flow_m3h: 2 });
    expect(pipe.material).toBeInstanceOf(THREE.MeshBasicMaterial);
    expect(colorOf(pipe)).toBe(ISA101.running);
    view.update({ flow_m3h: 0 });
    expect(pipe.material).toBe(lit);
  });
});

describe("shadow roles (PLAN 4.14 item 2): a walk of the scene graph", () => {
  it("only solid housings and parts cast; outlines, beams, decals, lines, labels and helpers do neither", () => {
    const scene = new THREE.Scene();
    for (const type of Object.keys(BUILDERS)) {
      const view = buildComponent(geom(type), ctx);
      view.update(ACTIVE);
      applyShadowRoles(view.object);
      scene.add(view.object);
    }
    const parts = new PartMeshes();
    parts.update([{ id: "P000001", name: "box", size: [0.3, 0.2, 0.2], position: [0, 0.1, 0] } as never]);
    scene.add(parts.group);
    const { floor, grid } = buildStage(-0.75);
    scene.add(floor, grid);
    // a selection box as edit mode draws it (main.ts) and a label stand-in
    const selection = new THREE.BoxHelper(scene.children[0]);
    scene.add(selection, new THREE.Object3D());

    let solids = 0;
    scene.traverse((o) => {
      const mesh = o as THREE.Mesh;
      const where = `${o.parent?.name ?? ""}/${o.name || o.type}`;
      if (!mesh.isMesh) {
        expect(o.castShadow, where).toBe(false);
        expect(o.receiveShadow, where).toBe(false);
        return;
      }
      let marked = false;
      for (let p: THREE.Object3D | null = o; p; p = p.parent) marked ||= Boolean(p.userData.noShadow);
      const transparent = (mesh.material as THREE.Material).transparent;
      if (o === floor) {
        expect([o.castShadow, o.receiveShadow], "floor").toEqual([false, true]);
      } else if (marked || transparent) {
        expect([o.castShadow, o.receiveShadow], where).toEqual([false, false]);
      } else {
        expect(o.castShadow, where).toBe(true);
        expect(o.receiveShadow, where).toBe(!o.userData.status);
        solids += 1;
      }
    });
    expect(solids).toBeGreaterThan(50);

    // the named cases the task lists
    const named = (type: string, name: string) => scene.getObjectByName(`${type.toUpperCase()}_01`)!.getObjectByName(name)!;
    for (const [type, name] of [["photo_eye", "beam"], ["distance_sensor", "beam"]]) {
      expect([named(type, name).castShadow, named(type, name).receiveShadow], `${type} beam`).toEqual([false, false]);
    }
    for (const type of ["box_emitter", "part_remover"]) {
      for (const mesh of meshes(scene.getObjectByName(`${type.toUpperCase()}_01`)!)) {
        expect([mesh.castShadow, mesh.receiveShadow], `${type} outline`).toEqual([false, false]);
      }
    }
    expect(named("belt_conveyor", "belt").castShadow).toBe(true);
    expect(named("belt_conveyor", "belt").receiveShadow).toBe(false); // unlit status surface
    const part = parts.group.children[0] as THREE.Mesh;
    expect([part.castShadow, part.receiveShadow]).toEqual([true, true]); // opted in by parts.ts
    expect(part.children[0].castShadow).toBe(false); // its edge lines
    expect([grid.castShadow, grid.receiveShadow, selection.castShadow]).toEqual([false, false, false]);
  });

  it("in edit mode, the move and turn gizmo and the selection outlines cast and receive nothing", () => {
    // as main.ts's startEditor builds them (4.1c): the gizmo's helper, a box around one view and
    // a box around a composite instance's views; the roles are then set over the whole scene,
    // the worst case. The palette adds through a select and a button (no drag preview), and the
    // 0.05 m / 15 degree grid snap draws no markers, so these are all the overlays edit mode has.
    const scene = new THREE.Scene();
    const view = buildComponent(geom("belt_conveyor"), ctx);
    applyShadowRoles(view.object);
    scene.add(view.object);
    const element = { addEventListener() {}, removeEventListener() {}, style: {}, ownerDocument: { pointerLockElement: null } };
    const gizmo = new TransformControls(new THREE.PerspectiveCamera(), element as unknown as HTMLElement);
    const handle = new THREE.Object3D();
    scene.add(handle);
    gizmo.attach(handle);
    const highlight = new THREE.Color(ISA101.highlight);
    const overlays = [
      editOverlay(gizmo.getHelper()),
      editOverlay(new THREE.BoxHelper(view.object, highlight)),
      editOverlay(new THREE.Box3Helper(new THREE.Box3().setFromObject(view.object), highlight)),
    ];
    scene.add(...overlays);
    applyShadowRoles(scene);
    let checked = 0;
    for (const overlay of overlays) {
      overlay.traverse((o) => {
        expect([o.castShadow, o.receiveShadow], `${overlay.type}/${o.name || o.type}`).toEqual([false, false]);
        if ((o as THREE.Mesh).isMesh) checked += 1;
      });
    }
    expect(checked).toBeGreaterThan(10); // the gizmo's handles and pickers were walked
    // the plant under it keeps its roles
    expect(view.object.getObjectByName("belt")!.castShadow).toBe(true);
  });

  it("a bidirectional conveyor's direction arrow is a decal: no shadow", () => {
    const view = buildComponent({ ...geom("belt_conveyor"), bidirectional: true } as ComponentGeometry, ctx);
    applyShadowRoles(view.object);
    const arrow = view.object.getObjectByName("direction");
    expect(arrow).toBeDefined();
    expect([arrow!.castShadow, arrow!.receiveShadow]).toEqual([false, false]);
  });
});

/** A renderer stand-in with the settings a level touches. */
function fakeRenderer(): RendererSettings {
  return { toneMapping: THREE.NoToneMapping, toneMappingExposure: 1, shadowMap: { enabled: false, type: THREE.BasicShadowMap } };
}

describe("quality levels (PLAN 4.14 item 4)", () => {
  it("low is today's lighting: no tone mapping, no environment, no shadows, the old lights", () => {
    const renderer = fakeRenderer();
    const scene = new THREE.Scene();
    let made = 0;
    const lighting = new Lighting(renderer, scene, () => (made++, new THREE.Texture()));
    lighting.apply("low");
    expect(renderer.toneMapping).toBe(THREE.NoToneMapping);
    expect(scene.environment).toBeNull();
    expect(renderer.shadowMap.enabled).toBe(false);
    expect(lighting.sun.castShadow).toBe(false);
    expect(lighting.hemisphere.intensity).toBe(LOW_LIGHTS.hemisphere);
    expect(lighting.sun.intensity).toBe(LOW_LIGHTS.sun);
    expect(lighting.sun.color.getHex()).toBe(0xffffff);
    expect(lighting.sun.position.toArray()).toEqual([3, 8, 5]);
    expect(made).toBe(0); // low never builds the environment
  });

  it("standard adds tone mapping and the environment; high adds soft shadows; the environment is built once", () => {
    const renderer = fakeRenderer();
    const scene = new THREE.Scene();
    let made = 0;
    const lighting = new Lighting(renderer, scene, () => (made++, new THREE.Texture()));
    lighting.apply("standard");
    expect(renderer.toneMapping).toBe(THREE.ACESFilmicToneMapping);
    expect(scene.environment).not.toBeNull();
    expect(scene.environmentIntensity).toBe(LIT.environment);
    expect(renderer.shadowMap.enabled).toBe(false);
    lighting.apply("high");
    expect(renderer.shadowMap.enabled).toBe(true);
    expect(renderer.shadowMap.type).toBe(THREE.PCFShadowMap); // PCFSoftShadowMap is removed in r186
    expect(lighting.sun.castShadow).toBe(true);
    expect(lighting.sun.shadow.radius).toBe(SHADOW.radius);
    lighting.apply("low");
    expect(scene.environment).toBeNull();
    lighting.apply("standard");
    expect(made).toBe(1);
  });

  it("a level change marks every material for recompiling (tone mapping and shadows live in the shader)", () => {
    const scene = new THREE.Scene();
    const material = new THREE.MeshStandardMaterial();
    scene.add(new THREE.Mesh(new THREE.BoxGeometry(), material));
    const lighting = new Lighting(fakeRenderer(), scene, () => new THREE.Texture());
    lighting.apply("low");
    const before = material.version;
    lighting.apply("high");
    expect(material.version).toBeGreaterThan(before);
  });
});

describe("fitted shadow camera (PLAN 4.14 item 3)", () => {
  /** Whether every corner of `box` is inside the sun's shadow camera. */
  function covered(lighting: Lighting, box: THREE.Box3): boolean {
    const cam = lighting.sun.shadow.camera;
    lighting.sun.updateMatrixWorld();
    lighting.sun.target.updateMatrixWorld();
    cam.position.copy(lighting.sun.position);
    cam.lookAt(lighting.sun.target.position);
    cam.updateMatrixWorld();
    cam.updateProjectionMatrix();
    for (const x of [box.min.x, box.max.x]) for (const y of [box.min.y, box.max.y]) for (const z of [box.min.z, box.max.z]) {
      const p = new THREE.Vector3(x, y, z).project(cam);
      if (Math.abs(p.x) > 1 || Math.abs(p.y) > 1 || p.z < -1 || p.z > 1) return false;
    }
    return true;
  }

  it("covers a small cell and a 40 m line alike", () => {
    for (const box of [
      new THREE.Box3(new THREE.Vector3(-1, -0.75, -0.5), new THREE.Vector3(3, 0.5, 0.5)),
      new THREE.Box3(new THREE.Vector3(-20, -0.75, -8), new THREE.Vector3(20, 1.5, 8)),
    ]) {
      const lighting = new Lighting(fakeRenderer(), new THREE.Scene(), () => new THREE.Texture());
      lighting.apply("high");
      lighting.fit(box);
      expect(covered(lighting, box), JSON.stringify(box)).toBe(true);
    }
  });

  it("a fixed 12 m box (the reverted spike's) would not cover the 40 m line", () => {
    const lighting = new Lighting(fakeRenderer(), new THREE.Scene(), () => new THREE.Texture());
    lighting.apply("high");
    const line = new THREE.Box3(new THREE.Vector3(-20, -0.75, -8), new THREE.Vector3(20, 1.5, 8));
    lighting.fit(line);
    const cam = lighting.sun.shadow.camera;
    Object.assign(cam, { left: -6, right: 6, top: 6, bottom: -6 });
    expect(covered(lighting, line)).toBe(false);
  });
});
