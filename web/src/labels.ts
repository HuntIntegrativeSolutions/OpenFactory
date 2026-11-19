// Values in the picture (PLAN 5.14d): what a component's label says, and where labels that would
// overlap go. DOM-free, so labels.test.ts runs under node.
//
// Three settings: `ids` (the component's id, as before 5.14d), `tags` (the id, then each tag the
// component owns with the value the frame's `io` gives it), `off` (no label, except a faulted
// component's, so a fault is never hidden by the setting). The bindings come from the scene
// document's `tags` (each tag's `owner`), so nothing is added to the document. A tag the frame
// has no value for yet shows "–": no value is invented. Text is the normal colour; the alarm
// colour stays for faults only (ISA-101, isa101.ts).
import type { SceneDocument } from "./api";

export type LabelMode = "ids" | "tags" | "off";
export const LABEL_MODES: LabelMode[] = ["ids", "tags", "off"];

/** Past this many components only the selected component and its neighbours show values. */
export const MAX_VALUE_LABELS = 40;

/** `?labels=` : ids (the default), tags or off; anything else is ids with a warning. */
export function parseLabels(raw: string | null): { mode: LabelMode; warning: string | null } {
  if (raw === null) return { mode: "ids", warning: null };
  if ((LABEL_MODES as string[]).includes(raw)) return { mode: raw as LabelMode, warning: null };
  return { mode: "ids", warning: `?labels=${raw} is not ids, tags or off; ids are shown` };
}

/** Each component's own tags, in the document's order (a composite's part owns its own). */
export function boundTags(doc: SceneDocument): Map<string, { name: string; dtype: string }[]> {
  const out = new Map<string, { name: string; dtype: string }[]>();
  for (const tag of doc.tags) {
    const list = out.get(tag.owner) ?? [];
    list.push({ name: tag.name, dtype: tag.dtype });
    out.set(tag.owner, list);
  }
  return out;
}

/** A value as the label shows it: a bool as `true` or `false` (as /api/state gives it), a
 * number to 3 decimals with trailing zeros dropped, "–" when the frame has none. */
export function formatValue(value: unknown): string {
  if (value === undefined || value === null) return "–";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return String(value);
    if (Number.isInteger(value)) return String(value);
    return String(Number(value.toFixed(3)));
  }
  return String(value);
}

/** The lines of one label: null for no label (off, no fault). */
export function labelLines(
  mode: LabelMode,
  id: string,
  tags: { name: string }[],
  io: Record<string, unknown> | null,
  fault: string | null,
  values = true,
): string[] | null {
  const head = fault ? `${id} ⚠ ${fault.replace("_", " ")}` : id;
  if (mode === "off") return fault ? [head] : null;
  if (mode === "ids" || !values) return [head];
  return [head, ...tags.map((t) => `${t.name} ${formatValue(io ? io[t.name] : undefined)}`)];
}

/** Which components show values in tags mode: every one up to MAX_VALUE_LABELS components;
 * past that only `focus` (the selected component and its neighbours). */
export function showsValues(componentCount: number, id: string, focus: ReadonlySet<string>): boolean {
  return componentCount <= MAX_VALUE_LABELS || focus.has(id);
}

export interface LabelRect {
  id: string;
  left: number;
  top: number;
  width: number;
  height: number;
}

function overlap(a: { left: number; top: number; width: number; height: number }, b: typeof a, gap: number): boolean {
  return a.left < b.left + b.width + gap && b.left < a.left + a.width + gap && a.top < b.top + b.height + gap && b.top < a.top + a.height + gap;
}

/**
 * Stack labels that would overlap: each label, taken top to bottom (then left to right, then by
 * id, so the order never depends on the order they were drawn in), moves down below every label
 * already placed that it would overlap, until it overlaps none. Returns each label's downward
 * shift in pixels (0 for one that stays). A pure function of the rects, so the same picture
 * stacks the same way every frame.
 */
export function stackLabels(rects: LabelRect[], gap = 2): Map<string, number> {
  const order = [...rects].sort((a, b) => a.top - b.top || a.left - b.left || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  const placed: LabelRect[] = [];
  const out = new Map<string, number>();
  for (const rect of order) {
    const at = { ...rect };
    for (let moved = true, guard = 0; moved && guard <= placed.length; guard++) {
      moved = false;
      for (const other of placed) {
        if (overlap(at, other, gap)) {
          at.top = other.top + other.height + gap;
          moved = true;
        }
      }
    }
    placed.push(at);
    out.set(rect.id, at.top - rect.top);
  }
  return out;
}

/** The longest program line the status lines show (PLAN 5.14i). */
export const MAX_WATCH_LINE = 160;

/** The stand-in script's watch values (PLAN 5.14i) as one status line, "program: step 20 ·
 * boxes_in_transit 1", for `labels=tags`; null with none. Text only (the page sets it as
 * textContent), control characters dropped, cut to MAX_WATCH_LINE. */
export function watchLine(watch: Record<string, unknown> | undefined): string | null {
  if (!watch) return null;
  const items = Object.entries(watch).map(([name, value]) => `${name} ${formatValue(value)}`);
  if (!items.length) return null;
  // eslint-disable-next-line no-control-regex
  const line = `program: ${items.join(" · ")}`.replace(/[\u0000-\u001f\u007f]/g, "");
  return line.length > MAX_WATCH_LINE ? `${line.slice(0, MAX_WATCH_LINE - 1)}…` : line;
}
