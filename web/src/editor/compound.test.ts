import { describe, expect, it } from "vitest";
import { addEntry, addItem, Drafts, keyProblem, removeEntry, removeItem, renameEntry, type Entries } from "./compound";

describe("list edits (PLAN 4.1j)", () => {
  it("adds at the end and removes by place, leaving the original alone", () => {
    const parts = [{ name: "a" }, { name: "b" }];
    const added = addItem(parts, { name: "c" });
    expect(added.map((p) => (p as { name: string }).name)).toEqual(["a", "b", "c"]);
    expect(removeItem(added, 1).map((p) => (p as { name: string }).name)).toEqual(["a", "c"]);
    expect(parts).toHaveLength(2);
  });
});

describe("mapping edits (PLAN 4.1j)", () => {
  const reached: Entries = [["REM_01", "oversize"], ["REM_02", "good"]];

  it("renames an entry in its place", () => {
    const r = renameEntry(reached, 0, "REM_09");
    expect(r.problem).toBeNull();
    expect(r.entries).toEqual([["REM_09", "oversize"], ["REM_02", "good"]]);
    expect(renameEntry(reached, 0, "REM_01")).toEqual({ entries: reached, problem: null }); // its own name
  });

  it("refuses a rename to a key another entry has, which would drop that entry (fix 2026-11-14)", () => {
    const r = renameEntry(reached, 0, "REM_02");
    expect(r.problem).toBe("REM_02 is already a key here");
    expect(r.entries).toBe(reached);
    // what refusing prevents: written as a mapping, the two entries would become one
    expect(Object.keys(Object.fromEntries([["REM_02", "oversize"], ["REM_02", "good"]]))).toHaveLength(1);
  });

  it("refuses a rename to an empty key (fix 2026-11-14)", () => {
    expect(renameEntry(reached, 1, "  ").problem).toBe("a key cannot be empty");
    expect(keyProblem(reached, "")).toBe("a key cannot be empty");
  });

  it("adds a new key at the end and refuses a duplicate or an empty one", () => {
    expect(addEntry(reached, "REM_03", "").entries.map(([k]) => k)).toEqual(["REM_01", "REM_02", "REM_03"]);
    expect(addEntry(reached, "REM_01", "").problem).toBe("REM_01 is already a key here");
    expect(addEntry(reached, "", "").problem).toBe("a key cannot be empty");
  });

  it("removes an entry by place", () => {
    expect(removeEntry(reached, 0)).toEqual([["REM_02", "good"]]);
  });
});

describe("drafts: a list still being filled in survives a redraw (fix 2026-11-14)", () => {
  it("shows the draft instead of the file's value until it is sent or cleared", () => {
    const drafts = new Drafts();
    const file = [{ name: "box", size: [0.3, 0.3, 0.3], weight: 1 }];
    const half = [...file, { name: "good", size: [NaN, NaN, NaN], weight: NaN }];
    expect(drafts.shown("components.EMT_01.parts", file)).toBe(file);
    drafts.keep("components.EMT_01.parts", half);
    // a redraw after another field's preview: the half-filled item is still there
    const shown = drafts.shown("components.EMT_01.parts", file) as typeof half;
    expect(shown).toHaveLength(2);
    expect(shown[1].name).toBe("good");
    expect(shown).not.toBe(half); // a copy: the control's edits do not reach the draft unsent
    drafts.clear("components.EMT_01.parts");
    expect(drafts.shown("components.EMT_01.parts", file)).toBe(file);
  });

  it("clears a deleted component's drafts and only those", () => {
    const drafts = new Drafts();
    drafts.keep("components.EMT_01.parts", []);
    drafts.keep("components.EMT_011.parts", []);
    drafts.keep("checks.parts_reached", {});
    drafts.clearUnder("components.EMT_01");
    expect(drafts.has("components.EMT_01.parts")).toBe(false);
    expect(drafts.has("components.EMT_011.parts")).toBe(true);
    drafts.clearUnder();
    expect(drafts.size).toBe(0);
  });
});
