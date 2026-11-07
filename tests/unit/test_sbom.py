"""PLAN 4.8: the SBOM check (tools/sbom.py). Building the SBOM needs uv and npm and runs as its own
CI step; these tests pin what the check catches, on documents built here."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("sbom", REPO / "tools" / "sbom.py")
sbom = importlib.util.module_from_spec(spec)
sys.modules["sbom"] = sbom
spec.loader.exec_module(sbom)


def component(name: str, version: str, license_: str, ecosystem: str = "pypi") -> dict:
    return {
        "type": "library",
        "bom-ref": f"{name}@{version}",
        "name": name,
        "version": version,
        "purl": f"pkg:{ecosystem}/{name}@{version}",
        "licenses": [{"license": {"name": license_}}],
    }


def doc(*components: dict) -> dict:
    return {"bomFormat": "CycloneDX", "specVersion": "1.5", "components": list(components)}


def listed_python() -> tuple[str, str, str]:
    """A package THIRD_PARTY.md lists as a Python runtime dependency: (name, version, license)."""
    name, (version, license_) = next(iter(sbom.listed()["pypi"].items()))
    return name, version, license_


def test_a_document_of_listed_components_passes():
    name, version, license_ = listed_python()
    three = sbom.listed()["npm"]["three"]
    assert sbom.problems(doc(component(name, version, license_), component(
        "three", three[0], three[1], "npm"))) == []  # fmt: skip


def test_the_check_catches_unlisted_stale_unlicensed_and_disallowed_components():
    name, version, license_ = listed_python()
    found = sbom.problems(
        doc(
            component("left-pad", "1.3.0", "MIT", "npm"),  # not in THIRD_PARTY.md
            component(name, "0.0.1", license_),  # THIRD_PARTY.md lists another version
            {**component(name, version, license_), "licenses": []},  # no license
            component(name, version, "GPL-3.0-only"),  # outside the ADR-0001 allowlist
        )
    )
    text = "\n".join(found)
    assert "left-pad@1.3.0: not in THIRD_PARTY.md (Web viewer runtime" in text
    assert f"{name}@0.0.1: THIRD_PARTY.md lists {version}; rerun tools/third_party.py" in text
    assert f"{name}@{version}: no license" in text
    assert "'GPL-3.0-only' is not in the ADR-0001 allowlist" in text
    assert f"bom-ref '{name}@{version}' appears twice" in text


def test_the_check_needs_cyclonedx_1_5_and_package_urls():
    name, version, license_ = listed_python()
    bare = {k: v for k, v in component(name, version, license_).items() if k != "purl"}
    found = sbom.problems({"bomFormat": "SPDX", "specVersion": "2.3", "components": [bare]})
    assert "not a CycloneDX 1.5 document" in found
    assert f"{name}@{version}: needs a name, a version and a package URL" in found
