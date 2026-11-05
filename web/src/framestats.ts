// Frame-time statistics for the stats overlay (PLAN 2.9): the interval between rendered frames,
// which is what a viewer sees as smoothness (60 fps = 16.7 ms), whatever the time went on
// (physics, scene updates, the GPU). DOM-free so web/src/framestats.test.ts runs under node.

export class FrameStats {
  private readonly stamps: number[] = [];
  private readonly costs: number[] = [];

  private readonly size: number;

  /** `size`: how many recent frames the statistics cover. */
  constructor(size = 120) {
    this.size = size;
  }

  /** Record a rendered frame at time `now` (ms, e.g. the requestAnimationFrame timestamp) that
   * took `costMs` of main-thread time to render. */
  frame(now: number, costMs = 0): void {
    this.stamps.push(now);
    this.costs.push(costMs);
    if (this.stamps.length > this.size + 1) this.stamps.shift();
    if (this.costs.length > this.size) this.costs.shift();
  }

  private intervals(): number[] {
    return this.stamps.slice(1).map((t, i) => t - this.stamps[i]);
  }

  /** Mean ms between consecutive frames, or null before two frames. */
  get meanMs(): number | null {
    const d = this.intervals();
    return d.length ? d.reduce((a, b) => a + b, 0) / d.length : null;
  }

  /** 95th percentile ms between frames (the stutter a mean hides), or null before two frames. */
  get p95Ms(): number | null {
    const d = this.intervals().sort((a, b) => a - b);
    return d.length ? d[Math.min(d.length - 1, Math.ceil(0.95 * d.length) - 1)] : null;
  }

  /** Mean main-thread ms spent rendering a frame. */
  get renderMs(): number {
    return this.costs.length ? this.costs.reduce((a, b) => a + b, 0) / this.costs.length : 0;
  }
}
