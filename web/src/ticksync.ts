// Tick-locked viewer physics (PLAN 2.0a). The viewer never steps Rapier on wall-clock time: each
// /ws/state frame carries the core tick, and the viewer steps exactly (tick - lastTick) times, at
// most MAX_STEPS_PER_FRAME, and never between frames. So browser physics advances exactly as far
// as the core has, whatever the frame rate, and a slow renderer (software WebGL) drops ticks
// visibly (counted in `dropped`) instead of silently falling behind.
// DOM-free so web/src/ticksync.test.ts can test it under node.

export const MAX_STEPS_PER_FRAME = 10;

export interface StepPlan {
  /** Physics steps to run for this frame. */
  steps: number;
  /** Core ticks skipped because of the cap (the browser world loses that much time). */
  dropped: number;
  /** Core tick the first step stands for (the last `steps` ticks up to the frame's tick). */
  firstTick: number;
  /** True on the first frame, or when the core's tick went backwards (a restarted run). */
  reset: boolean;
}

export function planSteps(lastTick: number | null, tick: number, max = MAX_STEPS_PER_FRAME): StepPlan {
  if (lastTick === null || tick < lastTick) return { steps: 0, dropped: 0, firstTick: tick + 1, reset: true };
  const due = tick - lastTick;
  const steps = Math.min(due, max);
  return { steps, dropped: due - steps, firstTick: tick - steps + 1, reset: false };
}

/** Pusher stroke for step `i` (1-based) of `steps`, moving linearly from `from` to `to`. */
export function strokeAt(from: number, to: number, i: number, steps: number): number {
  return steps <= 0 ? to : from + ((to - from) * i) / steps;
}
