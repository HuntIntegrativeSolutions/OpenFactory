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

  it("sends unreference after the delete it goes with (PLAN 4.1f)", () => {
    const ops = new EditOps();
    ops.remove("TK_01");
    ops.unreference(["TK_01"]);
    expect(ops.list()).toEqual([
      { op: "delete", id: "TK_01" },
      { op: "unreference", ids: ["TK_01"] },
    ]);
  });

  it("holds a placeholder description from the file until it is replaced (2026-11-13)", () => {
    const ops = new EditOps();
    ops.holdPlaceholder("CONV_01", "New belt conveyor");
    ops.holdPlaceholder("scene", "New scene");
    expect([...ops.undescribed]).toEqual(["CONV_01", "scene"]);
    ops.change({ op: "set", path: ["components", "CONV_01", "description"], value: "New belt conveyor" });
    expect(ops.undescribed.has("CONV_01")).toBe(true);
    ops.change({ op: "set", path: ["components", "CONV_01", "description"], value: "Infeed Belt Conveyor" });
    ops.change({ op: "set", path: ["scene", "description"], value: "A line" });
    expect([...ops.undescribed]).toEqual([]);
    ops.holdPlaceholder("PE_01", "New photo eye");
    ops.remove("PE_01"); // deleted: nothing left to describe
    expect([...ops.undescribed]).toEqual([]);
  });
});

describe("the laid_out mark (PLAN 5.5, ruled 2026-11-19 second set)", () => {
  const laid = new Set(["P_101"]);
  it("moving or re-typing a laid-out component's position clears its mark", () => {
    const ops = new EditOps();
    ops.laidOut = (id) => laid.has(id);
    ops.change({ op: "set", path: ["components", "P_101", "position"], value: [1, 0.3, 0] });
    expect(ops.list()).toEqual([
      { op: "set", path: ["components", "P_101", "position"], value: [1, 0.3, 0] },
      { op: "unset", path: ["components", "P_101", "laid_out"] },
    ]);
    const typed = new EditOps();
    typed.laidOut = (id) => laid.has(id);
    typed.change({ op: "set", path: ["components", "P_101", "position", 0], value: 2 });
    expect(typed.list()).toContainEqual({ op: "unset", path: ["components", "P_101", "laid_out"] });
  });

  it("leaves a component that is not laid out, and other keys, alone", () => {
    const ops = new EditOps();
    ops.laidOut = (id) => laid.has(id);
    ops.change({ op: "set", path: ["components", "TK_101", "position"], value: [0, 0, 0] });
    ops.change({ op: "set", path: ["components", "P_101", "kv"], value: 3 });
    expect(ops.list()).toEqual([
      { op: "set", path: ["components", "TK_101", "position"], value: [0, 0, 0] },
      { op: "set", path: ["components", "P_101", "kv"], value: 3 },
    ]);
  });

  it("the author may still set the mark by hand, after a move too", () => {
    const ops = new EditOps();
    ops.laidOut = (id) => laid.has(id);
    ops.change({ op: "set", path: ["components", "P_101", "position"], value: [1, 0.3, 0] });
    ops.change({ op: "set", path: ["components", "P_101", "laid_out"], value: true });
    expect(ops.list()).toEqual([
      { op: "set", path: ["components", "P_101", "position"], value: [1, 0.3, 0] },
      { op: "set", path: ["components", "P_101", "laid_out"], value: true },
    ]);
  });
});

describe("the laid_out mark, review of 2026-11-20 (gaps 2b and 2d)", () => {
  const laid = new Set(["P_101", "PE_201"]);
  const file: Record<string, number[]> = { P_101: [1, 0.3, 0], PE_201: [1.2, 0.1, 2.5] };
  const fresh = (): EditOps => {
    const ops = new EditOps();
    ops.laidOut = (id) => laid.has(id);
    ops.fileValue = (path) => (path[2] === "position" ? file[String(path[1])] : undefined);
    return ops;
  };

  it("a move undone before saving (moved back to the file's position) keeps the mark", () => {
    const ops = fresh();
    ops.change({ op: "set", path: ["components", "P_101", "position"], value: [4, 0.3, 0] });
    expect(ops.list()).toContainEqual({ op: "unset", path: ["components", "P_101", "laid_out"] });
    ops.change({ op: "set", path: ["components", "P_101", "position"], value: [1, 0.3, 0] });
    expect(ops.list()).toEqual([]);
  });

  it("typed axis by axis away and back, the same", () => {
    const ops = fresh();
    ops.change({ op: "set", path: ["components", "P_101", "position", 0], value: 20 });
    expect(ops.list()).toContainEqual({ op: "unset", path: ["components", "P_101", "laid_out"] });
    ops.change({ op: "set", path: ["components", "P_101", "position", 0], value: 1 });
    expect(ops.list()).toEqual([]);
  });

  it("moving back does not undo a mark the person cleared with the checkbox", () => {
    const ops = fresh();
    ops.change({ op: "set", path: ["components", "P_101", "position"], value: [4, 0.3, 0] });
    ops.change({ op: "set", path: ["components", "P_101", "laid_out"], value: false });
    ops.change({ op: "set", path: ["components", "P_101", "position"], value: [1, 0.3, 0] });
    expect(ops.list()).toEqual([{ op: "set", path: ["components", "P_101", "laid_out"], value: false }]);
  });

  it("a device carried with its belt keeps its mark", () => {
    const ops = fresh();
    ops.change({ op: "set", path: ["components", "PE_201", "position"], value: [3.2, 0.1, 2.5] }, { keepMark: true });
    expect(ops.list()).toEqual([{ op: "set", path: ["components", "PE_201", "position"], value: [3.2, 0.1, 2.5] }]);
  });
});

describe("a belt moved and moved back carries nothing into the file (review of 2026-11-20, second set, 1c)", () => {
  const file: Record<string, Record<string, unknown>> = {
    CV_201: { position: [0, 0, 2.5], laid_out: true },
    CYL_201: { position: [2, 0.15, 2.2], rotation_deg: 0, laid_out: true },
  };
  const fresh = (): EditOps => {
    const ops = new EditOps();
    ops.laidOut = (id) => Boolean(file[id]?.laid_out);
    ops.fileValue = (path) => file[String(path[1])]?.[String(path[2])];
    return ops;
  };

  it("a set back to the value the file has is no change, for any key", () => {
    const ops = fresh();
    ops.change({ op: "set", path: ["components", "CYL_201", "rotation_deg"], value: 90 });
    expect(ops.size).toBe(1);
    ops.change({ op: "set", path: ["components", "CYL_201", "rotation_deg"], value: 0 });
    expect(ops.list()).toEqual([]);
  });

  it("the belt and its carried pusher, moved and carried back, leave no op", () => {
    const ops = fresh();
    ops.change({ op: "set", path: ["components", "CV_201", "position"], value: [1, 0, 2.5] });
    ops.change({ op: "set", path: ["components", "CYL_201", "position"], value: [3, 0.15, 2.2] }, { keepMark: true });
    ops.change({ op: "set", path: ["components", "CYL_201", "rotation_deg"], value: 0 });
    ops.change({ op: "set", path: ["components", "CV_201", "position"], value: [0, 0, 2.5] });
    ops.change({ op: "set", path: ["components", "CYL_201", "position"], value: [2, 0.15, 2.2 + 1e-12] }, { keepMark: true });
    ops.change({ op: "set", path: ["components", "CYL_201", "rotation_deg"], value: 0 });
    expect(ops.list()).toEqual([]);
  });
});

describe("the set-back rule keeps the rules it sits beside (review of 2026-11-20, second set, 1c)", () => {
  it("a placeholder description typed back is held again, so nothing saves", () => {
    const ops = new EditOps();
    ops.fileValue = (path) => (path[2] === "description" ? "Describe this conveyor" : undefined);
    ops.holdPlaceholder("CV_9", "Describe this conveyor");
    ops.change({ op: "set", path: ["components", "CV_9", "description"], value: "Infeed Belt Conveyor" });
    expect(ops.undescribed.has("CV_9")).toBe(false);
    ops.change({ op: "set", path: ["components", "CV_9", "description"], value: "Describe this conveyor" });
    expect(ops.list()).toEqual([]);
    expect(ops.undescribed.has("CV_9")).toBe(true);
  });

  it("a mark a move cleared, ticked again, is no change when the file has it", () => {
    const ops = new EditOps();
    ops.laidOut = () => true;
    ops.fileValue = (path) => (path[2] === "laid_out" ? true : path[2] === "position" ? [1, 0, 0] : undefined);
    ops.change({ op: "set", path: ["components", "TK_1", "position"], value: [4, 0, 0] });
    expect(ops.list()).toContainEqual({ op: "unset", path: ["components", "TK_1", "laid_out"] });
    ops.change({ op: "set", path: ["components", "TK_1", "laid_out"], value: true });
    expect(ops.list()).toEqual([{ op: "set", path: ["components", "TK_1", "position"], value: [4, 0, 0] }]);
  });
});
