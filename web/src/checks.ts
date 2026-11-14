// Live checks (PLAN 4.1i): while a scene runs, a panel lists each of its `checks:` as pending,
// passed or failed, read from GET /api/checks once a second (core/checks.py `live`). A check is
// failed as soon as running longer cannot make it pass, and at `duration_s` the panel shows the
// graded result, as `openfactory test` grades the run, which then stays. Read-only: the panel
// has nothing to press but its header, which folds it. The instructor view shows the same panel,
// folded below 900 px as the exam panel is, so the fault labels stay visible.
//
// Colors (ISA-101, the palette in isa101.ts): color marks what is abnormal. A failed check is
// alarm red; a pending one is the stopped grey; a passed one is the normal text color, brighter
// than pending, never green, so good and bad are never green against red (HIS ISA-101 rule).
// Each row also says PASS, FAIL or a dash, so color is never the only cue.
import { startsCollapsed } from "./exampanel";
import { cssColor, type Isa101Color } from "./isa101";

export type CheckState = "pending" | "passed" | "failed";

export interface CheckEntry {
  name: string;
  state: CheckState;
  detail: string;
}

export interface CheckStatus {
  t: number;
  duration_s: number | null;
  graded: boolean;
  checks: CheckEntry[];
  replay?: boolean;
}

/** The palette entry and label each state is drawn with. */
export const STATE_STYLE: Record<CheckState, { color: Isa101Color; label: string }> = {
  pending: { color: "stopped", label: "—" },
  passed: { color: "normalText", label: "PASS" },
  failed: { color: "alarmHigh", label: "FAIL" },
};

/** The header line: how many passed and failed, and when the run is (or was) graded. */
export function summary(status: CheckStatus): string {
  const n = (s: CheckState) => status.checks.filter((c) => c.state === s).length;
  const counts = `${n("passed")} passed, ${n("failed")} failed, ${n("pending")} pending`;
  if (status.duration_s === null) return `Checks: ${counts}`;
  const when = status.graded
    ? `graded at ${status.duration_s} s`
    : `graded at ${status.duration_s} s (t = ${Math.floor(status.t)} s)`;
  return `Checks: ${counts} · ${when}`;
}

/** Whether the panel starts folded: as the exam panel (ruled 2026-11-10), what this browser
 * chose last time on this kind of page, else folded on the instructor page below 900 px, so its
 * fault labels stay visible, and open everywhere else. */
/** The folded panel's one line: the failures, if any, else how many checks are pending. */
export function folded(status: CheckStatus): string {
  const failed = status.checks.filter((c) => c.state === "failed").length;
  if (failed) return `Checks: ${failed} failed`;
  const pending = status.checks.filter((c) => c.state === "pending").length;
  return pending ? `Checks: ${pending} pending` : `Checks: ${status.checks.length} passed`;
}

function readFolded(key: string, instructor: boolean): boolean {
  let stored: string | null = null;
  try {
    stored = window.localStorage.getItem(key);
  } catch {
    // storage blocked: the default applies
  }
  return startsCollapsed(stored, instructor, window.innerWidth);
}

function writeFolded(key: string, folded: boolean): void {
  try {
    window.localStorage.setItem(key, folded ? "1" : "0");
  } catch {
    // storage blocked: the fold lasts for this page only
  }
}

export class ChecksPanel {
  readonly element: HTMLDivElement;
  /** The last status read, for tests (window.__openfactory.checks). */
  status: CheckStatus | null = null;
  private folded: boolean;
  private readonly storageKey: string;
  private timer: number | undefined;

  constructor(instructor = false) {
    this.storageKey = `openfactory.checksPanel.folded.${instructor ? "instructor" : "viewer"}`;
    this.folded = readFolded(this.storageKey, instructor);
    this.element = document.createElement("div");
    this.element.id = "checks-panel";
    this.element.style.cssText =
      `position:absolute;top:12px;right:12px;display:none;z-index:9;width:min(340px,calc(100vw - 32px));` +
      `box-sizing:border-box;font:13px Arial,sans-serif;color:${cssColor("normalText")};background:#111827;` +
      "border:1px solid #4b5563;border-radius:4px;padding:8px 10px";
    document.body.appendChild(this.element);
  }

  /** Poll GET /api/checks every `periodMs` until stop(). */
  start(periodMs = 1000): void {
    const tick = () => void this.refresh().catch(() => undefined);
    tick();
    this.timer = window.setInterval(tick, periodMs);
  }

  stop(): void {
    if (this.timer !== undefined) window.clearInterval(this.timer);
    this.timer = undefined;
  }

  /** Place the panel `top` px from the top (below the exam panel when that is shown). */
  placeAt(top: number): void {
    const css = `${Math.round(top)}px`;
    if (this.element.style.top === css) return;
    this.element.style.top = css;
    // a long list scrolls inside the panel, clear of the trend button at the bottom right
    this.element.style.maxHeight = `calc(100vh - ${Math.round(top)}px - 56px)`;
    this.element.style.overflowY = "auto";
  }

  async refresh(): Promise<void> {
    const res = await fetch("/api/checks");
    if (!res.ok) return;
    this.render((await res.json()) as CheckStatus);
  }

  render(status: CheckStatus): void {
    this.status = status;
    if (status.replay || !status.checks.length) {
      this.element.style.display = "none";
      return;
    }
    this.element.style.display = "block";
    this.element.replaceChildren();
    const head = document.createElement("div");
    head.id = "checks-panel-header";
    head.setAttribute("role", "button");
    head.tabIndex = 0;
    head.style.cssText = "font-weight:700;cursor:pointer;user-select:none";
    const failed = status.checks.some((c) => c.state === "failed");
    head.style.color = failed ? cssColor("alarmHigh") : cssColor("normalText");
    // folded, the panel is one short line, as narrow as its text, so it covers no labels
    head.textContent = this.folded ? `▸ ${folded(status)}` : `▾ ${summary(status)}`;
    this.element.style.width = this.folded ? "auto" : "min(340px,calc(100vw - 32px))";
    const fold = () => {
      this.folded = !this.folded;
      writeFolded(this.storageKey, this.folded);
      this.render(status);
    };
    head.addEventListener("click", fold);
    head.addEventListener("keydown", (e) => (e.key === "Enter" || e.key === " ") && fold());
    this.element.appendChild(head);
    if (this.folded) return;
    const list = document.createElement("div");
    list.id = "checks-panel-list";
    list.style.marginTop = "6px";
    for (const check of status.checks) {
      const style = STATE_STYLE[check.state];
      const row = document.createElement("div");
      row.className = `check check-${check.state}`;
      row.dataset.state = check.state;
      row.style.cssText = "display:flex;gap:8px;align-items:baseline;margin:3px 0";
      const label = document.createElement("span");
      label.className = "check-label";
      label.textContent = style.label;
      label.style.cssText = `flex:0 0 36px;font-weight:700;color:${cssColor(style.color)}`;
      const text = document.createElement("span");
      text.style.cssText = "flex:1;min-width:0";
      const name = document.createElement("div");
      name.className = "check-name";
      name.textContent = check.name;
      name.style.color = cssColor(style.color);
      const detail = document.createElement("div");
      detail.className = "check-detail";
      detail.textContent = check.detail;
      detail.style.cssText = `font-size:11px;color:${cssColor("stopped")};overflow-wrap:anywhere`;
      text.append(name, detail);
      row.append(label, text);
      list.appendChild(row);
    }
    this.element.appendChild(list);
  }
}
