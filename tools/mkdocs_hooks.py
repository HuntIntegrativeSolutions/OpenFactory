"""MkDocs hooks for the OpenFactory docs site (PLAN 4.4), named in ``mkdocs.yml``.

- **Component type pages.** One page per registered component type, generated at build time under
  ``reference/components/`` and listed in the nav's "Component types" section, so a new type
  needs no nav edit. They are the site's component reference (ruled 2026-11-07):
  ``docs/components.md`` itself is not a site page, only their source. Its introduction and
  "Conventions" become the section's index page, ``reference/components/index.md``, with each
  category's introduction and links to its types; a link to ``components.md`` from any page
  lands there, or on the type's page when its anchor names a type. Each type page joins four
  sources: the type's section of ``docs/components.md`` (the hand-written behaviour, parameters
  and ports, which ``test_component_reference.py`` keeps complete), the scene schema's parameter
  fields (type, default, description), the registry's ports and fault kinds, and the type
  module's docstring. The pages are never committed.
- **Links out of the docs.** A Markdown link from a page to a repository file outside ``docs/``
  (``../README.md``, ``../../examples/pyplc/x.py``) works on GitHub but not on the site, so it is
  rewritten to that file on GitHub, on ``main``. Links inside ``docs/`` are left to MkDocs, which
  checks them (``strict: true``).

Training and demo tool; not a safety-rated simulation.
"""

from __future__ import annotations

import inspect
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any

REPO = Path(__file__).resolve().parents[1]
BLOB = "https://github.com/HuntIntegrativeSolutions/OpenFactory/blob/main/"
TREE = "https://github.com/HuntIntegrativeSolutions/OpenFactory/tree/main/"
PAGES = "reference/components"
NAV_SECTION = "Component types"
SOURCE = "components.md"  # under docs/; the generated pages' source, not a page of its own
COMMON = {"id", "type", "description", "position", "rotation_deg", "io", "mesh"}
#: A Markdown link or image target: ](target) or ](target "title").
LINK = re.compile(r"(\]\()([^)\s]+)((?:\s+\"[^\"]*\")?\))")


def _registry() -> tuple[Any, Any, Any]:
    if str(REPO / "src") not in sys.path:
        sys.path.insert(0, str(REPO / "src"))
    import openfactory.components  # noqa: F401 - registers the built-in types
    from openfactory.core.component import REGISTRY
    from openfactory.core.schema import COMPONENT_MODELS, port_specs

    return REGISTRY, COMPONENT_MODELS, port_specs


def sections(text: str) -> dict[str, tuple[str, str]]:
    """``docs/components.md`` by type: {type: (category heading, section body)}."""
    out: dict[str, tuple[str, str]] = {}
    category = ""
    parts = re.split(r"^(## .+|### `[a-z_]+`)$", text, flags=re.MULTILINE)
    for i in range(1, len(parts), 2):
        heading, body = parts[i], parts[i + 1]
        if heading.startswith("### "):
            out[heading[5:-1]] = (category, body.strip())
        else:
            category = heading[3:].strip()
    return out


def rst_to_md(text: str) -> str:
    """A module docstring's reStructuredText inline markup as Markdown."""
    text = re.sub(r":[a-z]+:`~?([^`]+)`", r"`\1`", text)  # :class:`X`, :attr:`~a.b`
    return text.replace("``", "`")


def schema_label(prop: dict[str, Any], defs: dict[str, Any]) -> tuple[str, str]:
    """(type, constraints) of one JSON Schema property, as the exported scene schema has it."""
    if "anyOf" in prop:
        options = [o for o in prop["anyOf"] if o.get("type") != "null"]
        labels = [schema_label(o, defs) for o in options]
        unset = " or unset" if len(options) < len(prop["anyOf"]) else ""
        return " or ".join(t for t, _ in labels) + unset, "; ".join(c for _, c in labels if c)
    if "$ref" in prop:
        name = prop["$ref"].rsplit("/", 1)[-1]
        target = defs.get(name, {})
        if "enum" in target:
            return " / ".join(f"`{v}`" for v in target["enum"]), ""
        return name, ""
    if "enum" in prop:
        return " / ".join(f"`{v}`" for v in prop["enum"]), ""
    if "const" in prop:
        return f"`{prop['const']}`", ""
    kind = prop.get("type", "")
    if kind == "array":
        items = prop.get("prefixItems") or [prop.get("items", {})]
        inner = sorted({schema_label(i, defs)[0] for i in items})
        size = f"{len(items)} × " if "prefixItems" in prop else ""
        kind = f"array of {size}{' or '.join(inner)}"
    bounds = []
    for key, sign in (
        ("exclusiveMinimum", ">"),
        ("minimum", "≥"),
        ("exclusiveMaximum", "<"),
        ("maximum", "≤"),
        ("minItems", "items ≥"),
        ("maxItems", "items ≤"),
    ):
        if key in prop:
            bounds.append(f"{sign} {prop[key]}")
    return kind, ", ".join(bounds)


def default_label(field: Any) -> str:
    if field.is_required():
        return "required"
    default = field.default
    return "unset" if default is None else f"`{default}`"


def cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def type_page(name: str, category: str, body: str) -> str:
    registry, models, port_specs = _registry()
    model = models[name]
    cls = registry.resolve(name)
    module = sys.modules[cls.__module__]
    lines = [
        f"# `{name}`",
        "",
        f"*{category}.* Generated from `docs/components.md`, the scene schema and the "
        f"`{cls.__module__}` module. Training and demo tool, not a safety-rated simulation.",
        "",
        # the section's own links are relative to docs/components.md, one level up from here
        LINK.sub(lambda m: m[1] + _from_reference(m[2]) + m[3], body),
        "",
        "## From the scene schema",
        "",
        "Types, defaults and limits as `web/public/scene.schema.json` enforces them. The common"
        " fields (`id`, `type`, `description`, `position`, `rotation_deg`, `io`, `mesh`) are in"
        " [the conventions](index.md#conventions).",
        "",
    ]
    schema = model.model_json_schema(by_alias=True)
    props, defs = schema.get("properties", {}), schema.get("$defs", {})
    params = [(f.alias or n, f) for n, f in model.model_fields.items() if n not in COMMON]
    if params:
        lines += ["| Parameter | Type | Default | Limits |", "|---|---|---|---|"]
        for pname, f in params:
            kind, limits = schema_label(props.get(pname, {}), defs)
            lines.append(f"| `{pname}` | {cell(kind)} | {default_label(f)} | {cell(limits)} |")
    else:
        lines.append("No parameters beyond the common fields.")
    ports = port_specs(model.model_fields["io"].annotation)
    lines += ["", "## Ports, from the registry", ""]
    if ports:
        lines += ["| Port | Kind | Required | Range | Description |", "|---|---|---|---|---|"]
        for spec in ports.values():
            span = f"{spec.range[0]:g} to {spec.range[1]:g}" if spec.range else ""
            if spec.range_from:
                span = f"from `{spec.range_from}`"
            if spec.units:
                span = f"{span} {spec.units}".strip()
            lines.append(
                f"| `{spec.name}` | {spec.kind} | {'yes' if spec.required else 'no'} | "
                f"{cell(span)} | {cell(spec.description)} |"
            )
    else:
        lines.append("None.")
    faults = ", ".join(f"`{k}`" for k in cls.FAULTS) or "none"
    lines += ["", "## Faults", "", f"{faults}.", ""]
    doc = inspect.getdoc(module)
    if doc:
        lines += ["## Behaviour, from the module docstring", "", rst_to_md(doc), ""]
    return "\n".join(lines) + "\n"


def index_page(text: str) -> str:
    """``docs/components.md`` without its type sections: the introduction and the conventions in
    full, then each category's introduction and a link to each of its types' pages."""
    parts = re.split(r"^(## .+|### `[a-z_]+`)$", text, flags=re.MULTILINE)
    out = [parts[0].rstrip(), ""]
    category_types: list[str] = []

    def close() -> None:
        if category_types:
            out.extend(f"- [`{name}`]({name}.md)" for name in category_types)
            out.append("")
            category_types.clear()

    for i in range(1, len(parts), 2):
        heading, body = parts[i], parts[i + 1]
        if heading.startswith("### "):
            category_types.append(heading[5:-1])
            continue
        close()
        # a category's introduction links relative to docs/components.md, as a type section does
        out += [
            heading,
            "",
            LINK.sub(lambda m: m[1] + _from_reference(m[2]) + m[3], body.strip()),
            "",
        ]
    close()
    out[0] = out[0].replace("# OpenFactory component reference", "# Component reference", 1)
    return "\n".join(out).rstrip() + "\n"


def _from_reference(target: str) -> str:
    """A link target written relative to docs/, seen from docs/reference/components/."""
    if re.match(r"^[a-z]+:|^#|^/", target):
        return target
    return f"../../{target}"


def _types() -> list[tuple[str, str, str]]:
    registry, _, _ = _registry()
    text = (REPO / "docs" / "components.md").read_text(encoding="utf-8")
    found = sections(text)
    missing = registry.types() - set(found)
    if missing:
        raise SystemExit(f"docs/components.md has no section for {sorted(missing)}")
    return [(name, *found[name]) for name in found if name in registry.types()]


def on_config(config: Any, **_: Any) -> Any:
    """Fill the nav's "Component types" section: its index page, then one page per type, in
    components.md order."""
    pages = [f"{PAGES}/index.md"] + [{name: f"{PAGES}/{name}.md"} for name, _, _ in _types()]
    for section in config["nav"]:
        if isinstance(section, dict):
            for entries in section.values():
                for i, entry in enumerate(entries if isinstance(entries, list) else []):
                    if isinstance(entry, dict) and NAV_SECTION in entry:
                        entries[i] = {NAV_SECTION: pages}
    return config


def on_files(files: Any, config: Any, **_: Any) -> Any:
    from mkdocs.structure.files import File

    text = (REPO / "docs" / SOURCE).read_text(encoding="utf-8")
    files.append(File.generated(config, f"{PAGES}/index.md", content=index_page(text)))
    for name, category, body in _types():
        files.append(
            File.generated(config, f"{PAGES}/{name}.md", content=type_page(name, category, body))
        )
    return files


def outside_link(target: str, page_src: str) -> str | None:
    """The GitHub URL for a relative link that leaves docs/, or None to leave it alone."""
    if re.match(r"^[a-z][a-z0-9+.-]*:|^#|^/", target):
        return None
    path, _, anchor = target.partition("#")
    if not path:
        return None
    parts: list[str] = []
    for part in (PurePosixPath("docs") / PurePosixPath(page_src).parent / path).parts:
        if part == "..":
            if parts:
                parts.pop()
        elif part != ".":
            parts.append(part)
    resolved = "/".join(parts)
    if resolved == f"docs/{SOURCE}":
        return reference_link(anchor, page_src)
    if resolved.startswith("docs/"):
        return None
    base = TREE if (REPO / resolved).is_dir() else BLOB
    return f"{base}{resolved}" + (f"#{anchor}" if anchor else "")


def reference_link(anchor: str, page_src: str) -> str:
    """Where a link to ``components.md#anchor`` goes on the site, relative to ``page_src``: the
    type's page when the anchor names a registered type, else the reference's index page."""
    registry, _, _ = _registry()
    name = anchor.replace("-", "_")
    target = f"{PAGES}/{name}.md" if name in registry.types() else f"{PAGES}/index.md"
    up = "../" * (len(PurePosixPath(page_src).parts) - 1)
    keep = "" if target.endswith(f"/{name}.md") or not anchor else f"#{anchor}"
    return f"{up}{target}{keep}"


def on_page_markdown(markdown: str, page: Any, **_: Any) -> str:
    """Rewrite links that leave docs/ to GitHub, outside fenced code blocks."""
    chunks = re.split(r"(^```.*?^```[^\n]*$)", markdown, flags=re.MULTILINE | re.DOTALL)
    for i in range(0, len(chunks), 2):  # the odd chunks are fenced code
        chunks[i] = LINK.sub(
            lambda m: m[1] + (outside_link(m[2], page.file.src_uri) or m[2]) + m[3], chunks[i]
        )
    return "".join(chunks)
