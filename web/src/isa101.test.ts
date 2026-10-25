import { describe, expect, it } from "vitest";
import { ISA101, cssColor } from "./isa101";

describe("ISA-101 palette", () => {
  it("matches the HIS Controls status colors", () => {
    expect(cssColor("background")).toBe("#1F2A36");
    expect(cssColor("running")).toBe("#22C55E");
    expect(cssColor("stopped")).toBe("#9CA3AF");
    expect(cssColor("alarmHigh")).toBe("#EF4444");
  });

  it("keeps running and alarm colors distinct from the stopped gray", () => {
    expect(new Set(Object.values(ISA101)).size).toBe(Object.keys(ISA101).length);
  });
});
