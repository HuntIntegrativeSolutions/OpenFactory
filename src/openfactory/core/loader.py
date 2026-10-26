"""Scene loader: scene file → I/O table + world + components (PLAN §3, task 1.3).

Behavior spec
-------------
:func:`build` takes a validated :class:`~openfactory.core.schema.Scene` and:

1. creates one :class:`~openfactory.core.iotable.Tag` per bound port, in scene order, with the kind
   from the tag suffix, the analog range and units from the port declaration, the owning component
   id, a description of the form ``"<component description> — <port description>"``, and the
   binding's ``initial`` value when it has one (DO/AO only; the PLC's first write replaces it);
2. creates the :class:`~openfactory.core.world.World` with the scene seed;
3. resolves each component's ``type`` string through the registry and instantiates it.

Components are kept in scene-file order, which is also their step order (PLAN §3.2).

Scene lint: when one conveyor's discharge end (center of the end at belt-top height) is within
5 mm of another conveyor's infeed origin and no ``connections`` entry joins them, :func:`build`
logs a warning and records it in :attr:`LoadedScene.warnings`. The headless model does not hand
parts across touching belts on its own (PLAN §4), so such a scene usually forgot a connection.
The connection is never inferred: the scene author decides.
:meth:`LoadedScene.step` is one simulation tick: the kinematic model moves parts first, then each
component steps in scene order, then binding transforms (PLAN 2.17) write their conditioned DI tags
from the components' raw values.
"""

from __future__ import annotations

import importlib
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path

from openfactory.core.component import REGISTRY, Component, ComponentRegistry
from openfactory.core.geometry import to_world
from openfactory.core.iotable import IOTable, Tag
from openfactory.core.kinematics import KinematicModel
from openfactory.core.schema import CARRIER_TYPES, Scene, load_scene
from openfactory.core.transforms import TransformStage
from openfactory.core.world import World

log = logging.getLogger(__name__)

#: Discharge-to-infeed distance under which two conveyors count as touching end to end.
TOUCH_M = 0.005


@dataclass
class LoadedScene:
    scene: Scene
    io: IOTable
    world: World
    components: list[Component]
    kinematics: KinematicModel
    #: Number of completed ticks.
    tick: int = 0
    #: Simulated seconds elapsed (sum of step dt).
    time: float = 0.0
    #: Scene lint warnings found while building (also logged).
    warnings: list[str] = field(default_factory=list)
    #: Binding-level signal transforms (PLAN 2.17), applied after the components each tick.
    transforms: TransformStage | None = None

    def step(self, dt: float) -> None:
        """One tick: move parts, step components in scene order, then write transformed tags.
        Never reads the clock."""
        self.kinematics.step(dt)
        for comp in self.components:
            comp.step(dt)
        if self.transforms is not None:
            self.transforms.step(dt)
        self.tick += 1
        self.time += dt

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
                value=binding.initial if binding.initial is not None else 0,
            )
        )
    return table


def lint(scene: Scene) -> list[str]:
    """Warnings for a valid scene that is probably not what its author meant."""
    carriers = [c for c in scene.components if c.type in CARRIER_TYPES]
    joined = {(c.from_, c.to) for c in scene.connections}
    out = []
    for up in carriers:
        end = to_world(up.position, up.rotation_deg, (up.length, 0.0, 0.0))  # type: ignore[attr-defined]
        for down in carriers:
            if down.id == up.id or (up.id, down.id) in joined:
                continue
            if math.dist(end, down.position) <= TOUCH_M:
                out.append(
                    f"conveyors {up.id} and {down.id} touch end to end but no connection joins "
                    f"them; parts will fall off {up.id}'s discharge. Add "
                    f"'{{from: {up.id}, to: {down.id}}}' to connections if {up.id} feeds {down.id}"
                )
    return out


def build(scene: Scene, registry: ComponentRegistry | None = None) -> LoadedScene:
    """Instantiate a validated scene. Uses the default registry unless one is given."""
    if registry is None:
        importlib.import_module("openfactory.components")  # registers the built-in types
        registry = REGISTRY
    io = build_iotable(scene)
    world = World(seed=scene.scene.seed)
    components = [registry.resolve(c.type)(c, io, world) for c in scene.components]
    kinematics = KinematicModel(world, components, scene.connections, scene.scene.friction)
    warnings = lint(scene)
    for warning in warnings:
        log.warning("%s: %s", scene.scene.name, warning)
    transforms = TransformStage.build(io, components, scene.tag_bindings())
    transforms.prime()
    return LoadedScene(
        scene, io, world, components, kinematics, warnings=warnings, transforms=transforms
    )


def load(path: str | Path, registry: ComponentRegistry | None = None) -> LoadedScene:
    """Load, validate, and instantiate a scene file."""
    return build(load_scene(path), registry)
