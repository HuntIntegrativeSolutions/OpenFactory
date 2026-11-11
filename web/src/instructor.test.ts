import { describe, expect, it } from "vitest";
import type { StateFrame } from "./api";
import { faultOf, isInstructorPath, refusalOf, tokenFromHash } from "./instructor";

const frame = (extra: Partial<StateFrame>): StateFrame => ({
  t: 0, tick: 1, components: { PE_01: {}, CONV_01: { fault: "jam" } }, parts: [], io: {}, ...extra,
});

describe("the instructor view (PLAN 4.12)", () => {
  it("is /instructor, with or without a trailing slash, and nothing else", () => {
    expect(isInstructorPath("/instructor")).toBe(true);
    expect(isInstructorPath("/instructor/")).toBe(true);
    expect(isInstructorPath("/")).toBe(false);
    expect(isInstructorPath("/instructors")).toBe(false);
  });

  it("reads the token from the fragment only", () => {
    expect(tokenFromHash("#token=s3cret")).toBe("s3cret");
    expect(tokenFromHash("#token=a%20b")).toBe("a b");
    expect(tokenFromHash("")).toBeNull();
    expect(tokenFromHash("#token=")).toBeNull();
  });

  it("marks faults from the instructor frame's list, which exam mode does not hide", () => {
    const teacher = frame({ instructor: true, faults: { PE_01: "misaligned" } });
    expect(faultOf(teacher, "PE_01")).toBe("misaligned");
    expect(faultOf(teacher, "CONV_01")).toBeNull(); // the list is the truth for an instructor
    const trainee = frame({});
    expect(faultOf(trainee, "CONV_01")).toBe("jam");
    expect(faultOf(trainee, "PE_01")).toBeNull();
  });

  it("tells a refusal from a frame", () => {
    expect(refusalOf({ error: "wrong token", refused: true })).toBe("wrong token");
    expect(refusalOf({ tick: 3 })).toBeNull();
    expect(refusalOf(null)).toBeNull();
  });
});
