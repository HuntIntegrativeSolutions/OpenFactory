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

export class EditOps {
  private ops = new Map<string, EditOp>();
  private added = new Map<string, Added>();
  private placeholders = new Map<string, string>();
  /** New components whose description is still the placeholder: nothing is saved until each
   * has its own (ruled 2026-11-10, decision 5). */
  readonly undescribed = new Set<string>();

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
  change(op: PathOp): void {
    this.ops.set(pathKey(op.path), op);
    const [top, id, key] = op.path;
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
    this.ops.clear();
    this.added.clear();
    this.placeholders.clear();
    this.undescribed.clear();
  }
}
