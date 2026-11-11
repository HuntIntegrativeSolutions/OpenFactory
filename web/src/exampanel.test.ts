import { describe, expect, it } from "vitest";
import { examPanelTop, SwitchHold } from "./exampanel";

describe("an exam switch click is held until the core echoes it (ruled 2026-11-10)", () => {
  it("ignores a stale frame after the click and follows frames again once one agrees", () => {
    const hold = new SwitchHold();
    expect(hold.shown("PE_01_DI", false, 0)).toBe(false); // nothing held: the frame decides
    hold.click("PE_01_DI", true, 100);
    expect(hold.shown("PE_01_DI", false, 120)).toBe(true); // stale frame: the click stays
    expect(hold.shown("PE_01_DI", true, 150)).toBe(true); // the echo
    expect(hold.shown("PE_01_DI", false, 200)).toBe(false); // released: someone turned it off
  });

  it("gives up after HOLD_MS, so a lost click shows the core's value", () => {
    const hold = new SwitchHold();
    hold.click("PE_01_DI", true, 0);
    expect(hold.shown("PE_01_DI", false, SwitchHold.HOLD_MS)).toBe(true);
    expect(hold.shown("PE_01_DI", false, SwitchHold.HOLD_MS + 1)).toBe(false);
  });

  it("holds per switch and stops holding when released", () => {
    const hold = new SwitchHold();
    hold.click("A", true, 0);
    expect(hold.shown("B", false, 10)).toBe(false);
    hold.release("A");
    expect(hold.shown("A", false, 10)).toBe(false);
  });
});

describe("the exam panel keeps clear of the status lines (review of 2026-11-10)", () => {
  it("stays at the top right when both fit side by side", () => {
    expect(examPanelTop({ right: 600, bottom: 90 }, 320, 1280)).toBe(12);
  });

  it("drops below the status lines when the viewport is too narrow", () => {
    expect(examPanelTop({ right: 600, bottom: 90 }, 320, 640)).toBe(98);
    expect(examPanelTop({ right: 300.2, bottom: 87.4 }, 320, 640)).toBe(96);
  });
});
