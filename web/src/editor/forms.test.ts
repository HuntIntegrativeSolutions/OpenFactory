import { describe, expect, it } from "vitest";
import schema from "../../public/scene.schema.json";
import { blank, buildForm, checkField, componentDefs, defForType, eitherIndex, fieldFor, flatten, listValue, opFor, optionLabel, sameValue, switchOption, type Field } from "./forms";

/** The fields the form builder sends as YAML text, by design (PLAN 4.1b): `mesh.scale` (a number
 * or a 3-vector) on every type with a mesh, and a composite instance's `io`, a mapping of free
 * keys whose values the schema does not describe. Lists and pattern-keyed mappings have their
 * own controls since PLAN 4.1j (the emitter's parts, a tank's nozzles, a drain's schedule, a
 * pipe's path, a pump's curve). */
const EXPECTED_FALLBACKS = new Set(["CompositeInstance io"]);

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
    // the scene settings form edits a top-level block (2026-11-13)
    expect(opFor(["checks"], speed, 0.6)).toEqual({ op: "set", path: ["checks", "speed_mps"], value: 0.6 });
    const yaml = fieldFor(schema, ["parts"], "parts", { type: "array", items: { type: "object" } }, false);
    expect(opFor("EMT_01", yaml, "[{name: a}]")).toEqual({ op: "set_raw", path: ["components", "EMT_01", "parts"], literal: "[{name: a}]" });
  });

  it("names a composite type's def as CompositeInstance", () => {
    expect(defForType(schema, "photo_eye")).toBe("PhotoEye");
    expect(defForType(schema, "accumulation_conveyor")).toBe("CompositeInstance");
  });
});

describe("list and mapping controls (PLAN 4.1j)", () => {
  const field = (def: string, key: string) => flatten(buildForm(schema, def).fields).find((f) => f.path.join(".") === key)!;
  const inner = (f: Field) => (f.kind === "optional" ? f.inner : f);

  it("gives the lists and mappings that were typed as YAML a control built from the schema", () => {
    const parts = field("BoxEmitter", "parts");
    expect(parts.kind).toBe("list");
    if (parts.kind !== "list") return;
    expect(parts.minItems).toBe(1);
    expect(parts.item.kind).toBe("group");
    if (parts.item.kind === "group") expect(parts.item.fields.map((f) => `${f.key}:${f.kind}`)).toEqual(["name:text", "size:vector", "weight:number", "color:optional"]);
    const nozzles = field("Tank", "nozzles");
    expect(nozzles.kind === "map" && nozzles.value.kind).toBe("either"); // 4.1g2: a height, or a group
    expect(inner(field("Drain", "schedule")).kind).toBe("list");
    expect(inner(field("Pump", "curve")).kind).toBe("list");
    const path = field("Pipe", "path");
    expect(path.kind === "list" && path.item.kind).toBe("vector");
  });

  it("builds the scene's checks with no YAML field at all", () => {
    const checks = flatten(buildForm(schema, "Checks").fields);
    expect(checks.filter((f) => f.kind === "yaml").map((f) => f.key)).toEqual([]);
    const reached = checks.find((f) => f.key === "parts_reached")!;
    expect(reached.kind).toBe("map");
    if (reached.kind !== "map") return;
    expect(reached.keyPattern).toBe("^[A-Z][A-Z0-9_]*$");
    expect(reached.value.kind === "list" && reached.value.single).toBe(true);
    // one name is written bare, several as a list
    expect(checkField(reached, { REM_01: "oversize", REM_02: ["good", "tall"] })).toBeNull();
    expect(checkField(reached, { rem_01: "good" })).toMatch(/key rem_01: must match/);
    expect(checkField(reached, { REM_01: "" })).toBe("REM_01: item 1: not empty"); // a new entry, not filled in yet
    if (reached.value.kind === "list") {
      expect(listValue(reached.value, ["good"])).toBe("good");
      expect(listValue(reached.value, ["good", "tall"])).toEqual(["good", "tall"]);
    }
  });

  it("checks a list item by item and never passes a half-made one", () => {
    const parts = field("BoxEmitter", "parts");
    const good = { name: "good", size: [0.3, 0.2, 0.3], weight: 0.7 };
    expect(checkField(parts, [good])).toBeNull();
    expect(checkField(parts, [])).toBe("at least one item");
    expect(parts.kind === "list" && blank(parts.item)).toEqual({ name: "", size: [NaN, NaN, NaN], weight: NaN });
    expect(checkField(parts, [good, parts.kind === "list" ? blank(parts.item) : null])).toMatch(/^item 2: name: must match/);
    expect(checkField(parts, [{ ...good, weight: NaN }])).toBe("item 1: weight: a number");
    expect(checkField(parts, [{ ...good, color: "red" }])).toBeNull();
    expect(checkField(parts, [{ name: "good", size: [0.3, 0.2, 0.3] }])).toBe("item 1: weight: required");
    expect(opFor("EMT_01", parts, [good])).toEqual({ op: "set", path: ["components", "EMT_01", "parts"], value: [good] });
  });

  it("knows a value it already has, key order included", () => {
    expect(sameValue(3, 3.0)).toBe(true);
    expect(sameValue([0.3, 0.2], [0.3, 0.2])).toBe(true);
    expect(sameValue({ a: 1, b: 2 }, { a: 1, b: 2 })).toBe(true);
    expect(sameValue({ a: 1, b: 2 }, { b: 2, a: 1 })).toBe(false); // written in another order
    expect(sameValue("3", 3)).toBe(false);
  });
});

describe("a value of one of several kinds (PLAN 4.1g2)", () => {
  const nozzles = flatten(buildForm(schema, "Tank").fields).find((f) => f.key === "nozzles")!;
  const either = nozzles.kind === "map" ? nozzles.value : null;

  it("makes a tank's nozzle a height or a height and a bearing, never a line of YAML", () => {
    expect(either?.kind).toBe("either");
    if (either?.kind !== "either") return;
    expect(either.options.map(optionLabel)).toEqual(["a number", "height and bearing_deg"]);
    expect(checkField(nozzles, { side: 1.2, bottom: { height: 0, bearing_deg: 90 } })).toBeNull();
    expect(checkField(nozzles, { side: { height: 1.2 } })).toBe("side: height and bearing_deg: bearing_deg: required");
    expect(checkField(nozzles, { side: "high" })).not.toBeNull(); // neither kind (the server checks 0 to height)
    expect(eitherIndex(either, 1.2)).toBe(0);
    expect(eitherIndex(either, { height: 1.2 })).toBe(1);
  });

  it("keeps the height when a bearing is added, and when it is taken away", () => {
    if (either?.kind !== "either") return;
    expect(switchOption(1.2, either.options[1])).toEqual({ height: 1.2, bearing_deg: NaN });
    expect(switchOption({ height: 1.2, bearing_deg: 90 }, either.options[0])).toBe(1.2);
  });

  it("also gives a mesh's scale (one number or three) its own control", () => {
    const scale = flatten(buildForm(schema, "BeltConveyor").fields).find((f) => f.path.join(".") === "mesh.scale")!;
    const inner = scale.kind === "optional" ? scale.inner : scale;
    expect(inner.kind).toBe("either");
  });
});
