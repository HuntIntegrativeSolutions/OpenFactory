// PLAN 4.14 item 4: which quality level a page starts at, and remembering a HUD choice.
import { describe, expect, it } from "vitest";
import { DEFAULT_QUALITY, initialQuality, readStored, remember, STORAGE_KEY } from "./quality";

describe("initialQuality", () => {
  it("takes the URL first, then the remembered choice, then low when automated, else the default", () => {
    expect(initialQuality("?quality=high", "low", true)).toBe("high");
    expect(initialQuality("", "high", true)).toBe("high");
    expect(initialQuality("", null, true)).toBe("low");
    expect(initialQuality("", null, false)).toBe(DEFAULT_QUALITY);
    expect(DEFAULT_QUALITY).toBe("standard"); // open for ruling until the owner's GPU reading
  });

  it("ignores values that are not levels", () => {
    expect(initialQuality("?quality=ultra", "medium", false)).toBe(DEFAULT_QUALITY);
    expect(initialQuality("?trend=TK_01_LT_AI&quality=low", null, false)).toBe("low");
  });
});

describe("remembered choice", () => {
  it("round-trips through storage under its key", () => {
    const data = new Map<string, string>();
    const storage = { getItem: (k: string) => data.get(k) ?? null, setItem: (k: string, v: string) => void data.set(k, v) };
    remember(storage, "high");
    expect(data.get(STORAGE_KEY)).toBe("high");
    expect(readStored(storage)).toBe("high");
  });

  it("survives storage that is missing or throws", () => {
    const broken = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
    };
    expect(readStored(broken)).toBeNull();
    expect(() => remember(broken, "low")).not.toThrow();
    expect(readStored(null)).toBeNull();
  });
});
