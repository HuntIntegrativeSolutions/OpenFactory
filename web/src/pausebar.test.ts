// PLAN 5.14h: the pause banner's words (ruling 4: pause with a real PLC is allowed, with the banner).
import { describe, expect, it } from "vitest";
import { bannerText, STEP_LABEL } from "./pausebar";

describe("pause banner (PLAN 5.14h)", () => {
  it("says the PLC keeps running when a protocol server serves one", () => {
    const text = bannerText(["Modbus TCP"]);
    expect(text).toContain("Simulation paused, PLC still running (Modbus TCP)");
    expect(text).toContain("heartbeat has stopped");
    expect(bannerText(["Modbus TCP", "OPC UA"])).toContain("(Modbus TCP, OPC UA)");
  });

  it("says the stand-in script pauses with the simulation when no PLC is served", () => {
    expect(bannerText([])).toBe("PAUSED: Simulation paused (the stand-in script, if any, pauses with it)");
    expect(STEP_LABEL).toBe("Step 0.1 s");
  });
});
