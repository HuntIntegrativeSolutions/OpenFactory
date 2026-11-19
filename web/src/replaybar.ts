// Pause, seek and step in a replay (PLAN 5.14f): the bar at the bottom of the viewer that
// `openfactory replay` serves. Play or pause, a time slider over the whole recording, and a step
// back or forward by one recorded frame (10 Hz at 50 Hz). Space plays or pauses, and the left
// and right arrow keys step, while no text field has the keyboard. Each control posts to the
// server's replay endpoints (POST /api/replay/seek, /pause, /step), which answer where the
// replay is; the frames that follow come over /ws/state as always. The helpers above the class
// are DOM-free, so replaybar.test.ts runs under node.
import { cssColor } from "./isa101";

export interface ReplayStatus {
  tick: number;
  t: number;
  paused: boolean;
  finished: boolean;
  index: number;
  frames: number;
  first_tick: number;
  last_tick: number;
  frame_every: number;
  dt: number;
  speed: number;
}

/** The recorded frame a slider value means: the frame at or before it, within the recording. */
export function frameTick(value: number, s: Pick<ReplayStatus, "first_tick" | "last_tick" | "frame_every">): number {
  const clamped = Math.min(Math.max(Math.round(value), s.first_tick), s.last_tick);
  if (clamped === s.last_tick) return s.last_tick; // the last frame may sit off the 5-tick grid
  return s.first_tick + Math.floor((clamped - s.first_tick) / s.frame_every) * s.frame_every;
}

/** How far on a frame may be and still be the replay playing: two recorded frames, or three
 * times the ticks a 20 Hz state frame covers at the replay's speed, whichever is more. */
export function jumpLimit(s: Pick<ReplayStatus, "speed" | "dt" | "frame_every">): number {
  return Math.max(2 * s.frame_every, Math.ceil((3 * s.speed * 0.05) / s.dt - 1e-9));
}

/** A frame that goes back, or further on than `limit` ticks from the last shown (a seek, a step
 * back, a loop): parts are put where it has them at once instead of easing there. */
export function isJump(previous: number | null, next: number, limit = 10): boolean {
  if (previous === null) return true;
  return next < previous || next - previous > limit;
}

/** "12.30 s / 60.00 s". */
export function timeText(tick: number, s: Pick<ReplayStatus, "dt" | "last_tick">): string {
  return `${(tick * s.dt).toFixed(2)} s / ${(s.last_tick * s.dt).toFixed(2)} s`;
}

export class ReplayBar {
  readonly element = document.createElement("div");
  private readonly play = document.createElement("button");
  private readonly back = document.createElement("button");
  private readonly forward = document.createElement("button");
  private readonly slider = document.createElement("input");
  private readonly time = document.createElement("span");
  private status: ReplayStatus | null = null;
  private dragging = false;

  constructor() {
    this.element.id = "replay-bar";
    this.element.style.cssText =
      `position:absolute;left:50%;transform:translateX(-50%);bottom:44px;z-index:9;display:none;` +
      `align-items:center;gap:6px;font:12px Arial,sans-serif;color:${cssColor("normalText")};` +
      "background:rgba(31,42,54,0.9);border:1px solid #4b5563;border-radius:4px;padding:4px 8px;" +
      "width:min(640px,calc(100vw - 32px));box-sizing:border-box";
    const button = (b: HTMLButtonElement, id: string, text: string, title: string) => {
      b.id = id;
      b.textContent = text;
      b.title = title;
      b.style.cssText =
        `font:12px Arial,sans-serif;color:${cssColor("normalText")};background:#1f2937;` +
        "border:1px solid #4b5563;border-radius:3px;padding:2px 6px;cursor:pointer;min-width:30px";
    };
    button(this.back, "replay-back", "◀|", "Back one frame (left arrow)");
    button(this.play, "replay-play", "❚❚", "Play or pause (Space)");
    button(this.forward, "replay-forward", "|▶", "Forward one frame (right arrow)");
    this.slider.id = "replay-slider";
    this.slider.type = "range";
    this.slider.style.cssText = "flex:1;min-width:80px";
    this.slider.title = "Drag to any moment of the recording";
    this.time.id = "replay-time";
    this.time.style.cssText = "white-space:nowrap;font-variant-numeric:tabular-nums";
    this.element.append(this.back, this.play, this.forward, this.slider, this.time);
    this.play.addEventListener("click", () => void this.send("/api/replay/pause", { paused: !this.status?.paused }));
    this.back.addEventListener("click", () => void this.send("/api/replay/step", { frames: -1 }));
    this.forward.addEventListener("click", () => void this.send("/api/replay/step", { frames: 1 }));
    this.slider.addEventListener("pointerdown", () => (this.dragging = true));
    this.slider.addEventListener("input", () => {
      if (this.status) this.time.textContent = timeText(frameTick(Number(this.slider.value), this.status), this.status);
    });
    this.slider.addEventListener("change", () => {
      this.dragging = false;
      if (this.status) void this.send("/api/replay/seek", { tick: frameTick(Number(this.slider.value), this.status) });
    });
    window.addEventListener("keydown", (e) => {
      if (this.element.style.display === "none") return;
      const typing = e.target instanceof HTMLInputElement && e.target !== this.slider;
      if (typing || e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLSelectElement) return;
      if (e.key === " ") {
        e.preventDefault();
        this.play.click();
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        this.back.click();
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        this.forward.click();
      }
    });
  }

  /** Show the bar for the replay the server serves. */
  async start(): Promise<void> {
    const res = await fetch("/api/replay");
    if (!res.ok) return;
    this.apply((await res.json()) as ReplayStatus);
    this.element.style.display = "flex";
  }

  /** The largest step on that is not a jump (`jumpLimit`), 10 ticks before the status arrives. */
  jumpLimit(): number {
    return this.status ? jumpLimit(this.status) : 10;
  }

  /** A state frame arrived: the slider and the time follow it unless the person is dragging. */
  frame(tick: number): void {
    if (!this.status) return;
    this.status.tick = tick;
    if (!this.dragging) this.slider.value = String(tick);
    this.time.textContent = timeText(tick, this.status);
    window.__openfactory.replay = { ...this.status };
  }

  private apply(s: ReplayStatus): void {
    this.status = s;
    this.slider.min = String(s.first_tick);
    this.slider.max = String(s.last_tick);
    this.slider.step = "1";
    if (!this.dragging) this.slider.value = String(s.tick);
    this.play.textContent = s.paused || s.finished ? "▶" : "❚❚";
    this.play.title = s.paused || s.finished ? "Play (Space)" : "Pause (Space)";
    this.time.textContent = timeText(s.tick, s);
    window.__openfactory.replay = { ...s };
  }

  private async send(url: string, body: Record<string, unknown>): Promise<void> {
    const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (res.ok) this.apply((await res.json()) as ReplayStatus);
    else console.warn(`OpenFactory: ${url} refused (${res.status})`);
  }

  /** The play button's state follows the server's (a replay that reached its end shows play). */
  async refresh(): Promise<void> {
    const res = await fetch("/api/replay");
    if (res.ok) this.apply((await res.json()) as ReplayStatus);
  }
}
