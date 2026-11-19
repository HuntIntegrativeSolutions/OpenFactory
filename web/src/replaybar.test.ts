import { describe, expect, it } from "vitest";
import * as THREE from "three";
import { PartMeshes } from "./parts";
import { frameTick, isJump, jumpLimit, timeText } from "./replaybar";

const sorter = { first_tick: 0, last_tick: 3000, frame_every: 5, dt: 0.02 };
const piece = { first_tick: 755, last_tick: 3752, frame_every: 5, dt: 0.02 }; // a capture's piece

describe("the replay bar (PLAN 5.14f)", () => {
  it("turns a slider value into the recorded frame at or before it", () => {
    expect(frameTick(300, sorter)).toBe(300);
    expect(frameTick(304, sorter)).toBe(300);
    expect(frameTick(-10, sorter)).toBe(0);
    expect(frameTick(9999, sorter)).toBe(3000);
    expect(frameTick(800, piece)).toBe(800);
    expect(frameTick(3751, piece)).toBe(3750);
    expect(frameTick(3752, piece)).toBe(3752); // the capture's own last frame, off the grid
  });

  it("calls a frame a jump when it goes back or skips more than two frames", () => {
    expect(isJump(null, 0)).toBe(true);
    expect(isJump(300, 305)).toBe(false);
    expect(isJump(300, 310)).toBe(false);
    expect(isJump(300, 315)).toBe(true);
    expect(isJump(300, 295)).toBe(true);
  });

  it("does not call a fast replay's frames jumps (the viewer gets 20 frames a second)", () => {
    // at 4x the state frames are ~10 ticks apart (up to 15): they ease, as before 5.14f
    const four = jumpLimit({ speed: 4, dt: 0.02, frame_every: 5 });
    expect(four).toBe(30);
    expect(isJump(300, 315, four)).toBe(false);
    // at 10x every frame is ~25 ticks on: still not a jump
    const ten = jumpLimit({ speed: 10, dt: 0.02, frame_every: 5 });
    expect(isJump(300, 340, ten)).toBe(false);
    expect(isJump(300, 300 + ten + 1, ten)).toBe(true);
    expect(isJump(300, 295, ten)).toBe(true); // back is always a jump
    expect(jumpLimit({ speed: 1, dt: 0.02, frame_every: 5 })).toBe(10); // 1x: two frames
  });

  it("writes the time shown and the recording's length", () => {
    expect(timeText(300, sorter)).toBe("6.00 s / 60.00 s");
  });

  it("puts parts where they are headed at once after a jump, and knows when they are there", () => {
    const parts = new PartMeshes();
    const box = { id: "P1", name: "tall", size: [0.2, 0.3, 0.2] as [number, number, number], position: [0, 0, 0] as [number, number, number], carrier: null };
    parts.update([box]);
    expect(parts.settled()).toBe(true);
    parts.update([{ ...box, position: [1, 0, 0] }]);
    expect(parts.settled()).toBe(false);
    parts.animate();
    expect(parts.settled()).toBe(false); // eased part of the way
    parts.snap();
    expect(parts.settled()).toBe(true);
    const mesh = parts.group.children[0] as THREE.Mesh;
    expect(mesh.position.x).toBe(1);
  });
});
