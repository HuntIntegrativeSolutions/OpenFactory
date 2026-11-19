// Why it happened (PLAN 5.14g): clicking a part asks POST /api/explain for its story (the server
// works it out from the last 60 s it holds, or a replay's recording up to the frame shown), then
// the viewer draws the part's trail through the positions its frames give and lists the same
// steps in a panel, each with its time and what it rests on. Esc or the × closes both. Read-only:
// the request changes nothing in the run.
//
// Colors (ISA-101, isa101.ts): the trail and its step markers are the highlight blue, which marks
// a selection, never a state; the panel's text is the normal text color.
import { cssColor } from "./isa101";

export interface ExplainStep {
  tick: number;
  t: number;
  until_tick?: number;
  text: string;
  basis: string;
  position?: [number, number, number];
}

export interface ExplainAnswer {
  text: string;
  subject: string | null;
  from_tick: number;
  to_tick: number;
  steps: ExplainStep[];
  trail: [number, number, number, number][]; // [tick, x, y, z]
}

/** A step's time as the panel shows it: "9.20 s", or "11.60 to 11.70 s" for a span between two
 * frames (dt from the step's own tick and time). */
export function stepTime(step: ExplainStep): string {
  if (step.until_tick === undefined || step.tick === 0) return `${step.t.toFixed(2)} s`;
  const dt = step.t / step.tick;
  return `${step.t.toFixed(2)} to ${(step.until_tick * dt).toFixed(2)} s`;
}

/** The panel's rows: time, then the claim; the basis is the row's tooltip. */
export function stepRows(answer: ExplainAnswer): { time: string; text: string; basis: string }[] {
  return answer.steps.map((s) => ({ time: stepTime(s), text: s.text, basis: s.basis }));
}

/** The trail's points, x y z, in tick order. */
export function trailPoints(answer: ExplainAnswer): [number, number, number][] {
  return [...answer.trail].sort((a, b) => a[0] - b[0]).map(([, x, y, z]) => [x, y, z]);
}

/** The step markers: every step that says where the part was. */
export function stepPoints(answer: ExplainAnswer): [number, number, number][] {
  return answer.steps.flatMap((s) => (s.position ? [s.position] : []));
}

export class ExplainPanel {
  readonly element = document.createElement("div");
  private readonly title = document.createElement("div");
  private readonly list = document.createElement("ol");
  private readonly close = document.createElement("button");

  constructor(onClose: () => void) {
    this.element.id = "explain-panel";
    this.element.style.cssText =
      `position:absolute;top:110px;left:16px;z-index:9;display:none;width:min(440px,calc(100vw - 32px));` +
      `max-height:calc(100vh - 200px);overflow:auto;font:12px Arial,sans-serif;color:${cssColor("normalText")};` +
      "background:rgba(17,24,39,0.92);border:1px solid #4b5563;border-radius:4px;padding:6px 8px";
    const head = document.createElement("div");
    head.style.cssText = "display:flex;justify-content:space-between;align-items:center;gap:8px";
    this.title.style.cssText = `font-weight:bold;color:${cssColor("highlight")}`;
    this.close.textContent = "×";
    this.close.title = "Close (Esc)";
    this.close.style.cssText = `font:14px Arial,sans-serif;color:${cssColor("normalText")};background:none;border:none;cursor:pointer`;
    this.close.addEventListener("click", onClose);
    head.append(this.title, this.close);
    this.list.style.cssText = "margin:4px 0 0 0;padding-left:18px";
    this.element.append(head, this.list);
  }

  show(answer: ExplainAnswer): void {
    this.title.textContent = `Why: ${answer.subject ?? "no part"} (ticks ${answer.from_tick} to ${answer.to_tick})`;
    this.list.replaceChildren(
      ...stepRows(answer).map((row) => {
        const li = document.createElement("li");
        li.textContent = `${row.time}: ${row.text}`;
        li.title = row.basis;
        li.style.margin = "2px 0";
        return li;
      }),
    );
    this.element.style.display = "block";
  }

  showError(text: string): void {
    this.title.textContent = "Why: not available";
    const li = document.createElement("li");
    li.textContent = text;
    this.list.replaceChildren(li);
    this.element.style.display = "block";
  }

  hide(): void {
    this.element.style.display = "none";
  }
}
