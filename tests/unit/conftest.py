"""Shared helpers for unit tests."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from openfactory.core.component import Component
from openfactory.core.iotable import Writer
from openfactory.core.loader import LoadedScene, build
from openfactory.core.schema import Scene

DT = 0.02  # the default 50 Hz step (PLAN §3)


class Rig:
    """One scene under test, with helpers to drive it like a PLC and a scheduler would."""

    def __init__(self, loaded: LoadedScene) -> None:
        self.loaded = loaded
        self.io = loaded.io
        self.world = loaded.world

    def __getitem__(self, component_id: str) -> Component:
        return self.loaded.component(component_id)

    def plc(self, tag: str, value: Any) -> None:
        self.io.set(tag, value, Writer.PLC)

    def step(self, n: int = 1, dt: float = DT) -> None:
        for _ in range(n):
            for comp in self.loaded.components:
                comp.tick(dt)  # as LoadedScene.step: fault clock, then step


@pytest.fixture
def rig() -> Callable[..., Rig]:
    """Build a scene from component dicts using the real loader and default registry."""

    def make(*components: dict[str, Any], seed: int = 0) -> Rig:
        scene = Scene.model_validate(
            {
                "openfactory": "0.1",
                "scene": {"name": "unit-test", "description": "Unit test scene", "seed": seed},
                "components": list(components),
            }
        )
        return Rig(build(scene))

    return make
