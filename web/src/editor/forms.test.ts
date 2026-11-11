import { describe, expect, it } from "vitest";
import schema from "../../public/scene.schema.json";
import { buildForm, checkField, componentDefs, defForType, fieldFor, flatten, opFor, type Field } from "./forms";

/** The fields the form builder sends as YAML text, by design (PLAN 4.1b): `mesh.scale` (a number
 * or a 3-vector) on every type with a mesh, and these lists and free-key mappings. */
const EXPECTED_FALLBACKS = new Set([
  "BoxEmitter parts",
  "Tank nozzles",
  "Drain schedule",
  "Pipe path",
  "Pump curve",
  "CompositeInstance io",
]);

describe("the property form (PLAN 4.1b)", () => {
  const defs = componentDefs(schema);

  it("builds a form for every component type with no unexpected fallback", () => {
    expect(defs.length).toBeGreaterThanOrEqual(29);
    const unexpected: string[] = [];
    for (const def of defs) {
      const form = buildForm(schema, def);
      const fields = flatten(form.fields);
      expect(fields.length, def).toBeGreaterThan(3);
      for (const f of fields) {
        if (f.kind !== "yaml") continue;
        const name = `${def} ${f.path.join(".")}`;
        if (f.path.join(".") === "mesh.scale" || EXPECTED_FALLBACKS.has(name)) continue;
        unexpected.push(name);
      }
    }
    expect(unexpected).toEqual([]);
    for (const name of EXPECTED_FALLBACKS) {
      const [def, key] = name.split(" ");
      const found = flatten(buildForm(schema, def).fields).filter((f) => f.path.join(".") === key);
      expect(found.map((f) => f.kind), name).toContain("yaml");
    }
  });

  it("gives every component type an id, a type, a description and a position", () => {
    for (const def of defs) {
      const top = new Map(buildForm(schema, def).fields.map((f) => [f.key, f]));
      expect(top.get("id")?.kind, def).toBe("text");
      expect(top.get("description")?.kind, def).toBe("text");
      expect(top.get("position")?.kind, def).toBe("vector");
      if (def !== "CompositeInstance") expect(top.get("type")?.kind, def).toBe("const");
    }
  });

  it("maps a conveyor's properties to controls with the schema's limits", () => {
    const form = buildForm(schema, defForType(schema, "belt_conveyor"));
    expect(form.type).toBe("belt_conveyor");
    const byKey = new Map<string, Field>();
    for (const f of flatten(form.fields)) if (!byKey.has(f.path.join("."))) byKey.set(f.path.join("."), f); // an optional before its inner field
    const speed = byKey.get("speed_mps") as Extract<Field, { kind: "number" }>;
    expect(speed.kind).toBe("number");
    expect(checkField(speed, 0.6)).toBeNull();
    expect(checkField(speed, -1)).not.toBeNull();
    expect(byKey.get("bidirectional")?.kind).toBe("bool");
    expect(byKey.get("mesh")?.kind).toBe("optional");
    expect(byKey.get("io")?.kind).toBe("group");
    expect(byKey.get("io.run")?.kind).toBe("tag");
    expect(checkField(byKey.get("io.run")!, "CONV_09_RUN_DO")).toBeNull();
    expect(checkField(byKey.get("io.run")!, "conv run")).toMatch(/must match/);
    const position = byKey.get("position")!;
    expect(checkField(position, [1, 0.75, 0])).toBeNull();
    expect(checkField(position, [1, 0.75])).toBe("3 numbers");
    expect(checkField(byKey.get("id")!, "conv_01")).toMatch(/must match/);
  });

  it("turns an accepted value into the operation the server applies", () => {
    const form = buildForm(schema, "BeltConveyor");
    const speed = flatten(form.fields).find((f) => f.key === "speed_mps")!;
    expect(opFor("CONV_01", speed, 0.6)).toEqual({ op: "set", path: ["components", "CONV_01", "speed_mps"], value: 0.6 });
    const mesh = flatten(form.fields).find((f) => f.key === "mesh")!;
    expect(opFor("CONV_01", mesh, null)).toEqual({ op: "unset", path: ["components", "CONV_01", "mesh"] });
    const yaml = fieldFor(schema, ["parts"], "parts", { type: "array", items: { type: "object" } }, false);
    expect(opFor("EMT_01", yaml, "[{name: a}]")).toEqual({ op: "set_raw", path: ["components", "EMT_01", "parts"], literal: "[{name: a}]" });
  });

  it("names a composite type's def as CompositeInstance", () => {
    expect(defForType(schema, "photo_eye")).toBe("PhotoEye");
    expect(defForType(schema, "accumulation_conveyor")).toBe("CompositeInstance");
  });
});
