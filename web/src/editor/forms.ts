// Scene editor forms (PLAN 4.1b): a component's property form, built from the scene JSON Schema
// (web/public/scene.schema.json, generated from core/schema.py). Pure: buildForm turns one
// component type's schema into field specs; checkField checks one value against its field's
// schema keywords as the user types; opFor turns an accepted value into the edit operation the
// server applies (server/editor.py, core/sceneedit.py). The server's load and lint are the
// authority (ruled 2026-11-10: single-field checks here, no ajv).
//
// A list (`items`) or a mapping of pattern keys (`patternProperties`) whose items are themselves
// controls gets a list or mapping control (PLAN 4.1j): the whole value is edited in place and
// written as one `set`, so no list is typed as YAML. `anyOf [T, a list of T]` (a check's
// `parts_reached` value: one name or several) is a list that writes a single item bare.
// A construct the builder has no control for falls back to one YAML text field for that key
// (sent as `set_raw`); forms.test.ts pins the expected fallbacks, so a new construct in the
// schema cannot silently get no form.

export type JsonSchema = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
export type Path = (string | number)[];

interface Base {
  path: Path; // relative to the component
  key: string;
  title: string;
  description: string;
  required: boolean;
  default?: unknown;
}

export interface NumberLimits {
  integer: boolean;
  minimum?: number;
  maximum?: number;
  exclusiveMinimum?: number;
  exclusiveMaximum?: number;
}

export type Field =
  | (Base & { kind: "const"; value: unknown })
  | (Base & { kind: "text"; pattern?: string; minLength?: number; maxLength?: number })
  | (Base & { kind: "number" } & NumberLimits)
  | (Base & { kind: "bool" })
  | (Base & { kind: "enum"; options: (string | number)[] })
  | (Base & { kind: "vector"; items: NumberLimits[] })
  | (Base & { kind: "tag"; pattern?: string; maxLength?: number; ioKind?: string; spec: Field[] })
  | (Base & { kind: "optional"; inner: Field })
  | (Base & { kind: "group"; fields: Field[] })
  | (Base & { kind: "list"; item: Field; minItems?: number; maxItems?: number; single?: boolean })
  | (Base & { kind: "map"; keyPattern: string; keyMaxLength?: number; value: Field })
  | (Base & { kind: "either"; options: Field[] })
  | (Base & { kind: "yaml"; reason: string });

/** The kinds a list's item or a mapping's value may be: anything with a control of its own. */
const NESTABLE = new Set(["number", "text", "enum", "bool", "vector", "optional", "group", "list", "map", "either"]);

function nestable(f: Field): boolean {
  if (f.kind === "optional") return nestable(f.inner);
  if (f.kind === "group") return f.fields.every(nestable);
  if (f.kind === "list") return nestable(f.item);
  if (f.kind === "map") return nestable(f.value);
  if (f.kind === "either") return f.options.every(nestable);
  return NESTABLE.has(f.kind);
}

export interface Form {
  type: string; // the component type (the `type` constant), or "composite instance"
  def: string; // the $defs entry
  fields: Field[];
}

function deref(schema: JsonSchema, node: JsonSchema): JsonSchema {
  let n = node;
  for (let i = 0; i < 10 && n && typeof n.$ref === "string"; i++) {
    n = schema.$defs[n.$ref.split("/").pop()!];
  }
  return n;
}

function limits(n: JsonSchema): NumberLimits {
  return {
    integer: n.type === "integer",
    minimum: n.minimum,
    maximum: n.maximum,
    exclusiveMinimum: n.exclusiveMinimum,
    exclusiveMaximum: n.exclusiveMaximum,
  };
}

function base(path: Path, key: string, node: JsonSchema, required: boolean): Base {
  return {
    path,
    key,
    title: node.title ?? key,
    description: node.description ?? "",
    required,
    ...(node.default !== undefined ? { default: node.default } : {}),
  };
}

/** One property's field. */
export function fieldFor(schema: JsonSchema, path: Path, key: string, raw: JsonSchema, required: boolean): Field {
  const node = deref(schema, raw);
  const b = base(path, key, { ...node, ...raw, $ref: undefined }, required);
  const yaml = (reason: string): Field => ({ ...b, kind: "yaml", reason });
  if (node.const !== undefined) return { ...b, kind: "const", value: node.const };
  if (Array.isArray(node.anyOf)) {
    const branches = node.anyOf.map((x: JsonSchema) => deref(schema, x));
    const others = branches.filter((x: JsonSchema) => x.type !== "null");
    if (others.length === 1 && others.length < branches.length) {
      const inner = fieldFor(schema, path, key, others[0], true);
      return { ...b, kind: "optional", inner };
    }
    // an I/O binding: a tag name, or a mapping with the tag and its options
    const str = others.find((x: JsonSchema) => x.type === "string" && x.pattern);
    const obj = others.find((x: JsonSchema) => x.type === "object" && x.properties);
    if (others.length === 2 && str && obj) {
      return {
        ...b,
        kind: "tag",
        pattern: str.pattern,
        maxLength: str.maxLength,
        ioKind: raw["x-io-kind"] ?? node["x-io-kind"],
        spec: groupFields(schema, path, obj),
      };
    }
    // one value or a list of them (`parts_reached: {REM_01: good}` or `{REM_01: [a, b]}`)
    const one = others.find((x: JsonSchema) => x.type !== "array");
    const many = others.find((x: JsonSchema) => x.type === "array" && x.items);
    if (others.length === 2 && one && many && JSON.stringify(deref(schema, many.items)) === JSON.stringify(one)) {
      const item = fieldFor(schema, [], "item", one, true);
      if (nestable(item)) return { ...b, kind: "list", item, single: true };
    }
    // one of a few kinds of value, each with a control (a tank nozzle: a height, or a height
    // and a bearing, PLAN 4.1g2): the user picks which, then fills it in
    if (others.length >= 2 && others.length === branches.length) {
      const options = others.map((x: JsonSchema, i: number) => fieldFor(schema, [], `option${i + 1}`, x, true));
      const kinds = new Set(options.map((o: Field) => o.kind));
      if (kinds.size === options.length && options.every(nestable)) return { ...b, kind: "either", options };
    }
    return yaml("a union of several kinds of value");
  }
  if (Array.isArray(node.enum)) return { ...b, kind: "enum", options: node.enum };
  switch (node.type) {
    case "string":
      return { ...b, kind: "text", pattern: node.pattern, minLength: node.minLength, maxLength: node.maxLength };
    case "number":
    case "integer":
      return { ...b, kind: "number", ...limits(node) };
    case "boolean":
      return { ...b, kind: "bool" };
    case "array": {
      const items = node.prefixItems as JsonSchema[] | undefined;
      if (items && items.every((x) => ["number", "integer"].includes(deref(schema, x).type))) {
        return { ...b, kind: "vector", items: items.map((x) => limits(deref(schema, x))) };
      }
      if (!items && node.items) {
        const item = fieldFor(schema, [], "item", node.items, true);
        if (nestable(item)) return { ...b, kind: "list", item, minItems: node.minItems, maxItems: node.maxItems };
      }
      return yaml("a list");
    }
    case "object":
      if (node.properties && node.additionalProperties === false) {
        return { ...b, kind: "group", fields: groupFields(schema, path, node) };
      }
      {
        const patterns = Object.entries<JsonSchema>(node.patternProperties ?? {});
        if (patterns.length === 1 && !node.properties) {
          const value = fieldFor(schema, [], "value", patterns[0][1], true);
          if (nestable(value)) {
            return { ...b, kind: "map", keyPattern: patterns[0][0], keyMaxLength: node.propertyNames?.maxLength, value };
          }
        }
      }
      return yaml("a mapping of free keys");
    default:
      return yaml(`schema type ${String(node.type)}`);
  }
}

function groupFields(schema: JsonSchema, path: Path, node: JsonSchema): Field[] {
  const required = new Set<string>(node.required ?? []);
  return Object.entries<JsonSchema>(node.properties ?? {}).map(([k, p]) =>
    fieldFor(schema, [...path, k], k, p, required.has(k)),
  );
}

/** Every component type in the schema: `$defs` names from the components' `oneOf`. */
export function componentDefs(schema: JsonSchema): string[] {
  return schema.properties.components.items.oneOf.map((x: JsonSchema) => x.$ref.split("/").pop());
}

/** The form for one `$defs` entry. */
export function buildForm(schema: JsonSchema, def: string): Form {
  const node = schema.$defs[def];
  const type = node.properties?.type?.const ?? "composite instance";
  return { type: String(type), def, fields: groupFields(schema, [], node) };
}

/** The `$defs` entry for a component in the scene file (by its `type`). */
export function defForType(schema: JsonSchema, type: string): string {
  for (const def of componentDefs(schema)) {
    if (schema.$defs[def].properties?.type?.const === type) return def;
  }
  return "CompositeInstance"; // any other type names a composite (PLAN 2.16)
}

/** Every field, groups and optionals opened, depth first (a list's item and a mapping's value
 * are not opened: they are part of their list's or mapping's control). */
export function flatten(fields: Field[]): Field[] {
  return fields.flatMap((f) =>
    f.kind === "group" ? [f, ...flatten(f.fields)] : f.kind === "optional" ? [f, ...flatten([f.inner])] : [f],
  );
}

/** A new item's or entry's starting value: the schema default, or empty for the user to fill
 * (an empty number is NaN, which checkField refuses, so nothing half-made is ever sent). */
export function blank(field: Field): unknown {
  if (field.default !== undefined && field.default !== null) return structuredClone(field.default);
  switch (field.kind) {
    case "number":
      return NaN;
    case "text":
    case "tag":
      return "";
    case "enum":
      return field.options[0];
    case "bool":
      return false;
    case "vector":
      return field.items.map(() => NaN);
    case "optional":
      return undefined;
    case "group": {
      const out: Record<string, unknown> = {};
      for (const f of field.fields) if (f.required) out[f.key] = blank(f);
      return out;
    }
    case "list":
      return field.single ? blank(field.item) : Array.from({ length: field.minItems ?? 0 }, () => blank(field.item));
    case "map":
      return {};
    case "either":
      return blank(field.options[0]);
    default:
      return "";
  }
}

/** The items a list control shows for a value (a single list's bare item is one item). */
export function listItems(field: Extract<Field, { kind: "list" }>, value: unknown): unknown[] {
  if (Array.isArray(value)) return value;
  if (value === undefined || value === null) return [];
  return field.single ? [value] : [];
}

/** The value a list control writes for its items: a single list writes one item bare. */
export function listValue(field: Extract<Field, { kind: "list" }>, items: unknown[]): unknown {
  return field.single && items.length === 1 ? items[0] : items;
}

/** How an `either` field's option is offered: "a number", or a group's fields by name. */
export function optionLabel(option: Field): string {
  if (option.kind === "group") return option.fields.map((f) => f.key).join(" and ");
  if (option.kind === "vector") return `${option.items.length} numbers`;
  return `a ${option.kind}`;
}

/** Which of an `either` field's options a value is: the first that takes it, else the first of
 * the value's shape (a number, a list, a mapping). */
export function eitherIndex(field: Extract<Field, { kind: "either" }>, value: unknown): number {
  const ok = field.options.findIndex((o) => checkField(o, value) === null);
  if (ok >= 0) return ok;
  const shape = (o: Field) =>
    o.kind === "number" ? typeof value === "number"
      : o.kind === "text" || o.kind === "enum" ? typeof value === "string"
        : o.kind === "bool" ? typeof value === "boolean"
          : o.kind === "vector" || o.kind === "list" ? Array.isArray(value)
            : value !== null && typeof value === "object" && !Array.isArray(value);
  return Math.max(0, field.options.findIndex(shape));
}

/** A value carried over when the user picks another option: a scalar becomes the new group's
 * first field (a nozzle's height keeps its number when a bearing is added), and a group's first
 * field becomes the new scalar; otherwise the option starts empty. */
export function switchOption(from: unknown, to: Field): unknown {
  const fresh = blank(to);
  if (to.kind === "group" && to.fields.length && (typeof from === "number" || typeof from === "string")) {
    const first = to.fields[0];
    if ((first.kind === "number" && typeof from === "number") || (first.kind === "text" && typeof from === "string")) {
      return { ...(fresh as Record<string, unknown>), [first.key]: from };
    }
  }
  if ((to.kind === "number" || to.kind === "text") && from && typeof from === "object" && !Array.isArray(from)) {
    const first = Object.values(from as Record<string, unknown>)[0];
    if (to.kind === "number" ? typeof first === "number" : typeof first === "string") return first;
  }
  return fresh;
}

/** Whether two values are the same scene value (numbers by value, lists and mappings deeply,
 * key order included, as the file would be written). */
export function sameValue(a: unknown, b: unknown): boolean {
  if (typeof a === "number" && typeof b === "number") return a === b;
  if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((x, i) => sameValue(x, b[i]));
  if (a && b && typeof a === "object" && typeof b === "object" && !Array.isArray(a) && !Array.isArray(b)) {
    const ka = Object.keys(a), kb = Object.keys(b);
    return ka.length === kb.length && ka.every((k, i) => k === kb[i] && sameValue((a as Record<string, unknown>)[k], (b as Record<string, unknown>)[k]));
  }
  return a === b;
}

function numberError(v: number, l: NumberLimits): string | null {
  if (!Number.isFinite(v)) return "a number";
  if (l.integer && !Number.isInteger(v)) return "a whole number";
  if (l.minimum !== undefined && v < l.minimum) return `at least ${l.minimum}`;
  if (l.maximum !== undefined && v > l.maximum) return `at most ${l.maximum}`;
  if (l.exclusiveMinimum !== undefined && v <= l.exclusiveMinimum) return `more than ${l.exclusiveMinimum}`;
  if (l.exclusiveMaximum !== undefined && v >= l.exclusiveMaximum) return `less than ${l.exclusiveMaximum}`;
  return null;
}

/** Why a value is not acceptable for a field, or null; a value is what the control holds. */
export function checkField(field: Field, value: unknown): string | null {
  switch (field.kind) {
    case "number":
      return typeof value === "number" ? numberError(value, field) : "a number";
    case "vector": {
      if (!Array.isArray(value) || value.length !== field.items.length) return `${field.items.length} numbers`;
      for (let i = 0; i < value.length; i++) {
        const e = typeof value[i] === "number" ? numberError(value[i], field.items[i]) : "a number";
        if (e) return `item ${i + 1}: ${e}`;
      }
      return null;
    }
    case "text":
    case "tag": {
      if (typeof value !== "string") return "text";
      if (field.kind === "text" && field.minLength !== undefined && value.length < field.minLength) {
        return field.minLength === 1 ? "not empty" : `at least ${field.minLength} characters`;
      }
      if (field.maxLength !== undefined && value.length > field.maxLength) return `at most ${field.maxLength} characters`;
      if (field.pattern && !new RegExp(field.pattern).test(value)) return `must match ${field.pattern}`;
      return null;
    }
    case "enum":
      return field.options.includes(value as string | number) ? null : `one of ${field.options.join(", ")}`;
    case "bool":
      return typeof value === "boolean" ? null : "true or false";
    case "optional":
      return value === null || value === undefined ? null : checkField(field.inner, value);
    case "group": {
      if (value === null || typeof value !== "object" || Array.isArray(value)) return "a mapping";
      const v = value as Record<string, unknown>;
      for (const f of field.fields) {
        if (v[f.key] === undefined) {
          if (f.required) return `${f.key}: required`;
          continue;
        }
        const e = checkField(f, v[f.key]);
        if (e) return `${f.key}: ${e}`;
      }
      return null;
    }
    case "list": {
      const items = field.single && !Array.isArray(value) ? [value] : value;
      if (!Array.isArray(items)) return "a list";
      if (field.minItems !== undefined && items.length < field.minItems) {
        return field.minItems === 1 ? "at least one item" : `at least ${field.minItems} items`;
      }
      if (field.maxItems !== undefined && items.length > field.maxItems) return `at most ${field.maxItems} items`;
      for (let i = 0; i < items.length; i++) {
        // a name in a list is never empty (the schema's strings there have no minLength)
        if (field.item.kind === "text" && items[i] === "") return `item ${i + 1}: not empty`;
        const e = checkField(field.item, items[i]);
        if (e) return `item ${i + 1}: ${e}`;
      }
      return null;
    }
    case "either": {
      if (field.options.some((o) => checkField(o, value) === null)) return null;
      const which = eitherIndex(field, value);
      const e = checkField(field.options[which], value);
      return field.options.length > 1 ? `${optionLabel(field.options[which])}: ${e}` : e;
    }
    case "map": {
      if (value === null || typeof value !== "object" || Array.isArray(value)) return "a mapping";
      for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
        if (!new RegExp(field.keyPattern).test(k)) return `key ${k || "(empty)"}: must match ${field.keyPattern}`;
        if (field.keyMaxLength !== undefined && k.length > field.keyMaxLength) return `key ${k}: at most ${field.keyMaxLength} characters`;
        const e = checkField(field.value, v);
        if (e) return `${k}: ${e}`;
      }
      return null;
    }
    case "yaml":
      return typeof value === "string" && value.trim() ? null : "a YAML value";
    default:
      return "not editable here";
  }
}

export type EditOp =
  | { op: "set"; path: Path; value: unknown }
  | { op: "set_raw"; path: Path; literal: string }
  | { op: "unset"; path: Path }
  | { op: "add"; component: Record<string, unknown> }
  | { op: "delete"; id: string }
  | { op: "use_library"; name: string }
  | { op: "connect"; from: string; to: string }
  | { op: "unreference"; ids: string[] };

export type PathOp = Extract<EditOp, { path: Path }>;

/** The operation that writes an accepted value (null for an optional clears the key). The
 * target is a component id, or the path of the block the form edits (`["scene"]`, `["checks"]`,
 * the scene settings form). */
export function opFor(target: string | Path, field: Field, value: unknown): PathOp {
  const root: Path = typeof target === "string" ? ["components", target] : target;
  const path: Path = [...root, ...field.path];
  if (field.kind === "yaml") return { op: "set_raw", path, literal: String(value) };
  if (field.kind === "optional" && value === null) return { op: "unset", path };
  return { op: "set", path, value };
}
