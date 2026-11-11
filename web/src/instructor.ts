// Instructor view (PLAN 4.12, ruled 2026-10-30): /instructor is the same viewer, connected to
// /ws/instructor with the fault token. Its frames carry every active fault, so fault markers
// show even in exam mode, where /ws/state hides them from trainees; its exam switches are shown
// read-only, so the instructor watches without touching the trainees' inputs. The token comes
// from the URL fragment (/instructor#token=..., never sent to the server in the URL, and removed
// from the address bar at once) or from this tab's stored token; the socket sends it as its
// first message.
import type { StateFrame } from "./api";

/** Whether this page is the instructor view. */
export function isInstructorPath(pathname: string): boolean {
  return pathname.replace(/\/+$/, "") === "/instructor";
}

/** The token in a URL fragment (`#token=...`), or null. */
export function tokenFromHash(hash: string): string | null {
  const value = new URLSearchParams(hash.replace(/^#/, "")).get("token");
  return value ? value.trim() || null : null;
}

/** The fault to mark on a component: the instructor frame's list, else the component's own
 * state (absent in exam mode for trainees, by design). */
export function faultOf(frame: StateFrame, id: string): string | null {
  if (frame.instructor) return frame.faults?.[id] ?? null;
  const fault = frame.components[id]?.fault;
  return typeof fault === "string" ? fault : null;
}

/** A refusal from /ws/instructor (`{"error", "refused": true}`), or null for a frame. */
export function refusalOf(message: unknown): string | null {
  if (typeof message !== "object" || message === null) return null;
  const m = message as { refused?: unknown; error?: unknown };
  return m.refused === true ? String(m.error ?? "refused") : null;
}
