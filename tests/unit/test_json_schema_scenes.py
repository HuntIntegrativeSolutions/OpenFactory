"""Every scene in the repository validates against the exported JSON Schema (ruled 2026-11-01).

``web/public/scene.schema.json`` is what an editor uses to check a scene as it is typed, so it
must accept every scene the loader accepts. ``test_committed_json_schema_is_current`` only proves
the file matches the pydantic models; it cannot see a schema that pydantic exports but that
rejects real scenes, which is what happened when composite instances first joined the component
``oneOf`` (a built-in component matched two branches and every scene failed). This test runs a
real JSON Schema validator (Draft 2020-12, the dialect pydantic generates) over the shipped
scenes, the test scenes, and the kit copies, and checks that it still rejects what the loader
rejects.

Scenes are read with the loader's YAML rules (only ``true``/``false`` are booleans, as in YAML 1.2,
which is what editors use), so a port named ``on`` stays the string ``"on"``.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

from openfactory.core.schema import JSON_SCHEMA_PATH, _SceneYamlLoader, parse_scene

REPO = Path(__file__).resolve().parents[2]


def _scene_files() -> list[Path]:
    found = [
        *REPO.glob("scenes/*.yaml"),
        *REPO.glob("scenes/*/*.yaml"),
        *REPO.glob("tests/scenes/*.yaml"),
        *REPO.glob("kits/*/*.yaml"),
    ]
    return sorted(p for p in found if not p.name.endswith(".modbus.yaml"))


SCENES = _scene_files()


@pytest.fixture(scope="module")
def validator() -> Draft202012Validator:
    schema = json.loads(JSON_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _read(path: Path) -> dict:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=_SceneYamlLoader)  # noqa: S506


def _errors(validator: Draft202012Validator, data: dict) -> list[str]:
    return [
        f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message[:200]}"
        for e in validator.iter_errors(data)
    ]


def test_every_scene_kind_is_covered():
    names = {p.relative_to(REPO).as_posix() for p in SCENES}
    assert len(SCENES) >= 20, names
    assert any(n.startswith("kits/") for n in names)
    assert "tests/scenes/gated-conveyor.yaml" in names  # composite instances
    assert "tests/scenes/sorter-custom-housing.yaml" in names  # a custom mesh


@pytest.mark.parametrize("path", SCENES, ids=lambda p: p.relative_to(REPO).as_posix())
def test_scene_is_valid_against_the_exported_json_schema(validator, path):
    data = _read(path)
    parse_scene(path.read_text(encoding="utf-8"))  # the loader accepts it too
    assert _errors(validator, data) == []


@pytest.fixture
def sorter() -> dict:
    return _read(REPO / "scenes" / "sorting-by-height.yaml")


def _first(data: dict, type_name: str) -> dict:
    return next(c for c in data["components"] if c["type"] == type_name)


def _broken(sorter: dict, how: str) -> dict:
    data = copy.deepcopy(sorter)
    if how == "unknown key on a built-in":
        _first(data, "belt_conveyor")["speed_mph"] = 1.0
    elif how == "missing position":
        del _first(data, "photo_eye")["position"]
    elif how == "bad tag name":
        conveyor = _first(data, "belt_conveyor")
        port = next(iter(conveyor["io"]))
        conveyor["io"][port] = "conv 01 run"
    elif how == "mesh path not a string":
        _first(data, "pusher")["mesh"] = {"file": 3}
    elif how == "composite without parts":
        data["composites"] = [{"type": "empty_thing", "io": {}}]
    elif how == "top-level unknown key":
        data["extras"] = True
    return data


@pytest.mark.parametrize(
    "how",
    [
        "unknown key on a built-in",
        "missing position",
        "bad tag name",
        "mesh path not a string",
        "composite without parts",
        "top-level unknown key",
    ],
)
def test_schema_rejects_what_the_loader_rejects(validator, sorter, how):
    data = _broken(sorter, how)
    assert _errors(validator, data), f"JSON Schema accepted a scene with {how}"
    with pytest.raises(ValueError):
        parse_scene(yaml.safe_dump(data, sort_keys=False))
