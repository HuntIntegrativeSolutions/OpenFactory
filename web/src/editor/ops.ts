// The editor's pending operations (PLAN 4.1c), in the order the server applies them
// (core/sceneedit.py). A later change to the same value replaces the earlier one in place; a new
// component's `add` comes after the `use_library` its library composite needs; deleting a
// component that is not saved yet takes back its `add` and every change to it instead of sending
// a `delete`. Pure: no DOM, unit-tested in ops.test.ts.
import type { EditOp, Path, PathOp } from "./forms";

function pathKey(path: Path): string {
  return "set\u0000" + path.join("\u0000");
}

interface Added {
  library: string | null;
  placeholder: string;
}

/** Two scene values equal (numbers to 1e-9). */
function sameValue(a: unknown, b: unknown): boolean {
  if (typeof a === "number" && typeof b === "number") return Math.abs(a - b) <= 1e-9;
  if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((v, i) => sameValue(v, b[i]));
  return a === b && a !== undefined;
}

export class EditOps {
  private ops = new Map<string, EditOp>();
  private added = new Map<string, Added>();
  private placeholders = new Map<string, string>();
  /** New components whose description is still the placeholder: nothing is saved until each
   * has its own (ruled 2026-11-10, decision 5). */
  readonly undescribed = new Set<string>();
  /** Whether a component carries `laid_out` (PLAN 5.5). Moving or re-typing its position clears
   * the mark: a person placed it (ruled 2026-11-19, second set). The panel sets this. */
  laidOut: (id: string) => boolean = () => false;
  /** A value as the file on disk has it (the panel sets this): a position moved back to it is
   * no change, and the mark it cleared comes back (ruled 2026-11-20, gap 2d). */
  fileValue: (path: Path) => unknown = () => undefined;
  /** Marks this object cleared by a move, not by the person's own tick of the checkbox. */
  private clearedByMove = new Set<string>();

  get size(): number {
    return this.ops.size;
  }

  list(): EditOp[] {
    return [...this.ops.values()];
  }

  /** Ids of components added since the last save, in the order they were added. */
  addedIds(): string[] {
    return [...this.added.keys()];
  }

  isAdded(id: string): boolean {
    return this.added.has(id);
  }

  /** A component in the file (or the scene, as "scene") whose description is still the
   * placeholder `openfactory new` or the palette wrote: held like a new one (2026-11-13). */
  holdPlaceholder(id: string, placeholder: string): void {
    this.placeholders.set(id, placeholder);
    this.undescribed.add(id);
  }

  /** A change to one value (set, set_raw, unset) of a component, or of the scene. */
  change(op: PathOp, opts: { keepMark?: boolean } = {}): void {
    const [top, id, key] = op.path;
    const mark: Path = ["components", String(id), "laid_out"];
    if (top === "components" && key === "laid_out" && op.path.length === 3) this.clearedByMove.delete(String(id));
    this.ops.set(pathKey(op.path), op);
    if (top === "components" && key !== "position" && op.op === "set" && sameValue(op.value, this.fileValue(op.path))) {
      // set back to the value the file has (a belt moved and carried back, its devices' turns):
      // no change (review of 2026-11-20, second set, 1c); a description still goes through the
      // placeholder bookkeeping below
      this.ops.delete(pathKey(op.path));
      if (key !== "description") return;
    }
    if (top === "components" && key === "position" && this.positionIsFiles(String(id))) {
      // moved back to where the file has it (dragged or typed): no change, and a mark the move
      // cleared returns (review of 2026-11-20, gap 2d)
      for (const [k, o] of this.ops) {
        if ("path" in o && o.path[0] === "components" && o.path[1] === id && o.path[2] === "position") this.ops.delete(k);
      }
      if (this.clearedByMove.delete(String(id))) this.ops.delete(pathKey(mark));
      return;
    }
    if (top === "components" && key === "position" && op.op !== "unset" && !opts.keepMark && this.laidOut(String(id))) {
      // a later tick of the checkbox sets the same path, so it replaces this
      this.ops.set(pathKey(mark), { op: "unset", path: mark });
      this.clearedByMove.add(String(id));
    }
    // the description a change writes, and whose: a component's, or the scene's own
    const owner = top === "components" && key === "description" && op.path.length === 3 ? String(id)
      : top === "scene" && id === "description" && op.path.length === 2 ? "scene" : null;
    if (owner === null) return;
    const placeholder = this.added.get(owner)?.placeholder ?? this.placeholders.get(owner);
    if (placeholder === undefined) return;
    const value = op.op === "set" ? op.value : null;
    if (typeof value === "string" && value.trim() && value !== placeholder) this.undescribed.delete(owner);
    else this.undescribed.add(owner);
  }

  /** Whether the pending position of component `id` (a whole value, or typed axis by axis)
   * is the file's own. */
  private positionIsFiles(id: string): boolean {
    const file = this.fileValue(["components", id, "position"]);
    if (!Array.isArray(file)) return false;
    const now = [...file];
    for (const o of this.ops.values()) {
      if (!("path" in o) || o.path[0] !== "components" || o.path[1] !== id || o.path[2] !== "position") continue;
      if (o.op !== "set") return false;
      if (o.path.length === 3 && Array.isArray(o.value)) now.splice(0, now.length, ...o.value);
      else if (o.path.length === 4 && typeof o.path[3] === "number") now[o.path[3]] = o.value;
      else return false;
    }
    return sameValue(now, file);
  }

  /** A new component; `library` when the scene must first add `{library: name}`. */
  add(component: Record<string, unknown>, library: string | null): void {
    const id = String(component.id);
    if (library && !this.ops.has(`lib\u0000${library}`)) {
      this.ops.set(`lib\u0000${library}`, { op: "use_library", name: library });
    }
    this.ops.set(`add\u0000${id}`, { op: "add", component });
    this.added.set(id, { library, placeholder: String(component.description ?? "") });
    this.undescribed.add(id);
  }

  /** A connection a snap made (PLAN 4.1d): `from`'s discharge feeds `to`'s infeed. */
  connect(from: string, to: string): void {
    this.ops.set(`connect\u0000${from}\u0000${to}`, { op: "connect", from, to });
  }

  /** Remove the connections and checks that name `ids` (PLAN 4.1f), after their delete. */
  unreference(ids: string[]): void {
    this.ops.set(`unref\u0000${ids.join("\u0000")}`, { op: "unreference", ids });
  }

  /** Delete a component: drop everything pending for it, and send a `delete` only for one
   * that is in the file. */
  remove(id: string): void {
    for (const [key, op] of this.ops) {
      if ("path" in op && op.path[0] === "components" && op.path[1] === id) this.ops.delete(key);
      if (op.op === "connect" && (op.from === id || op.to === id)) this.ops.delete(key);
    }
    this.placeholders.delete(id);
    if (!this.added.has(id)) this.undescribed.delete(id);
    const added = this.added.get(id);
    if (added) {
      this.ops.delete(`add\u0000${id}`);
      this.added.delete(id);
      this.undescribed.delete(id);
      const stillUsed = [...this.added.values()].some((a) => a.library === added.library);
      if (added.library && !stillUsed) this.ops.delete(`lib\u0000${added.library}`);
      return;
    }
    this.ops.set(`delete\u0000${id}`, { op: "delete", id });
  }

  clear(): void {
    this.clearedByMove.clear();
    this.ops.clear();
    this.added.clear();
    this.placeholders.clear();
    this.undescribed.clear();
  }
}
