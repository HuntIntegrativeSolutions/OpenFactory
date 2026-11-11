// Scene editor panel (PLAN 4.1b): with `openfactory edit SCENE` the viewer shows this panel on the
// right. Click a component to select it; its properties appear in a form built from the JSON
// Schema (forms.ts); each accepted change becomes an edit operation, the server previews the whole
// list against the file it was based on (server/editor.py) and returns the lint's findings and
// the scene document, which the viewer redraws. Save writes the file; the server refuses a stale
// base (409) or a scene with lint errors (422). The browser never writes YAML.
import { cssColor } from "../isa101";
import type { SceneDocument } from "../api";
import {
  buildForm,
  checkField,
  defForType,
  opFor,
  type EditOp,
  type Field,
  type Form,
  type JsonSchema,
} from "./forms";

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
  errors: number;
  findings: string[];
  message: string;
}

const TEXT = cssColor("normalText");
const MUTED = "#9CA3AF";
const ALARM = cssColor("alarmHigh");
const WARN = cssColor("alarmMedium");

function key(op: EditOp): string {
  return op.path.join("\u0000");
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
  private ops = new Map<string, EditOp>();
  private selected: string | null = null;
  private findings: Finding[] = [];
  private errors = 0;
  private readonly onScene: (doc: SceneDocument) => void;
  private body: HTMLDivElement;
  private footer: HTMLDivElement;
  private saveButton: HTMLButtonElement;
  private revertButton: HTMLButtonElement;

  constructor(onScene: (doc: SceneDocument) => void) {
    this.onScene = onScene;
    this.status = { file: "", selected: null, ops: 0, previews: 0, saves: 0, errors: 0, findings: [], message: "" };
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
    buttons.append(this.saveButton, this.revertButton);
    this.body = document.createElement("div");
    this.body.id = "editor-form";
    this.footer = document.createElement("div");
    this.footer.id = "editor-findings";
    this.footer.style.cssText = "margin-top:12px;font-size:12px";
    this.element.append(head, buttons, this.body, this.footer);
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

  /** Read the schema and the file; show the hint until something is selected. */
  async start(): Promise<void> {
    this.schema = (await (await fetch("/scene.schema.json")).json()) as JsonSchema;
    await this.loadSource();
    this.renderForm();
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
    const list = this.data?.components ?? [];
    return (list.find((c) => c.id === this.selected) as Record<string, unknown> | undefined) ?? null;
  }

  private renderForm(): void {
    this.body.replaceChildren();
    const comp = this.component();
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
      const op = opFor(id, field, value);
      this.ops.set(key(op), op);
      void this.preview();
    };
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
      i.readOnly = true; // a binding with options: edited as YAML in 4.1c
      i.style.opacity = "0.7";
      return row;
    }
    i.addEventListener("change", () => {
      if (i.value === "" && field.kind === "optional") return commit(row, null);
      commit(row, i.value);
    });
    return row;
  }

  private refreshButtons(): void {
    this.saveButton.disabled = this.ops.size === 0 || this.errors > 0;
    this.revertButton.disabled = this.ops.size === 0;
    this.saveButton.style.opacity = this.saveButton.disabled ? "0.5" : "1";
    this.revertButton.style.opacity = this.revertButton.disabled ? "0.5" : "1";
    this.status.ops = this.ops.size;
    this.status.errors = this.errors;
    this.status.findings = this.findings.map((f) => `${f.code} ${f.message}`);
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
    const res = await fetch("/api/edit/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ base: this.base, ops: [...this.ops.values()] }),
    });
    if (!res.ok) {
      this.renderFindings(`Preview refused (${res.status}): ${String((await res.json()).detail)}`);
      return;
    }
    const p = (await res.json()) as Preview;
    this.status.previews += 1;
    this.findings = p.findings;
    this.errors = p.errors;
    this.data = p.data ?? this.data;
    if (p.scene) this.onScene(p.scene);
    this.renderFindings(`${this.ops.size} unsaved change${this.ops.size === 1 ? "" : "s"}`);
    this.renderForm();
  }

  private async save(): Promise<void> {
    const res = await fetch("/api/edit/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ base: this.base, ops: [...this.ops.values()] }),
    });
    const body = (await res.json()) as { base?: string; findings?: Finding[]; detail?: unknown };
    if (!res.ok) {
      const why = res.status === 409 ? "the file changed on disk; reload the page" : "the scene has lint errors";
      this.renderFindings(`Not saved (${res.status}): ${why}`);
      return;
    }
    this.ops.clear();
    this.status.saves += 1;
    this.findings = body.findings ?? [];
    this.errors = 0;
    await this.loadSource();
    this.renderFindings(`Saved ${this.status.file}`);
    this.renderForm();
  }

  private async revert(): Promise<void> {
    this.ops.clear();
    this.findings = [];
    this.errors = 0;
    await this.loadSource();
    const res = await fetch("/api/scene");
    if (res.ok) this.onScene((await res.json()) as SceneDocument);
    this.renderFindings("Reverted to the file");
    this.renderForm();
  }
}
