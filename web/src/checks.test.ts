// PLAN 4.1i: the live checks panel's states, colors and header.
import { describe, expect, it } from "vitest";
import { folded, STATE_STYLE, summary, type CheckStatus } from "./checks";
import { cssColor, ISA101 } from "./isa101";

const status = (graded: boolean, states: ("pending" | "passed" | "failed")[]): CheckStatus => ({
  t: graded ? 60 : 12.4,
  duration_s: 60,
  graded,
  checks: states.map((state, i) => ({ name: `check ${i}`, state, detail: "" })),
});

describe("live checks panel (PLAN 4.1i)", () => {
  it("draws each state in an ISA-101 palette color, never green against red", () => {
    expect(STATE_STYLE.failed.color).toBe("alarmHigh");
    expect(STATE_STYLE.pending.color).toBe("stopped");
    expect(STATE_STYLE.passed.color).toBe("normalText");
    for (const s of Object.values(STATE_STYLE)) {
      expect(ISA101[s.color]).toBeDefined();
      expect(cssColor(s.color)).not.toBe(cssColor("running")); // no green for "good"
    }
    // color is never the only cue
    expect(new Set(Object.values(STATE_STYLE).map((s) => s.label)).size).toBe(3);
  });

  it("counts states and says when the run is graded", () => {
    expect(summary(status(false, ["pending", "failed", "pending"]))).toBe(
      "Checks: 0 passed, 1 failed, 2 pending · graded at 60 s (t = 12 s)",
    );
    expect(summary(status(true, ["passed", "passed"]))).toBe("Checks: 2 passed, 0 failed, 0 pending · graded at 60 s");
  });

  it("folds to one short line", () => {
    expect(folded(status(false, ["pending", "failed"]))).toBe("Checks: 1 failed");
    expect(folded(status(false, ["pending", "passed"]))).toBe("Checks: 1 pending");
    expect(folded(status(true, ["passed", "passed"]))).toBe("Checks: 2 passed");
  });
});
