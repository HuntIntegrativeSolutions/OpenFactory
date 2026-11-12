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
  /** Draw a previewed scene document. */
  onScene(doc: SceneDocument): void;
  /** Select a component the panel chose (one just added, or none after a delete). */
  onSelect(id: string | null): void;
  /** Where a new component goes: the floor point at the centre of the view. */
  placement(): [number, number, number];
  /** What dragging the selection does. */
  setMode(mode: "move" | "turn"): void;
}

const TEXT = cssColor("normalText");
const MUTED = "#9CA3AF";
const ALARM = cssColor("alarmHigh");
const WARN = cssColor("alarmMedium");

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
  private moveButton: HTMLButtonElement;
  private turnButton: HTMLButtonElement;

  constructor(host: PanelHost) {
    this.host = host;
    this.status = {
      file: "", selected: null, ops: 0, previews: 0, saves: 0, downloads: 0, errors: 0, findings: [],
      message: "", added: [], undescribed: [], palette: [], mode: "move",
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
    this.element.append(head, buttons, add, this.tools, this.body, this.footer);
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
    await this.preview();
    this.host.onSelect(String(made.component.id));
  }

  async deleteSelected(): Promise<void> {
    const id = this.selected;
    if (!id || !this.componentData(id)) return;
    this.ops.remove(id);
    await this.preview();
    this.host.onSelect(null);
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
    (this.element.querySelector("#editor-head") as HTMLDivElement).textContent = `EDIT MODE · ${src.name}`;
  }

  select(id: string | null): void {
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
        : "Click a component to edit its properties.";
      this.body.appendChild(hint);
      return;
    }
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
    row.dataset.field = field.path.join(".");
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
    const id = String(comp.id);
    const commit = (row: HTMLElement, value: unknown) => {
      const error = checkField(field, value);
      const shown = error === null ? "" : error.startsWith("must") ? error : `must be ${error}`;
      (row.querySelector(".editor-error") as HTMLElement).textContent = shown;
      if (error) return;
      this.ops.change(opFor(id, field, value));
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
      if (current === undefined || current === null) {
        const none = document.createElement("div");
        none.style.color = MUTED;
        none.textContent = "not set";
        box.appendChild(none);
      } else {
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
      t.value = current === undefined ? "" : JSON.stringify(current);
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
      t.value = JSON.stringify(current)
        .replace(/"([a-z_]+)":/g, "$1: ")
        .replace(/"([A-Za-z][A-Za-z0-9_]*)"/g, "$1")
        .replace(/,/g, ", ");
      t.rows = 2;
      t.style.cssText = `width:100%;box-sizing:border-box;background:#0b1220;color:${TEXT};border:1px solid #4b5563;font:12px monospace`;
      const mapped = this.row(field, t);
      const asYaml: Field = { ...field, kind: "yaml", reason: "a binding with options" };
      t.addEventListener("change", () => {
        const error = checkField(asYaml, t.value);
        (mapped.querySelector(".editor-error") as HTMLElement).textContent = error ? `must be ${error}` : "";
        if (error) return;
        this.ops.change(opFor(id, asYaml, t.value));
        void this.preview();
      });
      return mapped;
    }
    i.addEventListener("change", () => {
      if (i.value === "" && field.kind === "optional") return commit(row, null);
      commit(row, i.value);
    });
    return row;
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
    if (p.scene) this.host.onScene(p.scene);
    this.renderFindings(`${this.ops.size} unsaved change${this.ops.size === 1 ? "" : "s"}`);
    this.renderForm();
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
    this.findings = [];
    this.errors = 0;
    await this.loadSource();
    await this.preview(); // no operations: the file as it is, and its meshes
    this.renderFindings("Reverted to the file");
    if (this.selected && !this.componentData(this.selected)) this.host.onSelect(null);
    this.renderForm();
  }
}
