// List and mapping controls (PLAN 4.1j): a value made of other values (the emitter's `parts`, a
// check's `parts_reached`, a tank's `nozzles`, a pipe's `path`) edited in place, item by item,
// from the field specs forms.ts builds from the schema. Each editor holds its working value and
// reports every change of it, whole, to its parent; the panel checks the whole value
// (forms.checkField) and writes it as one `set` when it is good, so a half-made item (an empty
// number) is never sent. Adding or removing an item redraws only that list or mapping.
//
// Every nested control carries `data-field`, its path from the form's root joined with dots
// (`parts.1.weight`, `checks.parts_reached.REM_01.0`), for tests and for the user's eye.
import { blank, listItems, listValue, type Field } from "./forms";

export interface CompoundHost {
  input(type: string, value: string): HTMLInputElement;
  button(text: string, onClick: () => void): HTMLButtonElement;
  /** Called when the user leaves a control after typing exactly what it already held (PLAN 4.1j
   * item 8): the panel shows that the value was taken. */
  unchanged(): void;
  muted: string;
  text: string;
}

const SELECT_CSS = "width:100%;background:#0b1220;border:1px solid #4b5563";

/** Calls `same` when the user typed in `el` and left it without changing what it held (no
 * `change` event fires then). */
export function watchUnchanged(el: HTMLInputElement | HTMLTextAreaElement, same: () => void): void {
  let typed = false;
  el.addEventListener("focus", () => (typed = false));
  el.addEventListener("input", () => (typed = true));
  el.addEventListener("change", () => (typed = false));
  el.addEventListener("blur", () => {
    if (typed) same();
    typed = false;
  });
}

function num(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) ? String(value) : "";
}

function labelled(host: CompoundHost, key: string, path: string, control: HTMLElement, optional: boolean): HTMLElement {
  const row = document.createElement("div");
  row.style.cssText = "margin:3px 0";
  row.dataset.field = path;
  const name = document.createElement("div");
  name.style.cssText = `color:${host.muted};font-size:11px`;
  name.textContent = key + (optional ? " (optional)" : "");
  row.append(name, control);
  return row;
}

/** An editor for one value of `field`; `onChange` gets the whole new value (undefined clears an
 * optional one). */
export function valueEditor(field: Field, value: unknown, path: string, onChange: (v: unknown) => void, host: CompoundHost): HTMLElement {
  switch (field.kind) {
    case "number": {
      const i = host.input("number", num(value));
      i.step = "any";
      i.dataset.field = path;
      i.addEventListener("change", () => onChange(i.value === "" ? NaN : Number(i.value)));
      watchUnchanged(i, host.unchanged);
      return i;
    }
    case "text":
    case "tag": {
      const i = host.input("text", typeof value === "string" ? value : "");
      i.dataset.field = path;
      i.addEventListener("change", () => onChange(i.value));
      watchUnchanged(i, host.unchanged);
      return i;
    }
    case "bool": {
      const i = host.input("checkbox", "");
      i.style.width = "auto";
      i.checked = Boolean(value);
      i.dataset.field = path;
      i.addEventListener("change", () => onChange(i.checked));
      return i;
    }
    case "enum":
      return enumSelect(field.options, value, path, false, onChange, host);
    case "vector": {
      const box = document.createElement("div");
      box.style.cssText = "display:flex;gap:4px";
      box.dataset.field = path;
      const values = Array.isArray(value) ? [...value] : field.items.map(() => NaN);
      const inputs = field.items.map((_, n) => {
        const i = host.input("number", num(values[n]));
        i.step = "any";
        watchUnchanged(i, host.unchanged);
        box.appendChild(i);
        return i;
      });
      for (const i of inputs) i.addEventListener("change", () => onChange(inputs.map((x) => (x.value === "" ? NaN : Number(x.value)))));
      return box;
    }
    case "optional": {
      const inner = field.inner;
      if (inner.kind === "enum") return enumSelect(inner.options, value, path, true, onChange, host);
      if (inner.kind === "number" || inner.kind === "text") {
        // an emptied box clears the key
        const clearEmpty = (v: unknown) => onChange(v === "" || (typeof v === "number" && Number.isNaN(v)) ? undefined : v);
        return valueEditor(inner, value, path, clearEmpty, host);
      }
      const box = document.createElement("div");
      box.dataset.field = path;
      const draw = (v: unknown) => {
        box.replaceChildren();
        if (v === undefined || v === null) {
          const set = host.button("Set", () => {
            const fresh = blank(inner);
            draw(fresh);
            onChange(fresh);
          });
          set.className = "editor-set";
          box.append(set);
          return;
        }
        const clear = host.button("Clear", () => {
          draw(undefined);
          onChange(undefined);
        });
        clear.className = "editor-clear";
        box.append(valueEditor(inner, v, path, onChange, host), clear);
      };
      draw(value);
      return box;
    }
    case "group": {
      const box = document.createElement("div");
      box.dataset.field = path;
      box.style.cssText = "border-left:2px solid #374151;padding-left:6px";
      const working: Record<string, unknown> = value && typeof value === "object" ? { ...(value as Record<string, unknown>) } : {};
      for (const f of field.fields) {
        const sub = `${path}.${f.key}`;
        const set = (v: unknown) => {
          // keep the schema's key order, as the file is written
          const next: Record<string, unknown> = {};
          for (const g of field.fields) {
            const cur = g.key === f.key ? v : working[g.key];
            if (cur !== undefined) next[g.key] = cur;
          }
          for (const k of Object.keys(working)) delete working[k];
          Object.assign(working, next);
          onChange({ ...working });
        };
        box.append(labelled(host, f.key, sub, valueEditor(f, working[f.key], sub, set, host), !f.required));
      }
      return box;
    }
    case "list":
      return listEditor(field, value, path, onChange, host);
    case "map":
      return mapEditor(field, value, path, onChange, host);
    default: {
      const none = document.createElement("div");
      none.textContent = "not editable here";
      return none;
    }
  }
}

function enumSelect(options: (string | number)[], value: unknown, path: string, optional: boolean, onChange: (v: unknown) => void, host: CompoundHost): HTMLSelectElement {
  const s = document.createElement("select");
  s.style.cssText = `${SELECT_CSS};color:${host.text}`;
  s.dataset.field = path;
  for (const o of optional ? ["", ...options] : options) {
    const opt = document.createElement("option");
    opt.value = String(o);
    opt.textContent = o === "" ? "(not set)" : String(o);
    s.appendChild(opt);
  }
  s.value = value === undefined || value === null ? (optional ? "" : String(options[0])) : String(value);
  s.addEventListener("change", () => onChange(s.value === "" && optional ? undefined : options.find((o) => String(o) === s.value)));
  return s;
}

function listEditor(field: Extract<Field, { kind: "list" }>, value: unknown, path: string, onChange: (v: unknown) => void, host: CompoundHost): HTMLElement {
  const box = document.createElement("div");
  box.className = "editor-list";
  box.dataset.field = path;
  const items = [...listItems(field, value)];
  const changed = () => onChange(listValue(field, [...items]));
  const draw = () => {
    box.replaceChildren();
    items.forEach((item, n) => {
      const row = document.createElement("div");
      row.className = "editor-item";
      row.style.cssText = "display:flex;gap:4px;align-items:flex-start;margin:3px 0";
      const head = document.createElement("div");
      head.style.cssText = `color:${host.muted};font-size:11px;min-width:16px;padding-top:4px`;
      head.textContent = String(n + 1);
      const editor = valueEditor(field.item, item, `${path}.${n}`, (v) => {
        items[n] = v;
        changed();
      }, host);
      editor.style.flex = "1";
      editor.style.minWidth = "0";
      const remove = host.button("×", () => {
        items.splice(n, 1);
        draw();
        changed();
      });
      remove.className = "editor-item-remove";
      remove.title = "Remove this item";
      remove.style.cssText += ";padding:2px 7px";
      row.append(head, editor, remove);
      box.append(row);
    });
    const add = host.button("Add item", () => {
      items.push(blank(field.item));
      draw();
      changed();
      (box.querySelector(`.editor-item:last-of-type input, .editor-item:last-of-type select`) as HTMLElement | null)?.focus();
    });
    add.className = "editor-list-add";
    add.style.cssText += ";padding:2px 8px;font-size:12px";
    box.append(add);
  };
  draw();
  return box;
}

function mapEditor(field: Extract<Field, { kind: "map" }>, value: unknown, path: string, onChange: (v: unknown) => void, host: CompoundHost): HTMLElement {
  const box = document.createElement("div");
  box.className = "editor-map";
  box.dataset.field = path;
  const entries: [string, unknown][] = value && typeof value === "object" && !Array.isArray(value) ? Object.entries(value as Record<string, unknown>) : [];
  const changed = () => onChange(Object.fromEntries(entries));
  const draw = () => {
    box.replaceChildren();
    entries.forEach(([key, v], n) => {
      const row = document.createElement("div");
      row.className = "editor-entry";
      row.style.cssText = "margin:4px 0;padding:3px 0;border-top:1px solid #1f2937";
      const top = document.createElement("div");
      top.style.cssText = "display:flex;gap:4px";
      const k = host.input("text", key);
      k.className = "editor-map-key";
      k.dataset.field = `${path}.${key}:key`;
      k.addEventListener("change", () => {
        entries[n] = [k.value, entries[n][1]];
        draw();
        changed();
      });
      watchUnchanged(k, host.unchanged);
      const remove = host.button("×", () => {
        entries.splice(n, 1);
        draw();
        changed();
      });
      remove.className = "editor-entry-remove";
      remove.title = `Remove ${key}`;
      remove.style.cssText += ";padding:2px 7px";
      top.append(k, remove);
      const editor = valueEditor(field.value, v, `${path}.${key}`, (nv) => {
        entries[n] = [entries[n][0], nv];
        changed();
      }, host);
      row.append(top, editor);
      box.append(row);
    });
    // a new entry: its key, then Add (the value starts empty for the user to fill)
    const add = document.createElement("div");
    add.style.cssText = "display:flex;gap:4px;margin-top:4px";
    const key = host.input("text", "");
    key.className = "editor-map-new";
    key.placeholder = "new key";
    const button = host.button("Add", () => {
      const name = key.value.trim();
      if (!name || entries.some(([k]) => k === name)) return;
      entries.push([name, blank(field.value)]);
      draw();
      changed();
    });
    button.className = "editor-map-add";
    button.style.cssText += ";padding:2px 8px;font-size:12px";
    add.append(key, button);
    box.append(add);
  };
  draw();
  return box;
}
