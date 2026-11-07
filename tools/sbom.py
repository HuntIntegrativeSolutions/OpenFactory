"""CycloneDX SBOM of what OpenFactory ships (PLAN 4.8).

Usage (from the repository root, after ``uv sync`` and ``npm ci`` in ``web/``)::

    uv run python tools/sbom.py            # write build/sbom/openfactory-<version>.cdx.json
    uv run python tools/sbom.py --check    # write it and check it; exit 1 on a problem (CI)
    uv run python tools/sbom.py --out F    # write it to F instead

The SBOM is generated, never committed (ruled 2026-11-06): CI builds and checks it on every run.
It merges two CycloneDX 1.5 exports into one document:

- ``uv export --format cyclonedx1.5 --frozen --no-dev --all-extras``: the Python runtime closure
  from ``uv.lock``, the logix and opcua extras included, development tools left out;
- ``npm sbom --sbom-format cyclonedx --omit dev --package-lock-only`` in ``web/``: what the viewer
  bundles into ``web/dist`` (three and Rapier), build tools left out.

uv's export carries no licenses, so each Python component takes the license THIRD_PARTY.md lists
for it; npm's carries the package's declared license. ``--check`` then fails if the document is
not CycloneDX 1.5, if two components share a ``bom-ref``, if a component lacks a name, version or
package URL, if a component is missing from THIRD_PARTY.md's runtime tables or listed there at
another version (THIRD_PARTY.md is stale: rerun ``tools/third_party.py``), or if a license falls
outside the ADR-0001 allowlist (``tools/third_party.py``'s rule, LGPL for asyncua only).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
WEB = REPO / "web"
OUT_DIR = REPO / "build" / "sbom"
#: THIRD_PARTY.md sections holding what is distributed, by ecosystem.
RUNTIME_SECTIONS = {
    "pypi": "## Python runtime (core and optional extras)",
    "npm": "## Web viewer runtime (bundled into `web/dist`)",
}

_spec = importlib.util.spec_from_file_location("third_party", REPO / "tools" / "third_party.py")
third_party = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(third_party)  # type: ignore[union-attr]


def run(cmd: list[str], cwd: Path = REPO) -> str:
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True).stdout


def version() -> str:
    text = (REPO / "src" / "openfactory" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__ = "([^"]+)"', text).group(1)  # type: ignore[union-attr]


def listed() -> dict[str, dict[str, tuple[str, str]]]:
    """THIRD_PARTY.md's runtime tables: {ecosystem: {normalized name: (version, license)}}."""
    text = (REPO / "THIRD_PARTY.md").read_text(encoding="utf-8")
    out: dict[str, dict[str, tuple[str, str]]] = {}
    for ecosystem, heading in RUNTIME_SECTIONS.items():
        start = text.index(heading)
        end = text.find("\n## ", start + len(heading))
        rows: dict[str, tuple[str, str]] = {}
        for line in text[start : end if end != -1 else None].splitlines():
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) >= 3 and cells[0] not in ("Package", "") and not cells[0].startswith("-"):
                rows[third_party.norm(cells[0])] = (cells[1], cells[2])
        out[ecosystem] = rows
    return out


def ecosystem_of(component: dict[str, Any]) -> str:
    purl = component.get("purl", "")
    return "npm" if purl.startswith("pkg:npm/") else "pypi"


def license_text(component: dict[str, Any]) -> str:
    """The component's license as one expression, from CycloneDX ``licenses``."""
    terms = []
    for entry in component.get("licenses") or []:
        if "expression" in entry:
            terms.append(entry["expression"])
        else:
            lic = entry.get("license", {})
            terms.append(lic.get("id") or lic.get("name") or "")
    return " OR ".join(t for t in terms if t)


def build() -> dict[str, Any]:
    """The merged CycloneDX 1.5 document."""
    python = json.loads(
        run(["uv", "export", "--format", "cyclonedx1.5", "--frozen", "--no-dev", "--all-extras",
             "--quiet"])
    )  # fmt: skip
    npm = shutil.which("npm") or "npm"
    web = json.loads(
        run([npm, "sbom", "--sbom-format", "cyclonedx", "--omit", "dev", "--package-lock-only"],
            cwd=WEB)
    )  # fmt: skip
    table = listed()
    components = []
    for component in python.get("components", []):
        if not component.get("licenses"):
            row = table["pypi"].get(third_party.norm(component.get("name", "")))
            if row:
                component["licenses"] = [{"license": {"name": row[1]}}]
        components.append(component)
    components += web.get("components", [])
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "bom-ref": f"openfactory@{version()}",
                "name": "openfactory",
                "version": version(),
                "licenses": [{"license": {"id": "Apache-2.0"}}],
                "purl": f"pkg:pypi/openfactory@{version()}",
            },
            "tools": {
                "components": [
                    {"type": "application", "name": "uv export --format cyclonedx1.5"},
                    {"type": "application", "name": "npm sbom --sbom-format cyclonedx"},
                    {"type": "application", "name": "tools/sbom.py"},
                ]
            },
        },
        "components": components,
        "dependencies": python.get("dependencies", []) + web.get("dependencies", []),
    }


def problems(doc: dict[str, Any]) -> list[str]:
    """Everything wrong with the document, as readable lines (empty if it passes)."""
    found = []
    if doc.get("bomFormat") != "CycloneDX" or doc.get("specVersion") != "1.5":
        found.append("not a CycloneDX 1.5 document")
    table = listed()
    refs: set[str] = set()
    for c in doc.get("components", []):
        name = c.get("name", "")
        label = f"{name}@{c.get('version', '?')}"
        if not name or not c.get("version") or not c.get("purl"):
            found.append(f"{label}: needs a name, a version and a package URL")
        if c.get("bom-ref") in refs:
            found.append(f"{label}: bom-ref {c['bom-ref']!r} appears twice")
        refs.add(c.get("bom-ref", ""))
        ecosystem = ecosystem_of(c)
        row = table[ecosystem].get(third_party.norm(name))
        if row is None:
            found.append(f"{label}: not in THIRD_PARTY.md ({RUNTIME_SECTIONS[ecosystem][3:]})")
        elif row[0] != c.get("version"):
            found.append(f"{label}: THIRD_PARTY.md lists {row[0]}; rerun tools/third_party.py")
        license_ = license_text(c)
        if not license_:
            found.append(f"{label}: no license")
        elif reason := third_party.license_problem(name, license_):
            found.append(reason)
    if not doc.get("components"):
        found.append("no components")
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CycloneDX SBOM of OpenFactory (PLAN 4.8).")
    parser.add_argument("--check", action="store_true", help="also check it; exit 1 on a problem")
    parser.add_argument("--out", type=Path, default=None, help="write here instead")
    args = parser.parse_args(argv)
    doc = build()
    out = args.out or OUT_DIR / f"openfactory-{version()}.cdx.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    counts = {e: sum(ecosystem_of(c) == e for c in doc["components"]) for e in RUNTIME_SECTIONS}
    shown = out.relative_to(REPO) if out.is_relative_to(REPO) else out
    print(f"wrote {shown}: {counts['pypi']} Python and {counts['npm']} npm components")
    if not args.check:
        return 0
    found = problems(doc)
    for line in found:
        print(f"SBOM problem: {line}", file=sys.stderr)
    if found:
        return 1
    print("SBOM checked: every component listed in THIRD_PARTY.md, licenses within ADR-0001")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
