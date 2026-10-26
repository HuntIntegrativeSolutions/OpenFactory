import { describe, expect, it } from "vitest";
import { MAX_STEPS_PER_FRAME, planSteps, strokeAt } from "./ticksync";

describe("planSteps (PLAN 2.0a)", () => {
  it("steps exactly the ticks since the last frame", () => {
    expect(planSteps(100, 103)).toEqual({ steps: 3, dropped: 0, firstTick: 101, reset: false });
  });
  it("never steps when no tick passed", () => {
    expect(planSteps(100, 100).steps).toBe(0);
  });
  it("caps at 10 per frame and reports what it dropped", () => {
    expect(MAX_STEPS_PER_FRAME).toBe(10);
    expect(planSteps(100, 125)).toEqual({ steps: 10, dropped: 15, firstTick: 116, reset: false });
  });
  it("resets on the first frame and when the core restarts", () => {
    expect(planSteps(null, 50)).toMatchObject({ steps: 0, reset: true });
    expect(planSteps(500, 3)).toMatchObject({ steps: 0, reset: true });
  });
});

describe("strokeAt", () => {
  it("interpolates the pusher stroke across the frame's steps", () => {
    expect([1, 2, 3, 4].map((i) => strokeAt(0, 0.4, i, 4))).toEqual([0.1, 0.2, 0.30000000000000004, 0.4]);
    expect(strokeAt(0.2, 0.5, 0, 0)).toBe(0.5);
  });
});
