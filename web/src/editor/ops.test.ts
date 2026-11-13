import { describe, expect, it } from "vitest";
import { EditOps } from "./ops";

const eye = { id: "PE_02", type: "photo_eye", description: "New photo eye", position: [1, 0.1, 0] };
const acc = { id: "ACC_01", type: "accumulation_conveyor", description: "New accumulation conveyor", position: [0, 0, 4] };

describe("EditOps (PLAN 4.1c)", () => {
  it("replaces a later change to the same value in place", () => {
    const ops = new EditOps();
    ops.change({ op: "set", path: ["components", "CONV_01", "speed_mps"], value: 0.6 });
    ops.change({ op: "set", path: ["components", "CONV_01", "length"], value: 3 });
    ops.change({ op: "set", path: ["components", "CONV_01", "speed_mps"], value: 0.7 });
    expect(ops.list()).toEqual([
      { op: "set", path: ["components", "CONV_01", "speed_mps"], value: 0.7 },
      { op: "set", path: ["components", "CONV_01", "length"], value: 3 },
    ]);
  });

  it("adds a library composite after its use_library, once, and holds it undescribed", () => {
    const ops = new EditOps();
    ops.add(acc, "accumulation_conveyor");
    ops.add({ ...acc, id: "ACC_02" }, "accumulation_conveyor");
    expect(ops.list().map((o) => o.op)).toEqual(["use_library", "add", "add"]);
    expect([...ops.undescribed]).toEqual(["ACC_01", "ACC_02"]);
    ops.change({ op: "set", path: ["components", "ACC_01", "description"], value: "New accumulation conveyor" });
    expect(ops.undescribed.has("ACC_01")).toBe(true); // the placeholder is not a description
    ops.change({ op: "set", path: ["components", "ACC_01", "description"], value: "Buffer Conveyor" });
    expect(ops.undescribed.has("ACC_01")).toBe(false);
  });

  it("takes back a new component's add and changes when it is deleted, and its library when unused", () => {
    const ops = new EditOps();
    ops.add(acc, "accumulation_conveyor");
    ops.add(eye, null);
    ops.change({ op: "set", path: ["components", "ACC_01", "position"], value: [1, 0, 4] });
    ops.remove("ACC_01");
    expect(ops.list()).toEqual([{ op: "add", component: eye }]);
    expect(ops.addedIds()).toEqual(["PE_02"]);
    expect(ops.undescribed.has("ACC_01")).toBe(false);
  });

  it("deletes a component in the file with a delete, dropping its pending changes", () => {
    const ops = new EditOps();
    ops.change({ op: "set", path: ["components", "PE_01", "beam_length"], value: 0.7 });
    ops.change({ op: "set", path: ["components", "CONV_01", "length"], value: 3 });
    ops.remove("PE_01");
    expect(ops.list()).toEqual([
      { op: "set", path: ["components", "CONV_01", "length"], value: 3 },
      { op: "delete", id: "PE_01" },
    ]);
  });

  it("keeps one connect per pair a snap made, and drops it with either end (PLAN 4.1d)", () => {
    const ops = new EditOps();
    ops.connect("CONV_01", "CONV_02");
    ops.connect("CONV_01", "CONV_02");
    ops.connect("CONV_02", "CONV_03");
    expect(ops.list()).toEqual([
      { op: "connect", from: "CONV_01", to: "CONV_02" },
      { op: "connect", from: "CONV_02", to: "CONV_03" },
    ]);
    ops.remove("CONV_03");
    expect(ops.list()).toEqual([
      { op: "connect", from: "CONV_01", to: "CONV_02" },
      { op: "delete", id: "CONV_03" },
    ]);
  });
});
