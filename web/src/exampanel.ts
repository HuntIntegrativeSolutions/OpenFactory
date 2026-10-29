// Exam mode (PLAN 2.11): with `openfactory run --exam`, every DI the scene binds `exam: switch`
// follows an operator switch here instead of the simulation, so an instructor can drive a sensor
// by hand while trainees watch the plant. The panel lists the switches from GET /api/exam and
// turns one with POST /api/exam (instructor endpoint: token-guarded off loopback, see guarded.ts).
// A lit switch is ISA-101 highlight blue (an operator override), an open one grey.
import { guardedFetch } from "./guarded";
import { cssColor } from "./isa101";

interface ExamSwitch {
  tag: string;
  component: string;
  port: string;
  description: string;
  on: boolean;
}

interface ExamState {
  enabled: boolean;
  switches: ExamSwitch[];
}

export class ExamPanel {
  readonly element: HTMLDivElement;
  private buttons = new Map<string, HTMLButtonElement>();

  constructor() {
    this.element = document.createElement("div");
    this.element.id = "exam-panel";
    this.element.style.cssText =
      `position:absolute;top:12px;right:12px;display:none;z-index:9;max-width:min(320px,calc(100vw - 32px));` +
      `font:13px Arial,sans-serif;color:${cssColor("normalText")};background:#111827;` +
      "border:1px solid #4b5563;border-radius:4px;padding:8px 10px";
    document.body.appendChild(this.element);
  }

  /** Read the server's exam state and show the panel if exam mode is on. Returns whether it is. */
  async refresh(): Promise<boolean> {
    const state = (await (await fetch("/api/exam")).json()) as ExamState;
    this.element.replaceChildren();
    this.buttons.clear();
    if (!state.enabled) {
      this.element.style.display = "none";
      return false;
    }
    const title = document.createElement("div");
    title.textContent = "EXAM MODE: operator switches";
    title.style.cssText = "font-weight:700;margin-bottom:6px";
    this.element.appendChild(title);
    for (const s of state.switches) {
      const button = document.createElement("button");
      button.dataset.tag = s.tag;
      button.title = `${s.component}.${s.port}: ${s.description}`;
      button.style.cssText =
        "display:block;width:100%;margin:4px 0;padding:6px 8px;text-align:left;cursor:pointer;" +
        "border:1px solid #4b5563;border-radius:3px;font:13px Arial,sans-serif;color:#111827";
      button.addEventListener("click", () => void this.toggle(s.tag));
      this.buttons.set(s.tag, button);
      this.show(s.tag, s.on);
      this.element.appendChild(button);
    }
    this.element.style.display = "block";
    return true;
  }

  private show(tag: string, on: boolean): void {
    const button = this.buttons.get(tag);
    if (!button) return;
    button.dataset.on = String(on);
    button.textContent = `${tag}: ${on ? "ON" : "OFF"}`;
    button.style.background = on ? cssColor("highlight") : cssColor("stopped");
  }

  private async toggle(tag: string): Promise<void> {
    const on = this.buttons.get(tag)?.dataset.on !== "true";
    const res = await guardedFetch("/api/exam", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tag, on }),
    });
    if (res.ok) this.show(tag, ((await res.json()) as { on: boolean }).on);
  }
}
