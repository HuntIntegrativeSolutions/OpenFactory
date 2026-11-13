// Viewer quality levels (PLAN 4.14, ruled 2026-11-11). DOM-free apart from the Storage-shaped
// object passed in, so web/src/quality.test.ts runs under node.
//
//   low       today's lighting (before 4.14) for everything except status materials, which are
//             exact at every level (components/types.ts, statusMaterial)
//   standard  filmic tone mapping and a studio environment, no shadows
//   high      standard plus soft shadows
//
// The level comes from `?quality=` in the URL, else the level this browser last chose with the HUD
// control, else low for an automated browser and the default for a person. "Automated" is decided
// in one place, `isAutomatedBrowser`, below. A URL level applies to that page only and is not
// remembered, so a shared link never overwrites a person's choice.

export type Quality = "low" | "standard" | "high";

export const QUALITIES: readonly Quality[] = ["low", "standard", "high"];

/** localStorage key for the level chosen with the HUD control. */
export const STORAGE_KEY = "openfactory.quality";

/** The level for a person with no choice made (PLAN 4.14: open for ruling until the owner's GPU reading). */
export const DEFAULT_QUALITY: Quality = "standard";

export function isQuality(value: unknown): value is Quality {
  return typeof value === "string" && (QUALITIES as readonly string[]).includes(value);
}

/**
 * Whether the page runs in an automated browser (PLAN 4.14, ruled 2026-11-12). This is the only
 * place that decides it. It detects exactly one thing: `navigator.webdriver` is the boolean
 * `true`. The WebDriver standard has a browser set that flag while automation controls it;
 * Chromium sets it under Playwright (CI, tools/screenshot.py, the integration tests), Selenium
 * and Puppeteer. Anything else (the flag false or missing, a non-boolean value, no navigator)
 * counts as a person. It does not look at headless mode, the user agent or screen size.
 */
export function isAutomatedBrowser(nav: { webdriver?: unknown } | null | undefined): boolean {
  return nav?.webdriver === true;
}

/** Tooltip of the HUD quality control; docs/guides/README.md, "Viewer quality", says the same. */
export const QUALITY_TOOLTIP =
  "Viewer quality: low (flat lighting, lightest), standard (tone mapping and a studio " +
  "environment), high (standard plus soft shadows). Status colors are exact at every level. " +
  "Your choice here is remembered by this browser; ?quality= in the address overrides it for one " +
  "page. With no choice made, the viewer starts at " + DEFAULT_QUALITY + ", or at low when " +
  "navigator.webdriver is true (a browser under automation such as Playwright).";

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
