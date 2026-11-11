// Scene editor forms (PLAN 4.1b): a component's property form, built from the scene JSON Schema
// (web/public/scene.schema.json, generated from core/schema.py). Pure: buildForm turns one
// component type's schema into field specs; checkField checks one value against its field's
// schema keywords as the user types; opFor turns an accepted value into the edit operation the
// server applies (server/editor.py, core/sceneedit.py). The server's load and lint are the
// authority (ruled 2026-11-10: single-field checks here, no ajv).
//
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
  | (Base & { kind: "yaml"; reason: string });

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
      return yaml("a list");
    }
    case "object":
      if (node.properties && node.additionalProperties === false) {
        return { ...b, kind: "group", fields: groupFields(schema, path, node) };
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

/** Every field, groups and optionals opened, depth first. */
export function flatten(fields: Field[]): Field[] {
  return fields.flatMap((f) =>
    f.kind === "group" ? [f, ...flatten(f.fields)] : f.kind === "optional" ? [f, ...flatten([f.inner])] : [f],
  );
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
      return value === null ? null : checkField(field.inner, value);
    case "yaml":
      return typeof value === "string" && value.trim() ? null : "a YAML value";
    default:
      return "not editable here";
  }
}

export type EditOp =
  | { op: "set"; path: Path; value: unknown }
  | { op: "set_raw"; path: Path; literal: string }
  | { op: "unset"; path: Path };

/** The operation that writes an accepted value (null for an optional clears the key). */
export function opFor(componentId: string, field: Field, value: unknown): EditOp {
  const path: Path = ["components", componentId, ...field.path];
  if (field.kind === "yaml") return { op: "set_raw", path, literal: String(value) };
  if (field.kind === "optional" && value === null) return { op: "unset", path };
  return { op: "set", path, value };
}
