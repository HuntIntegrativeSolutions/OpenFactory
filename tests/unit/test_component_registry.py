"""PLAN 1.3: component base class, registry decorator, and loader type resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from openfactory.core.component import Component, ComponentRegistry
from openfactory.core.iotable import TagWriteError, Writer
from openfactory.core.loader import build
from openfactory.core.schema import load_scene

EXAMPLE = Path(__file__).resolve().parents[2] / "scenes" / "sorting-by-height.yaml"
EXAMPLE_TYPES = {"belt_conveyor", "photo_eye", "pusher", "box_emitter", "part_remover"}


@pytest.fixture
def registry() -> ComponentRegistry:
    """A fresh registry so tests never touch the global one."""
    return ComponentRegistry()


def _stub_class() -> type[Component]:
    class Stub(Component):
        def step(self, dt: float) -> None:
            self.last_dt = dt

    return Stub


@pytest.fixture
def stub_registry(registry) -> ComponentRegistry:
    for type_name in EXAMPLE_TYPES:
        registry.register(type_name)(_stub_class())
    return registry


# --- registry ----------------------------------------------------------------------------------


def test_register_sets_type_name_and_resolves(registry):
    cls = registry.register("belt_conveyor")(_stub_class())
    assert cls.type_name == "belt_conveyor"
    assert registry.resolve("belt_conveyor") is cls
    assert "belt_conveyor" in registry
    assert registry.types() == {"belt_conveyor"}


def test_duplicate_registration_raises(registry):
    registry.register("photo_eye")(_stub_class())
    with pytest.raises(ValueError, match="already registered"):
        registry.register("photo_eye")(_stub_class())


def test_register_rejects_non_component(registry):
    with pytest.raises(TypeError):
        registry.register("photo_eye")(object)  # type: ignore[arg-type]


def test_unknown_type_lists_registered_types(registry):
    registry.register("pusher")(_stub_class())
    with pytest.raises(KeyError, match="unknown component type 'tank'.*pusher"):
        registry.resolve("tank")


def test_registries_are_independent(registry):
    registry.register("pusher")(_stub_class())
    assert "pusher" not in ComponentRegistry()


# --- loader ------------------------------------------------------------------------------------


def test_loader_resolves_type_strings_to_classes(stub_registry):
    loaded = build(load_scene(EXAMPLE), stub_registry)
    assert [c.id for c in loaded.components] == ["CONV_01", "PE_01", "CYL_01", "EMT_01", "REM_01"]
    for comp in loaded.components:
        assert type(comp) is stub_registry.resolve(comp.config.type)
        assert comp.type == comp.config.type


def test_loader_reports_unregistered_type(registry):
    registry.register("belt_conveyor")(_stub_class())
    with pytest.raises(KeyError, match="unknown component type 'photo_eye'"):
        build(load_scene(EXAMPLE), registry)


def test_loader_builds_iotable_from_bindings(stub_registry):
    loaded = build(load_scene(EXAMPLE), stub_registry)
    io = loaded.io
    assert [t.name for t in io.tags()][:3] == ["CONV_01_RUN_DO", "CONV_01_SPD_AO", "CONV_01_RUN_DI"]
    speed = io.tag("CONV_01_SPD_AO")
    assert (speed.kind, speed.dtype, speed.range, speed.units) == ("AO", "float32", (0, 100), "%")
    assert speed.owner == "CONV_01"
    assert speed.description == "Infeed Belt Conveyor — Sorting Line — Speed command"
    assert io.tag("PE_01_DI").dtype == "bool"
    assert loaded.world.seed == 42


# --- component base: ports, read/write ---------------------------------------------------------


def test_component_ports_inputs_outputs(stub_registry):
    loaded = build(load_scene(EXAMPLE), stub_registry)
    conv = loaded.component("CONV_01")
    assert conv.bindings == {
        "run": "CONV_01_RUN_DO",
        "speed": "CONV_01_SPD_AO",
        "running": "CONV_01_RUN_DI",
    }
    assert set(conv.inputs) == {"run", "speed"}
    assert set(conv.outputs) == {"running"}
    loaded.io.set("CONV_01_RUN_DO", True, Writer.PLC)
    assert conv.inputs["run"].value is True


def test_read_write_and_unbound_ports(stub_registry):
    loaded = build(load_scene(EXAMPLE), stub_registry)
    conv, cyl = loaded.component("CONV_01"), loaded.component("CYL_01")
    assert conv.read("run", default=False) is False
    loaded.io.set("CONV_01_RUN_DO", True, Writer.PLC)
    assert conv.read("run", default=False) is True
    conv.write("running", True)
    assert loaded.io.get("CONV_01_RUN_DI") is True
    # REM_01 has no ports; EMT_01 binds enable; an unbound optional port reads its default
    emitter = loaded.component("EMT_01")
    assert emitter.read("enable", default=True) is False  # bound, PLC has not set it
    assert cyl.read("extend", default=False) is False


def test_read_write_direction_is_enforced(stub_registry):
    conv = build(load_scene(EXAMPLE), stub_registry).component("CONV_01")
    with pytest.raises(ValueError, match="cannot write DO"):
        conv.write("run", True)
    with pytest.raises(ValueError, match="cannot read DI"):
        conv.read("running", default=False)
    with pytest.raises(KeyError, match="no port"):
        conv.read("reverse", default=False)


def test_plc_side_still_cannot_write_outputs(stub_registry):
    loaded = build(load_scene(EXAMPLE), stub_registry)
    with pytest.raises(TagWriteError):
        loaded.io.set("CONV_01_RUN_DI", True, Writer.PLC)


def test_geometry_default(stub_registry):
    pe = build(load_scene(EXAMPLE), stub_registry).component("PE_01")
    assert pe.geometry() == {
        "id": "PE_01",
        "type": "photo_eye",
        "position": [1.5, 0.15, 0.0],
        "rotation_deg": 0.0,
    }
    assert pe.state() == {}
