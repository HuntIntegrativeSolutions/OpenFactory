"""The built-in component library matches the scene schema, and the example scene runs."""

from __future__ import annotations

from pathlib import Path

from openfactory.core.component import REGISTRY
from openfactory.core.iotable import Writer
from openfactory.core.loader import load
from openfactory.core.schema import COMPONENT_MODELS

EXAMPLE = Path(__file__).resolve().parents[2] / "scenes" / "sorting-by-height.yaml"


def test_registry_types_match_schema_types():
    import openfactory.components  # noqa: F401 - registers the built-in types

    assert REGISTRY.types() == set(COMPONENT_MODELS)


def test_every_component_class_has_a_behavior_spec():
    import openfactory.components  # noqa: F401

    for type_name in REGISTRY.types():
        module = __import__(REGISTRY.resolve(type_name).__module__, fromlist=["_"])
        assert module.__doc__ and "Behavior spec" in module.__doc__, type_name


def test_example_scene_loads_with_default_registry_and_steps():
    loaded = load(EXAMPLE)
    loaded.io.set_many(
        {"CONV_01_RUN_DO": True, "CONV_01_SPD_AO": 100, "EMT_01_EN_DO": True}, Writer.PLC
    )
    for _ in range(50):
        for comp in loaded.components:
            comp.step(0.02)
    assert loaded.io.get("CONV_01_RUN_DI") is True
    assert loaded.io.get("CYL_01_RET_DI") is True
    assert len(loaded.world.parts) == 1  # emitter spawned once; parts do not move until PLAN 1.5
    assert {c.type for c in loaded.components} <= REGISTRY.types()
