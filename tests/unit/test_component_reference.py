"""docs/components.md covers every registered component type: one section per type naming each of
its parameters and ports (PLAN 2.5 ruling 8) and the fault kinds it supports (PLAN 2.6)."""

from __future__ import annotations

import re
from pathlib import Path

import openfactory.components  # noqa: F401 - registers the built-in types
from openfactory.core.component import REGISTRY
from openfactory.core.schema import COMPONENT_MODELS, port_specs

DOC = Path(__file__).resolve().parents[2] / "docs" / "components.md"
COMMON = {"id", "type", "description", "position", "rotation_deg", "io"}


def sections() -> dict[str, str]:
    text = DOC.read_text(encoding="utf-8")
    parts = re.split(r"^### `([a-z_]+)`$", text, flags=re.MULTILINE)
    return dict(zip(parts[1::2], parts[2::2], strict=True))


def test_every_registered_type_has_a_section_and_no_other_does():
    found = sections()
    assert set(found) == REGISTRY.types(), (
        f"missing: {sorted(REGISTRY.types() - set(found))}, "
        f"unknown: {sorted(set(found) - REGISTRY.types())}"
    )


def test_each_section_names_every_parameter_and_port():
    missing = []
    for type_name, body in sections().items():
        model = COMPONENT_MODELS[type_name]
        params = [f.alias or n for n, f in model.model_fields.items() if n not in COMMON]
        ports = list(port_specs(model.model_fields["io"].annotation))
        missing += [f"{type_name}: {name}" for name in params + ports if f"`{name}`" not in body]
    assert not missing, f"docs/components.md does not name: {missing}"


def test_each_section_lists_exactly_the_faults_its_type_supports():
    wrong = []
    for type_name, body in sections().items():
        kinds = REGISTRY.resolve(type_name).FAULTS
        match = re.search(r"^Faults: (.*?)(?:\.\s*$|\(|$)", body, flags=re.MULTILINE)
        listed = re.findall(r"`([a-z_]+)`", match.group(1)) if match else None
        expected = list(kinds)
        if match and match.group(1).startswith("none"):
            listed = []
        if listed != expected:
            wrong.append(f"{type_name}: docs {listed}, code {expected}")
    assert not wrong, f"docs/components.md fault lists differ: {wrong}"
