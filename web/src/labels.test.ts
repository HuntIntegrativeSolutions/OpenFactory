import { describe, expect, it } from "vitest";
import type { SceneDocument } from "./api";
import { boundTags, formatValue, labelLines, MAX_VALUE_LABELS, parseLabels, showsValues, stackLabels, type LabelRect } from "./labels";

const doc = {
  tags: [
    { name: "CYL_01_EXT_DO", kind: "DO", dtype: "bool", owner: "CYL_01", description: "" },
    { name: "PE_01_DI", kind: "DI", dtype: "bool", owner: "PE_01", description: "" },
    { name: "CYL_01_EXT_DI", kind: "DI", dtype: "bool", owner: "CYL_01", description: "" },
    { name: "LT_01_AI", kind: "AI", dtype: "float32", owner: "LT_01", description: "" },
  ],
} as unknown as SceneDocument;

describe("labels (PLAN 5.14d)", () => {
  it("reads ?labels=, ids by default, a wrong value ids with a warning", () => {
    expect(parseLabels(null)).toEqual({ mode: "ids", warning: null });
    expect(parseLabels("tags")).toEqual({ mode: "tags", warning: null });
    expect(parseLabels("off")).toEqual({ mode: "off", warning: null });
    const bad = parseLabels("values");
    expect(bad.mode).toBe("ids");
    expect(bad.warning).toContain("?labels=values");
  });

  it("finds each component's own tags in the document's order", () => {
    const tags = boundTags(doc);
    expect(tags.get("CYL_01")?.map((t) => t.name)).toEqual(["CYL_01_EXT_DO", "CYL_01_EXT_DI"]);
    expect(tags.get("NOPE")).toBeUndefined();
  });

  it("shows a value as /api/state gives it, and none it does not have", () => {
    expect(formatValue(true)).toBe("true");
    expect(formatValue(false)).toBe("false");
    expect(formatValue(12)).toBe("12");
    expect(formatValue(1.23456)).toBe("1.235");
    expect(formatValue(0.5)).toBe("0.5");
    expect(formatValue(undefined)).toBe("–");
    expect(formatValue(null)).toBe("–");
  });

  it("writes the id, then a line per tag; off shows only a fault", () => {
    const tags = boundTags(doc).get("CYL_01")!;
    const io = { CYL_01_EXT_DO: true };
    expect(labelLines("ids", "CYL_01", tags, io, null)).toEqual(["CYL_01"]);
    expect(labelLines("tags", "CYL_01", tags, io, null)).toEqual(["CYL_01", "CYL_01_EXT_DO true", "CYL_01_EXT_DI –"]);
    expect(labelLines("tags", "CYL_01", tags, io, "stuck_retracted")).toEqual(["CYL_01 ⚠ stuck retracted", "CYL_01_EXT_DO true", "CYL_01_EXT_DI –"]);
    expect(labelLines("tags", "CYL_01", tags, io, null, false)).toEqual(["CYL_01"]);
    expect(labelLines("off", "CYL_01", tags, io, null)).toBeNull();
    expect(labelLines("off", "CYL_01", tags, io, "jam")).toEqual(["CYL_01 ⚠ jam"]);
  });

  it("past 40 components shows values only for the selected component and its neighbours", () => {
    const focus = new Set(["CYL_01", "CONV_01"]);
    expect(showsValues(MAX_VALUE_LABELS, "PE_01", focus)).toBe(true);
    expect(showsValues(MAX_VALUE_LABELS + 1, "PE_01", focus)).toBe(false);
    expect(showsValues(MAX_VALUE_LABELS + 1, "CONV_01", focus)).toBe(true);
  });

  it("stacks labels that would overlap and leaves the rest where they are", () => {
    const rects: LabelRect[] = [
      { id: "FAN_101_HOA", left: 100, top: 200, width: 90, height: 18 },
      { id: "FAN_101_BODY", left: 40, top: 200, width: 100, height: 18 },
      { id: "FAR", left: 600, top: 200, width: 60, height: 18 },
      { id: "BELOW", left: 60, top: 221, width: 80, height: 18 }, // overlaps the second once stacked
    ];
    const shift = stackLabels(rects);
    expect(shift.get("FAN_101_BODY")).toBe(0); // further left: placed first
    expect(shift.get("FAN_101_HOA")).toBe(20); // 18 high + 2 px gap
    expect(shift.get("FAR")).toBe(0);
    const placed = rects.map((r) => ({ ...r, top: r.top + shift.get(r.id)! }));
    for (const a of placed) for (const b of placed) {
      if (a === b) continue;
      const apart = a.left + a.width <= b.left || b.left + b.width <= a.left || a.top + a.height <= b.top || b.top + b.height <= a.top;
      expect(apart, `${a.id} / ${b.id}`).toBe(true);
    }
    // the same rects in another order stack the same way
    expect(stackLabels([...rects].reverse())).toEqual(shift);
  });
});
