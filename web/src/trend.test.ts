import { describe, expect, test } from "vitest";
import { aiTags, label, polyline, xSpan, yRange, type History } from "./trend";

const history = (t: number[], v: number[], range: [number, number] | null = [0, 2]): History => ({
  tag: "TK_01_LT_AI", units: "m", range, period_s: 0.5, window_s: 600, t, v,
});

describe("trend panel (PLAN 3.6)", () => {
  test("offers the scene's AI tags only, in scene order", () => {
    const tags = [
      { name: "TK_01_LSH_DI", kind: "DI" }, { name: "FV_01_ZT_AI", kind: "AI" },
      { name: "FV_01_POS_AO", kind: "AO" }, { name: "TK_01_LT_AI", kind: "AI" },
    ].map((t) => ({ ...t, dtype: "float32", owner: "X", description: "" }));
    expect(aiTags({ tags })).toEqual(["FV_01_ZT_AI", "TK_01_LT_AI"]);
  });

  test("the y axis fits the samples, with a floor of 1 % of the tag's range", () => {
    expect(yRange(history([0, 0.5], [0.9, 1.1]))).toEqual([0.88, 1.12]);
    const [lo, hi] = yRange(history([0, 0.5], [1, 1])); // steady: 1 % of the 2 m range each side
    expect([lo, hi]).toEqual([0.98, 1.02]);
    expect(yRange(history([0, 0.5], [10, 20], null))).toEqual([9, 21]); // no range: the samples
    expect(yRange(history([], []))).toEqual([0, 2]);
  });

  test("the x axis spans the history held, up to ten minutes, the newest at the right edge", () => {
    expect(xSpan(history([0, 30, 60], [0, 0, 0]))).toBe(60);
    expect(xSpan(history([0, 900], [0, 0]))).toBe(600);
    const pts = polyline(history([0, 300, 600], [0, 1, 2]), 360, 140).split(" ");
    expect(pts).toEqual(["0.0,128.3", "180.0,70.0", "360.0,11.7"]);
    expect(polyline(history([], []), 360, 140)).toBe("");
  });

  test("axis labels carry the digits the span needs", () => {
    expect(label(1.0213, 0.05)).toBe("1.021");
    expect(label(52.3, 10)).toBe("52.3");
  });
});
