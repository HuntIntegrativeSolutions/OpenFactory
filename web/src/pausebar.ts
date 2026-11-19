// Pause and single-step a live run (PLAN 5.14h): Space or the Pause button pauses the simulation
// (POST /api/pause), Step runs 0.1 s (POST /api/step), and a banner says the simulation is
// paused, and, when a protocol server is serving a PLC, that the PLC keeps running (ruling 4:
// allowed, with the banner). The stand-in script pauses with the simulation. Every viewer of the
// run shows the banner, from the frames' `sim_paused`. Not offered in the editor, in a replay
// (its own bar pauses it), or to a trainee in exam mode.
//
// Colors (ISA-101, isa101.ts): a paused simulation is an abnormal condition, so the banner's
// border and marker are the medium-alarm yellow, with the normal text color for the words.
import { cssColor } from "./isa101";

/** The banner's words: paused, and with a protocol server, that the PLC keeps running. */
export function bannerText(protocols: string[]): string {
  if (!protocols.length) return "PAUSED: Simulation paused (the stand-in script, if any, pauses with it)";
  return `PAUSED: Simulation paused, PLC still running (${protocols.join(", ")}): its timers run on, and the heartbeat has stopped`;
}

/** What one Step runs, as the button says it. */
export const STEP_LABEL = "Step 0.1 s";

export interface PauseLink {
  pause(paused: boolean): Promise<void>;
  step(): Promise<void>;
}

export class PauseControls {
  readonly pauseButton = document.createElement("button");
  readonly stepButton = document.createElement("button");
  readonly banner = document.createElement("div");
  readonly controls = document.createElement("div");
  private paused = false;
  private protocols: string[] = [];
  private readonly link: PauseLink;

  constructor(link: PauseLink) {
    this.link = link;
    const button = (b: HTMLButtonElement, id: string, text: string, title: string) => {
      b.id = id;
      b.textContent = text;
      b.title = title;
      b.style.cssText =
        `font:12px Arial,sans-serif;color:${cssColor("normalText")};` +
        "background:#1f2937;border:1px solid #4b5563;border-radius:3px;padding:2px 6px;cursor:pointer";
    };
    button(this.pauseButton, "pause", "Pause", "Pause the simulation (Space)");
    button(this.stepButton, "step", STEP_LABEL, "Pause and run 0.1 s of simulated time");
    this.pauseButton.addEventListener("click", () => void this.link.pause(!this.paused));
    this.stepButton.addEventListener("click", () => void this.link.step());
    // one row beside Capture, so a wider "Resume" never covers Step
    this.controls.id = "pause-controls";
    this.controls.style.cssText = "position:absolute;bottom:12px;left:436px;display:flex;gap:6px";
    this.controls.append(this.pauseButton, this.stepButton);
    this.banner.id = "pause-banner";
    this.banner.style.cssText =
      `display:none;position:absolute;bottom:44px;left:50%;transform:translateX(-50%);z-index:10;width:max-content;max-width:min(560px,calc(100vw - 32px));` +
      `font:13px Arial,sans-serif;color:${cssColor("normalText")};background:rgba(17,24,39,0.92);` +
      `border:2px solid ${cssColor("alarmMedium")};border-radius:4px;padding:4px 10px;text-align:center`;
  }

  setProtocols(protocols: string[]): void {
    this.protocols = protocols;
    if (this.paused) this.banner.textContent = bannerText(protocols);
  }

  /** Show or hide the controls (they are offered only on a live run). */
  offer(on: boolean): void {
    this.controls.style.display = on ? "flex" : "none";
    if (!on) this.banner.style.display = "none";
  }

  /** Follow the frames: paused or not. */
  show(paused: boolean): void {
    if (paused === this.paused) return;
    this.paused = paused;
    this.pauseButton.textContent = paused ? "Resume" : "Pause";
    this.pauseButton.title = paused ? "Resume the simulation (Space)" : "Pause the simulation (Space)";
    this.banner.textContent = bannerText(this.protocols);
    this.banner.style.display = paused ? "block" : "none";
  }

  get isPaused(): boolean {
    return this.paused;
  }

  /** Space: pause, or resume when paused. */
  toggle(): Promise<void> {
    return this.link.pause(!this.paused);
  }
}
