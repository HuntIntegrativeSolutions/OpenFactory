// Trend panel (PLAN 3.6): ten minutes of one AI tag, drawn from GET /api/history?tag=, which the
// core samples at 2 Hz of simulated time into a ring buffer. The panel lists the scene's AI tags,
// polls the chosen one once a second, and draws it as an SVG line over a fixed 10-minute axis
// (the newest sample at the right edge, the x axis the history held up to ten minutes, the y axis
// fitted to the samples with the tag's range in the caption), so a level loop's 2 cm dip after a
// load step reads on a 2 m transmitter. It opens from the "Trend" button that
// scenes with AI tags show, or with ?trend=TAG in the address. A replay has no history: the
// panel says so instead of drawing.
// Colors: ISA-101 (ignition-conventions.md): the line is the highlight blue, the grid grey.
import { cssColor } from "./isa101";
import type { SceneDocument } from "./api";

export interface History {
  tag: string;
  units: string;
  range: [number, number] | null;
  period_s: number;
  window_s: number;
  t: number[];
  v: number[];
}

/** The scene's AI tags, in scene order: the tags a trend can show. */
export function aiTags(doc: Pick<SceneDocument, "tags">): string[] {
  return doc.tags.filter((t) => t.kind === "AI").map((t) => t.name);
}

/** The vertical span to draw: the samples' own span with a margin, so a 2 cm dip on a 2 m
 * level reads; the margin is a tenth of that span, and at least 1 % of the tag's range (or 1 % of
 * the value) so a steady reading is a flat line mid-panel, not noise filling it. */
export function yRange(h: Pick<History, "range" | "v">): [number, number] {
  if (!h.v.length) return h.range ? [h.range[0], h.range[1]] : [0, 1];
  const lo = Math.min(...h.v);
  const hi = Math.max(...h.v);
  const floor = h.range ? (h.range[1] - h.range[0]) * 0.01 : Math.abs(hi) * 0.01 || 0.01;
  const pad = Math.max((hi - lo) * 0.1, floor);
  return [lo - pad, hi + pad];
}

/** Seconds the x axis spans: the history held, up to its ten-minute window. */
export function xSpan(h: Pick<History, "t" | "window_s" | "period_s">): number {
  if (h.t.length < 2) return h.period_s;
  return Math.min(h.window_s, h.t[h.t.length - 1] - h.t[0]);
}

/** SVG polyline points for a history in a `width` x `height` box: x spans `xSpan`, ending at the
 * newest sample; y spans `yRange`, high values at the top. */
export function polyline(h: History, width: number, height: number): string {
  if (!h.t.length) return "";
  const end = h.t[h.t.length - 1];
  const span = xSpan(h);
  const [lo, hi] = yRange(h);
  const rise = hi - lo || 1;
  return h.t
    .map((t, i) => {
      const x = width - ((end - t) / span) * width;
      const y = height - ((h.v[i] - lo) / rise) * height;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}

/** A value for an axis label, to the precision the span needs. */
export function label(value: number, span: number): string {
  const digits = Math.max(1, Math.min(4, Math.ceil(-Math.log10(span || 1)) + 1));
  return value.toFixed(digits);
}

const W = 360;
const H = 140;
const SVG_NS = "http://www.w3.org/2000/svg";

export class TrendPanel {
  readonly element: HTMLDivElement;
  readonly button: HTMLButtonElement;
  private readonly select: HTMLSelectElement;
  private readonly svg: SVGSVGElement;
  private readonly line: SVGPolylineElement;
  private readonly caption: HTMLDivElement;
  private readonly labels: SVGTextElement[];
  private timer: number | null = null;
  /** The last history drawn, for tests and the screenshot runner. */
  last: History | null = null;

  constructor() {
    const text = cssColor("normalText");
    this.button = document.createElement("button");
    this.button.id = "trend-button";
    this.button.textContent = "Trend";
    this.button.style.cssText =
      `position:absolute;bottom:12px;right:12px;z-index:9;display:none;font:13px Arial,sans-serif;` +
      `color:${text};background:#111827;border:1px solid #4b5563;border-radius:4px;padding:4px 10px;cursor:pointer`;
    this.button.addEventListener("click", () => (this.element.style.display === "none" ? this.open() : this.close()));
    document.body.appendChild(this.button);

    this.element = document.createElement("div");
    this.element.id = "trend-panel";
    this.element.style.cssText =
      `position:absolute;bottom:44px;right:12px;z-index:9;display:none;width:${W + 20}px;max-width:calc(100vw - 32px);` +
      `font:12px Arial,sans-serif;color:${text};background:#111827;border:1px solid #4b5563;border-radius:4px;padding:8px 10px`;
    this.select = document.createElement("select");
    this.select.style.cssText = `font:12px Arial,sans-serif;color:${text};background:#1f2937;border:1px solid #4b5563;max-width:100%`;
    this.select.addEventListener("change", () => void this.poll());
    this.svg = document.createElementNS(SVG_NS, "svg");
    this.svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    this.svg.setAttribute("width", "100%");
    this.svg.style.cssText = "display:block;margin:6px 0;background:#0b1220";
    for (const f of [0.25, 0.5, 0.75]) {
      const grid = document.createElementNS(SVG_NS, "line");
      grid.setAttribute("x1", "0");
      grid.setAttribute("x2", String(W));
      grid.setAttribute("y1", String(H * f));
      grid.setAttribute("y2", String(H * f));
      grid.setAttribute("stroke", "#374151");
      grid.setAttribute("stroke-width", "1");
      this.svg.appendChild(grid);
    }
    this.line = document.createElementNS(SVG_NS, "polyline");
    this.line.setAttribute("fill", "none");
    this.line.setAttribute("stroke", cssColor("highlight"));
    this.line.setAttribute("stroke-width", "1.5");
    this.svg.appendChild(this.line);
    this.labels = [0, 1, 2].map((i) => {
      const text = document.createElementNS(SVG_NS, "text");
      text.setAttribute("x", i === 2 ? "4" : "4");
      text.setAttribute("y", String(i === 0 ? 12 : i === 1 ? H - 4 : H / 2 + 4));
      text.setAttribute("fill", "#9ca3af");
      text.setAttribute("font-size", "10");
      this.svg.appendChild(text);
      return text;
    });
    this.caption = document.createElement("div");
    this.caption.style.cssText = "color:#9ca3af;white-space:pre";
    this.element.append(this.select, this.svg, this.caption);
    document.body.appendChild(this.element);
  }

  /** Offer the scene's AI tags; show the button when there is one. */
  setScene(doc: SceneDocument): void {
    const tags = aiTags(doc);
    const keep = this.select.value;
    this.select.replaceChildren(
      ...tags.map((name) => {
        const option = document.createElement("option");
        option.value = name;
        option.textContent = `${name}: ${doc.tags.find((t) => t.name === name)?.description ?? ""}`;
        return option;
      }),
    );
    if (tags.includes(keep)) this.select.value = keep;
    this.button.style.display = tags.length ? "block" : "none";
    if (!tags.length) this.close();
  }

  /** Open on `tag` (or the one selected) and poll it every second. */
  open(tag?: string): void {
    if (tag && [...this.select.options].some((o) => o.value === tag)) this.select.value = tag;
    if (!this.select.value) return;
    this.element.style.display = "block";
    void this.poll();
    if (this.timer === null) this.timer = window.setInterval(() => void this.poll(), 1000);
  }

  close(): void {
    this.element.style.display = "none";
    if (this.timer !== null) window.clearInterval(this.timer);
    this.timer = null;
  }

  private async poll(): Promise<void> {
    const tag = this.select.value;
    const res = await fetch(`/api/history?tag=${encodeURIComponent(tag)}`);
    if (!res.ok) {
      const body = (await res.json().catch(() => ({}))) as { detail?: string };
      this.line.setAttribute("points", "");
      this.caption.textContent = body.detail ?? `no history (${res.status})`;
      return;
    }
    const h = (await res.json()) as History;
    if (tag !== this.select.value) return; // the choice changed while this was in flight
    this.last = h;
    this.line.setAttribute("points", polyline(h, W, H));
    const [lo, hi] = yRange(h);
    const now = h.v.length ? h.v[h.v.length - 1] : NaN;
    const unit = h.units ? ` ${h.units}` : "";
    this.labels[0].textContent = `${label(hi, hi - lo)}${unit}`;
    this.labels[1].textContent = `${label(lo, hi - lo)}${unit}`;
    this.labels[2].textContent = `${label((lo + hi) / 2, hi - lo)}`;
    const span = xSpan(h);
    const range = h.range ? `   range ${h.range[0]} to ${h.range[1]}${unit}` : "";
    this.caption.textContent =
      `now ${Number.isNaN(now) ? "-" : now.toFixed(3)}${unit}${range}\n` +
      `last ${span >= 120 ? `${(span / 60).toFixed(1)} min` : `${span.toFixed(0)} s`} of up to ` +
      `${(h.window_s / 60).toFixed(0)} min at ${1 / h.period_s} Hz, newest at the right`;
  }
}
