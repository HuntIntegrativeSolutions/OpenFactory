import { describe, expect, it } from "vitest";
import * as THREE from "three";
import type { SceneDocument } from "./api";
import { aimPlan, grow, linkTouches, lookTargets, panelsShown, parseView, planFrame, scaleBar, viewParam } from "./views";

describe("?view= (PLAN 5.14c)", () => {
  it("takes six finite numbers or the word top", () => {
    const pose = parseView("1,2,3,0,0.5,-1");
    expect(pose.kind).toBe("pose");
    if (pose.kind === "pose") {
      expect(pose.position.toArray()).toEqual([1, 2, 3]);
      expect(pose.target.toArray()).toEqual([0, 0.5, -1]);
    }
    expect(parseView(" 1 , 2 ,3,4,5,6 ").kind).toBe("pose");
    expect(parseView("top")).toEqual({ kind: "top" });
    expect(parseView(null)).toEqual({ kind: "default", warning: null });
  });

  it("falls back to the default view with a warning for anything else, never throwing", () => {
    for (const raw of ["", "TOP", "side", "1,2,3", "1,2,3,4,5,6,7", "1,2,3,4,5,x", "1,2,,4,5,6",
      "NaN,1,2,3,4,5", "Infinity,1,2,3,4,5", "1e999,0,0,0,0,0", "1,1,1,1,1,1", "<script>"]) {
      const v = parseView(raw);
      expect(v.kind, raw).toBe("default");
      expect(v.kind === "default" && v.warning, raw).toContain("the default view is shown");
    }
  });
});

const doc = {
  components: [
    { id: "CONV_01", type: "belt_conveyor" },
    { id: "CONV_02", type: "belt_conveyor" },
    { id: "CYL_01", type: "pusher" },
    { id: "TK_01", type: "tank" },
    { id: "PIPE_01", type: "pipe", from: "TK_01.outlet", to: "P_01" },
    { id: "P_01", type: "pump" },
    { id: "LT_01", type: "transmitter", measures: "TK_01.level" },
    { id: "ACC_01.belt", type: "belt_conveyor", composite: { id: "ACC_01" } },
    { id: "ACC_01.eye", type: "photo_eye", composite: { id: "ACC_01" } },
  ],
  connections: [{ from: "CONV_01", to: "CONV_02" }, { from: "CONV_02", to: "ACC_01" }],
} as unknown as SceneDocument;

describe("?look= (PLAN 5.14c)", () => {
  it("frames the component and what touches it", () => {
    expect(lookTargets(doc, "CYL_01")).toEqual(["CYL_01"]);
    expect(lookTargets(doc, "CONV_01")).toEqual(["CONV_01", "CONV_02"]);
    expect(lookTargets(doc, "CONV_02")).toEqual(["CONV_01", "CONV_02", "ACC_01.belt", "ACC_01.eye"]);
    expect(lookTargets(doc, "TK_01")).toEqual(["TK_01", "PIPE_01", "LT_01"]);
    expect(lookTargets(doc, "PIPE_01")).toEqual(["TK_01", "PIPE_01", "P_01"]);
    expect(lookTargets(doc, "ACC_01")).toEqual(["CONV_02", "ACC_01.belt", "ACC_01.eye"]);
    expect(lookTargets(doc, "NOPE")).toEqual([]);
  });

  it("finds what a link's drawn ends touch, as the scene document carries no from and to", () => {
    const plant = {
      components: [
        { id: "TK_201", type: "tank" },
        { id: "XV_201", type: "valve_onoff", points: [[3, 2, -2], [3.6, 2, -2], [4.5, 1.2, -2]] },
        { id: "P_201", type: "pump", points: [[4.9, 0.05, -2], [5.2, 0.25, -2], [6, 0.25, -2]] },
        { id: "DR_201", type: "drain" },
        { id: "CV_201", type: "belt_conveyor" },
      ],
      connections: [],
    } as unknown as SceneDocument;
    const box = (a: number[], b: number[]) => new THREE.Box3(new THREE.Vector3(...a), new THREE.Vector3(...b));
    const boxes = new Map([
      ["TK_201", box([4.4, 0, -2.6], [5.6, 1.2, -1.4])],
      ["XV_201", box([2.9, 1.1, -2.1], [4.6, 2.1, -1.9])],
      ["P_201", box([4.8, 0, -2.1], [6.1, 0.4, -1.9])],
      ["DR_201", box([5.96, 0, -2.2], [6.3, 0.3, -1.8])],
      ["CV_201", box([0, 0, -0.3], [6, 0.8, 0.3])],
    ]);
    const touches = linkTouches(plant, boxes);
    expect(lookTargets(plant, "TK_201", touches)).toEqual(["TK_201", "XV_201", "P_201"]);
    expect(lookTargets(plant, "P_201", touches)).toEqual(["TK_201", "P_201", "DR_201"]);
    expect(lookTargets(plant, "CV_201", touches)).toEqual(["CV_201"]);
  });

  it("grows the box 30 % about its centre", () => {
    const box = grow(new THREE.Box3(new THREE.Vector3(0, 0, 0), new THREE.Vector3(2, 1, 4)));
    expect(box.min.toArray().map((v) => +v.toFixed(6))).toEqual([-0.3, -0.15, -0.6]);
    expect(box.max.toArray().map((v) => +v.toFixed(6))).toEqual([2.3, 1.15, 4.6]);
  });
});

describe("the plan view (PLAN 5.14c)", () => {
  it("looks straight down with north (-z) up and +x right, the same scale everywhere", () => {
    const bounds = new THREE.Box3(new THREE.Vector3(-5, 0, -1), new THREE.Vector3(5, 2, 1));
    const camera = new THREE.OrthographicCamera();
    const aspect = 16 / 9;
    aimPlan(camera, planFrame(bounds, aspect), aspect, 2, 0);
    camera.updateMatrixWorld();
    const px = (x: number, y: number, z: number) => {
      const p = new THREE.Vector3(x, y, z).project(camera);
      return [((p.x + 1) / 2) * 1280, ((1 - p.y) / 2) * 720];
    };
    const [x0, y0] = px(0, 0, 0);
    expect(px(1, 0, 0)[0]).toBeGreaterThan(x0); // +x to the right
    expect(px(0, 0, -1)[1]).toBeLessThan(y0); // -z (north) up
    const a = px(-5, 0, 0)[0] - px(-4, 0, 0)[0];
    const b = px(4, 2, 0)[0] - px(5, 2, 0)[0]; // the other end, and higher up
    expect(Math.abs(a - b)).toBeLessThan(1e-6);
    for (const x of [-5, 5]) for (const z of [-1, 1]) {
      const [sx, sy] = px(x, 0, z);
      expect(sx >= 0 && sx <= 1280 && sy >= 0 && sy <= 720).toBe(true);
    }
  });

  it("chooses a scale bar of 1, 2 or 5 times a power of ten", () => {
    expect(scaleBar(100)).toEqual({ metres: 1, px: 100, label: "1 m" });
    expect(scaleBar(30).label).toBe("5 m");
    expect(scaleBar(50).label).toBe("2 m");
    expect(scaleBar(1000).label).toBe("100 mm");
    expect(scaleBar(3).label).toBe("50 m");
  });

  it("copies the view as a ?view= value", () => {
    expect(viewParam(true, new THREE.Vector3(), new THREE.Vector3())).toBe("view=top");
    const p = viewParam(false, new THREE.Vector3(1.23456, 2, -3), new THREE.Vector3(0, 0.5, 0));
    expect(p).toBe("view=1.235,2,-3,0,0.5,0");
    expect(parseView(p.slice(5)).kind).toBe("pose");
  });
});

describe("?panels=off (a picture without the panels, ruled 2026-11-18)", () => {
  it("hides the panels only for off, and says so for a value it does not know", () => {
    expect(panelsShown(null)).toEqual({ shown: true, warning: null });
    expect(panelsShown("off")).toEqual({ shown: false, warning: null });
    expect(panelsShown("on")).toEqual({ shown: true, warning: null });
    const bad = panelsShown("none");
    expect(bad.shown).toBe(true);
    expect(bad.warning).toContain("?panels=none");
  });
});
