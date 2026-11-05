import { describe, expect, it } from "vitest";
import { commsItems } from "./faultmenu";

describe("the comms menu (PLAN 2.13b)", () => {
  it("offers drop and two delays for every protocol server, and marks and clears the active one", () => {
    const healthy = commsItems({ comms: { drop: false, latency_ms: 0 }, protocols: ["Modbus TCP", "OPC UA"] })!;
    expect(healthy.title).toBe("Comms · Modbus TCP and OPC UA");
    expect(healthy.items.map((i) => i.text)).toEqual([
      "Drop every request", "Delay every reply 250 ms", "Delay every reply 1000 ms",
    ]);
    expect(healthy.items[0].body).toEqual({ comms: { drop: true } });
    expect(healthy.items[2].body).toEqual({ comms: { latency_ms: 1000 } });
    const late = commsItems({ comms: { drop: false, latency_ms: 250 }, protocols: ["OPC UA"] })!;
    expect(late.items.map((i) => i.text)).toEqual([
      "Drop every request", "● Delay every reply 250 ms", "Delay every reply 1000 ms", "Clear comms fault (250 ms)",
    ]);
    expect(late.items[3]).toMatchObject({ method: "DELETE", path: "/api/fault/comms", body: null });
    const dropped = commsItems({ comms: { drop: true, latency_ms: 0 }, protocols: ["Modbus TCP"] })!;
    expect(dropped.items[0].text).toBe("● Drop every request");
    expect(dropped.items.at(-1)!.text).toBe("Clear comms fault (drop)");
  });

  it("is absent when the run has no protocol server", () => {
    expect(commsItems({ comms: null, protocols: [] })).toBeNull();
  });
});
