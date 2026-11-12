// Viewer quality levels (PLAN 4.14, ruled 2026-11-11). DOM-free apart from the Storage-shaped
// object passed in, so web/src/quality.test.ts runs under node.
//
//   low       today's lighting (before 4.14) for everything except status materials, which are
//             exact at every level (components/types.ts, statusMaterial)
//   standard  filmic tone mapping and a studio environment, no shadows
//   high      standard plus soft shadows
//
// The level comes from `?quality=` in the URL, else the level this browser last chose with the HUD
// control, else low for an automated browser and the default for a person. "Automated" is
// `navigator.webdriver`, which Chromium sets under Playwright, so CI, tools/screenshot.py and
// every Playwright test run at low unless they ask for a level in the URL. A URL level applies to
// that page only and is not remembered, so a shared link never overwrites a person's choice.

export type Quality = "low" | "standard" | "high";

export const QUALITIES: readonly Quality[] = ["low", "standard", "high"];

/** localStorage key for the level chosen with the HUD control. */
export const STORAGE_KEY = "openfactory.quality";

/** The level for a person with no choice made (PLAN 4.14: open for ruling until the owner's GPU reading). */
export const DEFAULT_QUALITY: Quality = "standard";

export function isQuality(value: unknown): value is Quality {
  return typeof value === "string" && (QUALITIES as readonly string[]).includes(value);
}

/** The level a page starts at (see the header for the order). */
export function initialQuality(search: string, stored: string | null, automated: boolean): Quality {
  const asked = new URLSearchParams(search).get("quality");
  if (isQuality(asked)) return asked;
  if (isQuality(stored)) return stored;
  return automated ? "low" : DEFAULT_QUALITY;
}

/** The remembered level, or null; storage can be missing or throw (private mode, blocked site data). */
export function readStored(storage: Pick<Storage, "getItem"> | null | undefined): string | null {
  try {
    return storage?.getItem(STORAGE_KEY) ?? null;
  } catch {
    return null;
  }
}

/** Remember a level chosen with the HUD control; a storage failure is ignored (nothing breaks). */
export function remember(storage: Pick<Storage, "setItem"> | null | undefined, level: Quality): void {
  try {
    storage?.setItem(STORAGE_KEY, level);
  } catch {
    // the choice still applies to this page
  }
}
