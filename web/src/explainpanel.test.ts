// PLAN 5.14g: the explain panel's rows and the trail's points.
import { describe, expect, it } from "vitest";
import { stepPoints, stepRows, stepTime, trailPoints, type ExplainAnswer } from "./explainpanel";

const answer: ExplainAnswer = {
  text: "",
  subject: "P000002",
  from_tick: 0,
  to_tick: 3000,
  steps: [
    { tick: 202, t: 4.04, text: "made P000002", basis: "spawn_tick", position: [0.2, 0.3, 0] },
    { tick: 460, t: 9.2, text: "CYL_01_EXT_DO went true", basis: "the change at tick 460", position: [2.71, 0.2, 0] },
    { tick: 580, t: 11.6, until_tick: 585, text: "left the scene at REM_01", basis: "frames 580 and 585" },
  ],
  trail: [
    [210, 0.2, 0.2, 0],
    [205, 0.2, 0.28, 0],
    [215, 0.25, 0.2, 0],
  ],
};

describe("explain panel (PLAN 5.14g)", () => {
  it("shows each step's time, a span for a claim between two frames", () => {
    expect(stepTime(answer.steps[1])).toBe("9.20 s");
    expect(stepTime(answer.steps[2])).toBe("11.60 to 11.70 s");
    expect(stepRows(answer).map((r) => r.time)).toEqual(["4.04 s", "9.20 s", "11.60 to 11.70 s"]);
    expect(stepRows(answer)[1].basis).toBe("the change at tick 460");
  });

  it("draws the trail in tick order and marks only the steps that say where the part was", () => {
    expect(trailPoints(answer)).toEqual([
      [0.2, 0.28, 0],
      [0.2, 0.2, 0],
      [0.25, 0.2, 0],
    ]);
    expect(stepPoints(answer)).toEqual([
      [0.2, 0.3, 0],
      [2.71, 0.2, 0],
    ]);
  });
});
