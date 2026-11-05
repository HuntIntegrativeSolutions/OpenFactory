import { describe, expect, it } from "vitest";
import { FrameStats } from "./framestats";

describe("FrameStats (PLAN 2.9 stats overlay)", () => {
  it("reports the mean and 95th-percentile interval of the recent frames", () => {
    const s = new FrameStats(20);
    expect(s.meanMs).toBeNull();
    let t = 0;
    for (let i = 0; i < 40; i++) {
      t += i % 10 === 9 ? 50 : 10; // a 50 ms stutter in every 10 frames: 10 %, inside the p95
      s.frame(t, 2);
    }
    expect(s.meanMs).toBeCloseTo((18 * 10 + 2 * 50) / 20, 6); // only the last 20 intervals
    expect(s.p95Ms).toBe(50);
    expect(s.renderMs).toBe(2);
  });
});
