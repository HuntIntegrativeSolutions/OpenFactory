// Scene editor panel (PLAN 4.1b, 4.1c): with `openfactory edit SCENE` the viewer shows this panel
// on the right. Click a component to select it; its properties appear in a form built from the
// JSON Schema (forms.ts); each accepted change becomes an edit operation (ops.ts), the server
// previews the whole list against the file it was based on (server/editor.py) and returns the
// lint's findings and the scene document, which the viewer redraws. Save writes the file; the
// server refuses a stale base (409) or a scene with lint errors (422). Download YAML gives the
// same text without writing. The browser never writes YAML.
//
// 4.1c: the palette adds a built-in type or a composite (the scene's own, or a library one, which
// also adds `{library: name}` to `composites:`) where the view is centred; Move and Turn choose
// what the gizmo in the plant does (main.ts), Delete removes the selection, and a mesh is picked
// from the .glb files beside the scene. A new component's id, tags and starter values come from
// the server (core/templates.py); nothing is saved until each new one has its own description.
import { cssColor } from "../isa101";
import type { SceneDocument } from "../api";
import {
  buildForm,
  checkField,
  defForType,
  opFor,
  type Field,
  type Form,
  type JsonSchema,
} from "./forms";
import { EditOps } from "./ops";
import type { Placement } from "./place";

interface Finding {
  level: string;
  code: string;
  message: string;
}

interface Preview {
  base: string;
  findings: Finding[];
  errors: number;
  scene: SceneDocument | null;
  data: { components?: Record<string, unknown>[] } | null;
}

export interface EditorStatus {
  file: string;
  selected: string | null;
  ops: number;
  previews: number;
  saves: number;
  downloads: number;
  errors: number;
  findings: string[];
  message: string;
  added: string[];
  undescribed: string[];
  palette: string[];
  mode: "move" | "turn";
  /** The last snap a drag made (PLAN 4.1d): the ends that met and the connection it added. */
  snap: { id: string; mine: string; theirs: string; port: string; connection: { from: string; to: string } } | null;
  /** A snap candidate while dragging, or null. */
  snapping: string | null;
  /** A Delete waiting for the user (PLAN 4.1f): what it removes and what it leaves to fix. */
  confirm: { id: string; remove: string[]; fix: string[] } | null;
  /** Deletes done. */
  deletes: number;
  /** Where the last Add put its component (PLAN 4.1e): moved off the drop point, or snapped. */
  placed: { id: string; dropped: [number, number, number]; position: [number, number, number]; moved_m: number; snapped: string | null; waiting: boolean } | null;
}

export interface PaletteItem {
  type: string;
  kind: "primitive" | "composite" | "library";
  description: string;
  needs: string[];
  note: string;
  library?: string | null;
  in_scene?: boolean;
}

/** What the panel asks of the viewer around it (main.ts). */
export interface PanelHost {
  /** Draw a previewed scene document; resolves once it is drawn. */
  onScene(doc: SceneDocument): Promise<void>;
  /** Select a component the panel chose (one just added, or none after a delete). */
  onSelect(id: string | null): void;
  /** Where a new component goes: the floor point at the centre of the view. */
  placement(): [number, number, number];
  /** What dragging the selection does. */
  setMode(mode: "move" | "turn"): void;
  /** Where a component just added and drawn should go instead (PLAN 4.1e): clear of the plant,
   * or snapped to an open conveyor end; null to leave it where it was dropped. */
  settle(id: string, position: [number, number, number], rotationDeg: number): Placement | null;
  /** Whether a component is drawn in the plant now. */
  drawn(id: string): boolean;
}

const TEXT = cssColor("normalText");
const MUTED = "#9CA3AF";
const ALARM = cssColor("alarmHigh");
const WARN = cssColor("alarmMedium");

/** A value as one line of YAML flow text in the scenes' style: unquoted keys and plain
 * tag-like strings (`{name: good, size: [0.3, 0.2, 0.3]}`), not JSON. */
export function flowYaml(value: unknown): string {
  return JSON.stringify(value)
    .replace(/"([A-Za-z_][A-Za-z0-9_]*)":/g, "$1: ")
    .replace(/"([A-Za-z][A-Za-z0-9_]*)"/g, "$1")
    .replace(/,/g, ", ");
}

function getAt(obj: unknown, path: (string | number)[]): unknown {
  let cur: unknown = obj;
  for (const step of path) {
    if (cur === null || typeof cur !== "object") return undefined;
    cur = (cur as Record<string, unknown>)[String(step)];
  }
  return cur;
}

export class EditorPanel {
  readonly element: HTMLDivElement;
  readonly status: EditorStatus;
  private schema: JsonSchema = {};
  private base = "";
  private data: Preview["data"] = null;
  private readonly ops = new EditOps();
  /** Added components still waiting to be drawn and placed (PLAN 4.1e). */
  private readonly unplaced = new Set<string>();
  /** The block the form being built edits: `["components", id]`, `["scene"]` or `["checks"]`. */
  private root: (string | number)[] = [];
  private selected: string | null = null;
  private findings: Finding[] = [];
  private errors = 0;
  private palette: PaletteItem[] = [];
  private meshes: string[] = [];
  private readonly host: PanelHost;
  private body: HTMLDivElement;
  private footer: HTMLDivElement;
  private saveButton: HTMLButtonElement;
  private revertButton: HTMLButtonElement;
  private downloadButton: HTMLButtonElement;
  private paletteSelect: HTMLSelectElement;
  private tools: HTMLDivElement;
  private confirmBox: HTMLDivElement;
  private moveButton: HTMLButtonElement;
  private turnButton: HTMLButtonElement;

  constructor(host: PanelHost) {
    this.host = host;
    this.status = {
      file: "", selected: null, ops: 0, previews: 0, saves: 0, downloads: 0, errors: 0, findings: [],
      message: "", added: [], undescribed: [], palette: [], mode: "move", snap: null, snapping: null,
      placed: null, confirm: null, deletes: 0,
    };
    this.element = document.createElement("div");
    this.element.id = "editor-panel";
    this.element.style.cssText =
      "position:absolute;top:0;right:0;bottom:0;width:min(360px,calc(100vw - 32px));z-index:10;" +
      `overflow-y:auto;background:#111827;border-left:1px solid #4b5563;color:${TEXT};` +
      "font:13px Arial,sans-serif;padding:10px 12px;box-sizing:border-box";
    const head = document.createElement("div");
    head.id = "editor-head";
    head.style.cssText = "font-weight:700;margin-bottom:6px";
    head.textContent = "EDIT MODE";
    const buttons = document.createElement("div");
    buttons.style.cssText = "display:flex;gap:8px;margin:6px 0 10px";
    this.saveButton = this.button("Save", () => void this.save());
    this.saveButton.id = "editor-save";
    this.revertButton = this.button("Revert", () => void this.revert());
    this.revertButton.id = "editor-revert";
    this.downloadButton = this.button("Download YAML", () => void this.download());
    this.downloadButton.id = "editor-download";
    buttons.append(this.saveButton, this.revertButton, this.downloadButton);
    // the palette: every built-in type, then the scene's and the libraries' composites
    const add = document.createElement("div");
    add.style.cssText = "display:flex;gap:6px;margin:0 0 8px";
    this.paletteSelect = document.createElement("select");
    this.paletteSelect.id = "editor-palette";
    this.paletteSelect.style.cssText = `flex:1;min-width:0;background:#0b1220;color:${TEXT};border:1px solid #4b5563`;
    const addButton = this.button("Add", () => void this.addComponent(this.paletteSelect.value));
    addButton.id = "editor-add";
    add.append(this.paletteSelect, addButton);
    // what the gizmo does to the selection, and delete
    this.tools = document.createElement("div");
    this.tools.style.cssText = "display:flex;gap:6px;margin:0 0 8px";
    this.moveButton = this.button("Move", () => this.setMode("move"));
    this.moveButton.id = "editor-mode-move";
    this.turnButton = this.button("Turn", () => this.setMode("turn"));
    this.turnButton.id = "editor-mode-turn";
    const del = this.button("Delete", () => void this.deleteSelected());
    del.id = "editor-delete";
    this.tools.append(this.moveButton, this.turnButton, del);
    this.body = document.createElement("div");
    this.body.id = "editor-form";
    this.footer = document.createElement("div");
    this.footer.id = "editor-findings";
    this.footer.style.cssText = "margin-top:12px;font-size:12px";
    // PLAN 4.1f: what a Delete takes with it, shown until the user confirms or cancels
    this.confirmBox = document.createElement("div");
    this.confirmBox.id = "editor-confirm";
    this.confirmBox.style.cssText = `display:none;margin:0 0 10px;padding:8px;border:1px solid ${WARN};font-size:12px`;
    this.element.append(head, buttons, add, this.tools, this.confirmBox, this.body, this.footer);
    this.setMode("move");
    document.body.appendChild(this.element);
    this.refreshButtons();
  }

  private button(text: string, onClick: () => void): HTMLButtonElement {
    const b = document.createElement("button");
    b.textContent = text;
    b.style.cssText =
      "padding:5px 12px;border:1px solid #4b5563;border-radius:3px;background:#1f2937;cursor:pointer;" +
      `color:${TEXT};font:13px Arial,sans-serif`;
    b.addEventListener("click", onClick);
    return b;
  }

  /** Read the schema, the file and the palette; show the hint until something is selected. */
  async start(): Promise<void> {
    this.schema = (await (await fetch("/scene.schema.json")).json()) as JsonSchema;
    await this.loadSource();
    await this.loadPalette();
    this.renderForm();
  }

  private post(url: string, extra: Record<string, unknown> = {}): Promise<Response> {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ base: this.base, ops: this.ops.list(), ...extra }),
    });
  }

  private async loadPalette(): Promise<void> {
    const res = await this.post("/api/edit/palette");
    if (!res.ok) return;
    const body = (await res.json()) as { items: PaletteItem[]; meshes: string[] };
    this.palette = body.items;
    this.meshes = body.meshes;
    this.status.palette = this.palette.map((p) => p.type);
    const kept = this.paletteSelect.value;
    this.paletteSelect.replaceChildren();
    const groups: [string, PaletteItem["kind"]][] = [
      ["Built-in", "primitive"],
      ["This scene's composites", "composite"],
      ["Library composites", "library"],
    ];
    for (const [label, kind] of groups) {
      const items = this.palette.filter((p) => p.kind === kind);
      if (!items.length) continue;
      const group = document.createElement("optgroup");
      group.label = label;
      for (const item of items) {
        const opt = document.createElement("option");
        opt.value = item.type;
        const extra = item.needs.length ? ` (fill in ${item.needs.join(", ")})` : item.note ? ` (${item.note})` : "";
        opt.textContent = item.type.replace(/_/g, " ") + extra;
        opt.title = item.description;
        group.appendChild(opt);
      }
      this.paletteSelect.appendChild(group);
    }
    if (kept) this.paletteSelect.value = kept;
  }

  private setMode(mode: "move" | "turn"): void {
    this.status.mode = mode;
    for (const [b, m] of [[this.moveButton, "move"], [this.turnButton, "turn"]] as const) {
      b.style.background = m === mode ? cssColor("highlight") : "#1f2937";
      b.style.color = m === mode ? "#0b1220" : TEXT;
    }
    this.host.setMode(mode);
  }

  /** The selected component's values as the file (with pending edits) has them. */
  componentData(id: string): Record<string, unknown> | null {
    const list = this.data?.components ?? [];
    return (list.find((c) => c.id === id) as Record<string, unknown> | undefined) ?? null;
  }

  /** A drag ended on a snap (PLAN 4.1d): the snapped pose and the connection it makes. */
  snapTo(id: string, position: [number, number, number], rotationDeg: number, connection: { from: string; to: string }): void {
    this.ops.change({ op: "set", path: ["components", id, "position"], value: position });
    this.ops.change({ op: "set", path: ["components", id, "rotation_deg"], value: rotationDeg });
    this.ops.connect(connection.from, connection.to);
    void this.preview();
  }

  /** A drag in the plant ended (main.ts): the new position and, after a turn, rotation. */
  moveTo(id: string, position: [number, number, number], rotationDeg: number | null): void {
    this.ops.change({ op: "set", path: ["components", id, "position"], value: position });
    if (rotationDeg !== null) this.ops.change({ op: "set", path: ["components", id, "rotation_deg"], value: rotationDeg });
    void this.preview();
  }

  private async addComponent(type: string): Promise<void> {
    if (!type) return;
    const res = await this.post("/api/edit/new", { type, position: this.host.placement() });
    if (!res.ok) {
      this.renderFindings(`Not added (${res.status}): ${String((await res.json()).detail)}`);
      return;
    }
    const made = (await res.json()) as { component: Record<string, unknown>; library: string | null };
    this.ops.add(made.component, made.library);
    // PLAN 4.1e: once it is drawn, preview() moves it clear of the plant or snaps it to an open
    // end; one that cannot be drawn yet (a reference to fill in) is placed when it first is
    const id = String(made.component.id);
    const dropped = made.component.position as [number, number, number];
    this.unplaced.add(id);
    await this.preview();
    if (this.unplaced.has(id)) {
      this.status.placed = { id, dropped, position: dropped, moved_m: 0, snapped: null, waiting: true };
    }
    this.host.onSelect(id);
  }

  /** Components added and drawn but not yet placed (PLAN 4.1e): settle each, once. */
  private async placeAdded(): Promise<void> {
    for (const id of [...this.unplaced]) {
      if (!this.componentData(id)) {
        this.unplaced.delete(id); // deleted before it could be placed
        continue;
      }
      if (!this.host.drawn(id)) continue;
      this.unplaced.delete(id);
      const dropped = this.componentData(id)?.position as [number, number, number];
      const turned = Number(this.componentData(id)?.rotation_deg ?? 0);
      const placed = this.host.settle(id, dropped, turned);
      if (placed) {
        this.ops.change({ op: "set", path: ["components", id, "position"], value: placed.position });
        if (Math.abs(placed.rotation_deg - turned) > 1e-9) {
          this.ops.change({ op: "set", path: ["components", id, "rotation_deg"], value: placed.rotation_deg });
        }
        if (placed.connection) this.ops.connect(placed.connection.from, placed.connection.to);
      }
      if (placed) await this.preview();
      // last: a test waiting for it sees the preview with the component in place
      this.status.placed = {
        id, dropped, position: placed?.position ?? dropped, moved_m: placed?.moved_m ?? 0, snapped: placed?.snapped ?? null, waiting: false,
      };
    }
  }

  async deleteSelected(): Promise<void> {
    const id = this.selected;
    if (!id || !this.componentData(id)) return;
    if (this.ops.isAdded(id)) {
      await this.deleteNow(id, []); // not in the file: nothing there names it
      return;
    }
    // PLAN 4.1f: list what goes with it, and what is left to fix, before anything changes
    const res = await this.post("/api/edit/references", { id });
    if (!res.ok) {
      this.renderFindings(`Not deleted (${res.status}): ${String((await res.json()).detail)}`);
      return;
    }
    const refs = (await res.json()) as { ids: string[]; remove: { text: string }[]; fix: { text: string }[] };
    if (!refs.remove.length && !refs.fix.length) {
      await this.deleteNow(id, []);
      return;
    }
    this.askDelete(id, refs.ids, refs.remove.map((r) => r.text), refs.fix.map((r) => r.text));
  }

  private askDelete(id: string, ids: string[], remove: string[], fix: string[]): void {
    this.status.confirm = { id, remove, fix };
    this.confirmBox.replaceChildren();
    const title = document.createElement("div");
    title.style.cssText = "font-weight:700;margin-bottom:4px";
    title.textContent = `Delete ${id}?`;
    this.confirmBox.append(title);
    const list = (heading: string, items: string[], listId: string) => {
      if (!items.length) return;
      const h = document.createElement("div");
      h.textContent = heading;
      h.style.cssText = "margin-top:4px";
      const ul = document.createElement("ul");
      ul.id = listId;
      ul.style.cssText = "margin:2px 0 4px 18px;padding:0";
      for (const text of items) {
        const li = document.createElement("li");
        li.textContent = text;
        ul.append(li);
      }
      this.confirmBox.append(h, ul);
    };
    list("It also removes:", remove, "editor-confirm-remove");
    list("Left for you to fix (the scene will not load until you do):", fix, "editor-confirm-fix");
    const row = document.createElement("div");
    row.style.cssText = "display:flex;gap:8px;margin-top:6px";
    const yes = this.button(`Delete ${id}`, () => void this.deleteNow(id, remove.length ? ids : []));
    yes.id = "editor-confirm-delete";
    const no = this.button("Cancel", () => this.closeConfirm());
    no.id = "editor-confirm-cancel";
    row.append(yes, no);
    this.confirmBox.append(row);
    this.confirmBox.style.display = "block";
  }

  private closeConfirm(): void {
    this.status.confirm = null;
    this.confirmBox.style.display = "none";
    this.confirmBox.replaceChildren();
  }

  private async deleteNow(id: string, unreference: string[]): Promise<void> {
    this.closeConfirm();
    this.ops.remove(id);
    if (unreference.length) this.ops.unreference(unreference);
    await this.preview();
    this.host.onSelect(null);
    this.status.deletes += 1;
  }

  private async download(): Promise<void> {
    const res = await this.post("/api/edit/preview");
    if (!res.ok) {
      this.renderFindings(`Not downloaded (${res.status})`);
      return;
    }
    const p = (await res.json()) as Preview & { text: string };
    const url = URL.createObjectURL(new Blob([p.text], { type: "application/yaml" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = this.status.file || "scene.yaml";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    this.status.downloads += 1;
  }

  private async loadSource(): Promise<void> {
    const src = (await (await fetch("/api/edit/source")).json()) as { name: string; base: string; data: Preview["data"] };
    this.base = src.base;
    this.data = src.data;
    this.status.file = src.name;
    // a description still the placeholder `openfactory new` or the palette wrote holds Save
    for (const c of (this.data?.components ?? []) as Record<string, unknown>[]) {
      const placeholder = `New ${String(c.type).replace(/_/g, " ")}`;
      if (c.description === placeholder && !this.ops.undescribed.has(String(c.id))) this.ops.holdPlaceholder(String(c.id), placeholder);
    }
    const scene = (this.data as { scene?: { description?: unknown } } | null)?.scene;
    if (scene?.description === "New scene") this.ops.holdPlaceholder("scene", "New scene");
    (this.element.querySelector("#editor-head") as HTMLDivElement).textContent = `EDIT MODE · ${src.name}`;
  }

  select(id: string | null): void {
    if (this.status.confirm && this.status.confirm.id !== id) this.closeConfirm();
    this.selected = id;
    this.status.selected = id;
    this.renderForm();
  }

  private component(): Record<string, unknown> | null {
    return this.selected ? this.componentData(this.selected) : null;
  }

  private renderForm(): void {
    this.body.replaceChildren();
    const comp = this.component();
    this.tools.style.display = comp ? "flex" : "none";
    if (!comp) {
      const hint = document.createElement("div");
      hint.style.color = MUTED;
      hint.textContent = this.selected
        ? `${this.selected} is not in the file (a composite's part: select its instance)`
        : "Click a component to edit its properties. With nothing selected, the scene's own settings and checks:";
      this.body.appendChild(hint);
      if (!this.selected) this.renderSceneSettings();
      return;
    }
    this.root = ["components", String(comp.id)];
    const form = buildForm(this.schema, defForType(this.schema, String(comp.type)));
    const title = document.createElement("div");
    title.id = "editor-selected";
    title.style.cssText = "font-weight:700;margin:4px 0 8px;font-size:14px";
    title.textContent = `${String(comp.id)} (${form.type})`;
    this.body.appendChild(title);
    if (this.ops.undescribed.has(String(comp.id))) {
      const ask = document.createElement("div");
      ask.id = "editor-describe";
      ask.style.cssText = `color:${WARN};margin:0 0 6px`;
      ask.textContent = "New: write its description before saving.";
      this.body.appendChild(ask);
    }
    for (const field of form.fields) this.body.appendChild(this.control(form, comp, field));
  }

  /** The scene's own settings (2026-11-13): `scene:` (name, description, author, seed...) and
   * `checks:` (PLAN 2.11), as forms from the schema like a component's. */
  private renderSceneSettings(): void {
    const data = (this.data ?? {}) as Record<string, unknown>;
    const sections: [string, string, Record<string, unknown>][] = [
      ["scene", "SceneMeta", (data.scene ?? {}) as Record<string, unknown>],
      ["checks", "Checks", (data.checks ?? {}) as Record<string, unknown>],
    ];
    for (const [key, def, values] of sections) {
      if (!this.schema.$defs?.[def]) continue;
      const box = document.createElement("fieldset");
      box.id = `editor-${key}`;
      box.style.cssText = "border:1px solid #374151;margin:8px 0;padding:4px 8px";
      const legend = document.createElement("legend");
      legend.textContent = key;
      legend.style.cssText = "font-weight:700";
      box.appendChild(legend);
      if (key === "scene" && this.ops.undescribed.has("scene")) {
        const ask = document.createElement("div");
        ask.style.cssText = `color:${WARN};margin:0 0 6px`;
        ask.textContent = "New: write the scene's description before saving.";
        box.appendChild(ask);
      }
      this.root = [key];
      const form = buildForm(this.schema, def);
      for (const field of form.fields) box.appendChild(this.control(form, values, field));
      this.body.appendChild(box);
    }
    this.root = [];
  }

  private row(field: Field, input: HTMLElement): HTMLDivElement {
    const row = document.createElement("label");
    row.style.cssText = "display:block;margin:6px 0";
    row.title = field.description;
    const name = document.createElement("div");
    name.style.cssText = `color:${MUTED};font-size:12px`;
    name.textContent = field.key + (field.required ? "" : " (optional)");
    const error = document.createElement("div");
    error.className = "editor-error";
    error.style.cssText = `color:${ALARM};font-size:11px`;
    row.append(name, input, error);
    // a scene setting is named with its block (`scene.name`, `checks.duration_s`)
    const block = this.root[0] === "components" ? [] : this.root;
    row.dataset.field = [...block, ...field.path].join(".");
    return row as unknown as HTMLDivElement;
  }

  private input(type: string, value: string): HTMLInputElement {
    const i = document.createElement("input");
    i.type = type;
    i.value = value;
    i.style.cssText =
      "width:100%;box-sizing:border-box;padding:3px 5px;background:#0b1220;border:1px solid #4b5563;" +
      `border-radius:2px;color:${TEXT};font:13px Arial,sans-serif`;
    return i;
  }

  /** One field's control; on change it checks the value and, if it is good, records the op. */
  private control(form: Form, comp: Record<string, unknown>, field: Field): HTMLElement {
    const current = getAt(comp, field.path);
    const root = this.root;
    const commit = (row: HTMLElement, value: unknown) => {
      const error = checkField(field, value);
      const shown = error === null ? "" : error.startsWith("must") ? error : `must be ${error}`;
      (row.querySelector(".editor-error") as HTMLElement).textContent = shown;
      if (error) return;
      this.ops.change(opFor(root, field, value));
      void this.preview();
    };
    if (field.key === "mesh" && field.path.length === 1) return this.meshControl(form, comp, field, current);
    if (field.kind === "group") {
      const box = document.createElement("fieldset");
      box.style.cssText = "border:1px solid #374151;margin:8px 0;padding:4px 8px";
      const legend = document.createElement("legend");
      legend.textContent = field.key;
      legend.style.color = MUTED;
      box.appendChild(legend);
      if ((current === undefined || current === null) && field.key !== "io") {
        const none = document.createElement("div");
        none.style.color = MUTED;
        none.textContent = "not set";
        box.appendChild(none);
      } else {
        // `io` with nothing bound yet still lists its ports, to bind one (2026-11-13)
        for (const f of field.fields) box.appendChild(this.control(form, comp, f));
      }
      return box;
    }
    if (field.kind === "const" || (field.key === "id" && field.path.length === 1)) {
      const i = this.input("text", String(current ?? (field.kind === "const" ? field.value : "")));
      i.readOnly = true;
      i.style.opacity = "0.7";
      return this.row(field, i); // the id and type are not edited here (renames come with 4.1c)
    }
    const inner = field.kind === "optional" ? field.inner : field;
    if (field.kind === "optional" && (current === undefined || current === null) && inner.kind === "group") {
      const none = document.createElement("div");
      none.style.color = MUTED;
      none.textContent = "not set";
      return this.row(field, none);
    }
    if (inner.kind === "group") return this.control(form, comp, { ...inner, path: field.path, key: field.key });
    if (inner.kind === "bool") {
      const i = this.input("checkbox", "");
      i.style.width = "auto";
      i.checked = Boolean(current ?? field.default);
      const row = this.row(field, i);
      i.addEventListener("change", () => commit(row, i.checked));
      return row;
    }
    if (inner.kind === "enum") {
      const s = document.createElement("select");
      s.style.cssText = `width:100%;background:#0b1220;color:${TEXT};border:1px solid #4b5563`;
      for (const o of inner.options) {
        const opt = document.createElement("option");
        opt.value = String(o);
        opt.textContent = String(o);
        s.appendChild(opt);
      }
      s.value = String(current ?? field.default ?? inner.options[0]);
      const row = this.row(field, s);
      s.addEventListener("change", () => {
        const pick = inner.options.find((o) => String(o) === s.value);
        commit(row, pick);
      });
      return row;
    }
    if (inner.kind === "vector") {
      const box = document.createElement("div");
      box.style.cssText = "display:flex;gap:4px";
      const values = Array.isArray(current) ? [...current] : inner.items.map(() => 0);
      const inputs = inner.items.map((_, n) => {
        const i = this.input("number", String(values[n] ?? ""));
        i.step = "any";
        box.appendChild(i);
        return i;
      });
      const row = this.row(field, box);
      for (const i of inputs) {
        i.addEventListener("change", () => commit(row, inputs.map((x) => (x.value === "" ? NaN : Number(x.value)))));
      }
      return row;
    }
    if (inner.kind === "number") {
      const i = this.input("number", current === undefined || current === null ? "" : String(current));
      i.step = "any";
      if (current === undefined && field.default !== undefined) i.placeholder = `default ${String(field.default)}`;
      const row = this.row(field, i);
      i.addEventListener("change", () => {
        if (i.value === "" && field.kind === "optional") return commit(row, null);
        commit(row, i.value === "" ? NaN : Number(i.value));
      });
      return row;
    }
    if (inner.kind === "yaml") {
      const t = document.createElement("textarea");
      t.value = current === undefined ? "" : flowYaml(current);
      t.rows = 2;
      t.style.cssText = `width:100%;box-sizing:border-box;background:#0b1220;color:${TEXT};border:1px solid #4b5563;font:12px monospace`;
      const row = this.row(field, t);
      t.addEventListener("change", () => commit(row, t.value));
      return row;
    }
    // text and tag (a tag binding written as a mapping is shown as its tag name)
    const shown = current !== null && typeof current === "object" ? (current as Record<string, unknown>).tag : current;
    const i = this.input("text", shown === undefined || shown === null ? "" : String(shown));
    const row = this.row(field, i);
    if (current !== null && typeof current === "object") {
      // a binding with options ({tag, range, initial, ...}): edited as one YAML flow mapping
      const t = document.createElement("textarea");
      // YAML flow text in the scenes' style: unquoted keys and plain tag-like strings
      t.value = flowYaml(current);
      t.rows = 2;
      t.style.cssText = `width:100%;box-sizing:border-box;background:#0b1220;color:${TEXT};border:1px solid #4b5563;font:12px monospace`;
      const mapped = this.row(field, t);
      const asYaml: Field = { ...field, kind: "yaml", reason: "a binding with options" };
      t.addEventListener("change", () => {
        const error = checkField(asYaml, t.value);
        (mapped.querySelector(".editor-error") as HTMLElement).textContent = error ? `must be ${error}` : "";
        if (error) return;
        this.ops.change(opFor(root, asYaml, t.value));
        void this.preview();
      });
      return mapped;
    }
    i.addEventListener("change", () => {
      if (i.value === "" && field.kind === "optional") return commit(row, null);
      commit(row, i.value);
    });
    // an unbound optional port (a feedback, a limit, an enable): offer the conventional tag
    // with one click instead of typing it (2026-11-13, from building inline-reject)
    const proposal = inner.kind === "tag" && !field.required && (shown === undefined || shown === null || shown === "")
      ? this.proposeTag(form, field, inner.ioKind) : null;
    if (proposal) {
      i.placeholder = proposal;
      const use = this.button(`Use ${proposal}`, () => commit(row, proposal));
      use.className = "editor-use-tag";
      use.style.cssText += ";margin-top:3px;padding:2px 8px;font-size:12px";
      row.insertBefore(use, row.querySelector(".editor-error"));
    }
    return row;
  }

  /** The tag the palette would propose for an I/O port (core/templates.py `propose_tag`):
   * `ID_KIND` for a type with one port, else `ID_PORT_KIND`, at most 40 characters. */
  private proposeTag(form: Form, field: Field, kind: string | undefined): string | null {
    if (this.root[0] !== "components" || field.path[0] !== "io" || field.path.length !== 2) return null;
    if (!kind || !["DI", "DO", "AI", "AO"].includes(kind)) return null;
    const id = String(this.root[1]);
    const io = form.fields.find((f) => f.key === "io" && (f.kind === "group" || f.kind === "optional"));
    const ports = io?.kind === "group" ? io.fields : io?.kind === "optional" && io.inner.kind === "group" ? io.inner.fields : [];
    let tag = ports.length === 1 ? `${id}_${kind}` : `${id}_${String(field.path[1]).toUpperCase()}_${kind}`;
    if (tag.length > 40) tag = `${tag.slice(0, 40 - kind.length - 1).replace(/_+$/, "")}_${kind}`;
    return tag;
  }

  /** `mesh` (PLAN 2.15): a .glb beside the scene, or none; when set, its other fields. */
  private meshControl(form: Form, comp: Record<string, unknown>, field: Field, current: unknown): HTMLElement {
    const id = String(comp.id);
    const box = document.createElement("fieldset");
    box.style.cssText = "border:1px solid #374151;margin:8px 0;padding:4px 8px";
    box.dataset.field = "mesh";
    const legend = document.createElement("legend");
    legend.textContent = "mesh (optional)";
    legend.style.color = MUTED;
    box.appendChild(legend);
    const set = current !== null && typeof current === "object" ? (current as Record<string, unknown>) : null;
    const file = set ? String(set.file ?? "") : "";
    const s = document.createElement("select");
    s.id = "editor-mesh";
    s.style.cssText = `width:100%;background:#0b1220;color:${TEXT};border:1px solid #4b5563`;
    for (const name of ["", ...new Set([...(file ? [file] : []), ...this.meshes])]) {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name || "(none: the built-in housing)";
      s.appendChild(opt);
    }
    s.value = file;
    s.addEventListener("change", () => {
      if (!s.value) this.ops.change({ op: "unset", path: ["components", id, "mesh"] });
      else if (set) this.ops.change({ op: "set", path: ["components", id, "mesh", "file"], value: s.value });
      else this.ops.change({ op: "set", path: ["components", id, "mesh"], value: { file: s.value } });
      void this.preview();
    });
    box.appendChild(s);
    if (!this.meshes.length && !file) {
      const none = document.createElement("div");
      none.style.cssText = `color:${MUTED};font-size:11px`;
      none.textContent = "No .glb files beside the scene.";
      box.appendChild(none);
    }
    const inner = field.kind === "optional" ? field.inner : field;
    if (set && inner.kind === "group") {
      for (const f of inner.fields) if (f.key !== "file") box.appendChild(this.control(form, comp, f));
    }
    return box;
  }

  private refreshButtons(): void {
    const undescribed = this.ops.undescribed.size > 0;
    this.saveButton.disabled = this.ops.size === 0 || this.errors > 0 || undescribed;
    this.revertButton.disabled = this.ops.size === 0;
    this.downloadButton.disabled = this.errors > 0 || undescribed;
    for (const b of [this.saveButton, this.revertButton, this.downloadButton]) b.style.opacity = b.disabled ? "0.5" : "1";
    this.status.ops = this.ops.size;
    this.status.errors = this.errors;
    this.status.findings = this.findings.map((f) => `${f.code} ${f.message}`);
    this.status.added = this.ops.addedIds();
    this.status.undescribed = [...this.ops.undescribed];
  }

  private renderFindings(message = ""): void {
    this.footer.replaceChildren();
    if (message) {
      const m = document.createElement("div");
      m.id = "editor-message";
      m.style.cssText = "margin-bottom:6px";
      m.textContent = message;
      this.footer.appendChild(m);
    }
    if (this.ops.undescribed.size) {
      const m = document.createElement("div");
      m.style.cssText = `color:${WARN};margin-bottom:6px`;
      m.textContent = `Write a description for ${[...this.ops.undescribed].join(", ")} before saving.`;
      this.footer.appendChild(m);
    }
    this.status.message = message;
    for (const f of this.findings) {
      const line = document.createElement("div");
      line.style.cssText = `color:${f.level === "error" ? ALARM : WARN};margin:2px 0;white-space:pre-wrap`;
      line.textContent = `${f.code} ${f.message}`;
      this.footer.appendChild(line);
    }
    this.refreshButtons();
  }

  private async preview(): Promise<void> {
    const res = await this.post("/api/edit/preview");
    if (!res.ok) {
      this.renderFindings(`Preview refused (${res.status}): ${String((await res.json()).detail)}`);
      return;
    }
    const p = (await res.json()) as Preview;
    this.status.previews += 1;
    this.findings = p.findings;
    this.errors = p.errors;
    this.data = p.data ?? this.data;
    if (p.scene) await this.host.onScene(p.scene);
    this.renderFindings(`${this.ops.size} unsaved change${this.ops.size === 1 ? "" : "s"}`);
    this.renderForm();
    if (p.scene && this.unplaced.size) await this.placeAdded();
  }

  private async save(): Promise<void> {
    if (this.ops.undescribed.size) return;
    const res = await this.post("/api/edit/save");
    const body = (await res.json()) as { base?: string; findings?: Finding[]; detail?: unknown };
    if (!res.ok) {
      const why = res.status === 409 ? "the file changed on disk; reload the page" : "the scene has lint errors";
      this.renderFindings(`Not saved (${res.status}): ${why}`);
      return;
    }
    this.ops.clear();
    this.findings = body.findings ?? [];
    this.errors = 0;
    await this.loadSource();
    await this.loadPalette();
    this.renderFindings(`Saved ${this.status.file}`);
    this.renderForm();
    this.status.saves += 1; // last: a test waiting for it sees the panel as it is after the save
  }

  private async revert(): Promise<void> {
    this.ops.clear();
    this.unplaced.clear();
    this.closeConfirm();
    this.findings = [];
    this.errors = 0;
    await this.loadSource();
    await this.preview(); // no operations: the file as it is, and its meshes
    this.renderFindings("Reverted to the file");
    if (this.selected && !this.componentData(this.selected)) this.host.onSelect(null);
    this.renderForm();
  }
}
