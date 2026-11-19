import { describe, expect, it } from "vitest";
import * as THREE from "three";
import type { SceneDocument } from "../api";
import { DIM_OPACITY, dimForeign, dimObject, foreignFile, readOnlyMessage } from "./dim";

const doc = {
  components: [
    { id: "CV_101", type: "belt_conveyor", source: "areas/load.yaml", editable: false },
    { id: "CV_201", type: "belt_conveyor", source: "areas/wash.yaml", editable: true },
    { id: "PE_01", type: "photo_eye" }, // a plain scene: no source
  ],
} as unknown as SceneDocument;

describe("the editor on a plant (PLAN 5.4e)", () => {
  it("names the file of another file's component, and nothing for an editable one", () => {
    expect(foreignFile(doc, "CV_101")).toBe("areas/load.yaml");
    expect(foreignFile(doc, "CV_201")).toBeNull();
    expect(foreignFile(doc, "PE_01")).toBeNull();
    expect(foreignFile(null, "CV_101")).toBeNull();
    expect(readOnlyMessage("areas/load.yaml")).toBe("in areas/load.yaml: open that file to edit it");
  });

  it("dims a view with its own copies of its materials, leaving a shared one alone", () => {
    const shared = new THREE.MeshStandardMaterial({ color: 0x808080 });
    const mine = new THREE.Group();
    mine.add(new THREE.Mesh(new THREE.BoxGeometry(), shared));
    const theirs = new THREE.Group();
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(), shared);
    mesh.userData.lit = shared;
    theirs.add(mesh, new THREE.Line(new THREE.BufferGeometry(), new THREE.LineBasicMaterial()));
    const ids = dimForeign(doc, [{ id: "CV_201", object: mine }, { id: "CV_101", object: theirs }]);
    expect(ids).toEqual(["CV_101"]);
    const m = mesh.material as THREE.Material;
    expect(m).not.toBe(shared);
    expect(m.transparent && m.opacity === DIM_OPACITY && !m.depthWrite).toBe(true);
    expect((mesh.userData.lit as THREE.Material).opacity).toBe(DIM_OPACITY);
    expect(shared.opacity).toBe(1); // the editable component's material is untouched
    expect(((mine.children[0] as THREE.Mesh).material as THREE.Material).opacity).toBe(1);
    expect(dimObject(theirs)).toBe(0); // dimming twice changes nothing
    expect((mesh.material as THREE.Material).opacity).toBe(DIM_OPACITY);
  });
});
