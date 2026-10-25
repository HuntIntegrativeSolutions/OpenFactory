"""Scene loader: scene file → I/O table + world + components (PLAN §3, task 1.3).

Behavior spec
-------------
:func:`build` takes a validated :class:`~openfactory.core.schema.Scene` and:

1. creates one :class:`~openfactory.core.iotable.Tag` per bound port, in scene order, with the kind
   from the tag suffix, the analog range and units from the port declaration, the owning component
   id, and a description of the form ``"<component description> — <port description>"``;
2. creates the :class:`~openfactory.core.world.World` with the scene seed;
3. resolves each component's ``type`` string through the registry and instantiates it.

Components are kept in scene-file order, which is also their step order (PLAN §3.2).
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path

from openfactory.core.component import REGISTRY, Component, ComponentRegistry
from openfactory.core.iotable import IOTable, Tag
from openfactory.core.schema import Scene, load_scene
from openfactory.core.world import World


@dataclass
class LoadedScene:
    scene: Scene
    io: IOTable
    world: World
    components: list[Component]

    def component(self, component_id: str) -> Component:
        for comp in self.components:
            if comp.id == component_id:
                return comp
        raise KeyError(component_id)


def build_iotable(scene: Scene) -> IOTable:
    """One tag per bound port, in scene order."""
    descriptions = {c.id: c.description for c in scene.components}
    table = IOTable()
    for binding in scene.tag_bindings():
        table.add(
            Tag.new(
                binding.name,
                binding.kind,  # type: ignore[arg-type]
                description=f"{descriptions[binding.component_id]} — {binding.description}",
                owner=binding.component_id,
                range=binding.port.range,
                units=binding.port.units,
            )
        )
    return table


def build(scene: Scene, registry: ComponentRegistry | None = None) -> LoadedScene:
    """Instantiate a validated scene. Uses the default registry unless one is given."""
    if registry is None:
        importlib.import_module("openfactory.components")  # registers the built-in types
        registry = REGISTRY
    io = build_iotable(scene)
    world = World(seed=scene.scene.seed)
    components = [registry.resolve(c.type)(c, io, world) for c in scene.components]
    return LoadedScene(scene, io, world, components)


def load(path: str | Path, registry: ComponentRegistry | None = None) -> LoadedScene:
    """Load, validate, and instantiate a scene file."""
    return build(load_scene(path), registry)
