"""Scene loader: scene file → I/O table + world + components (PLAN §3, task 1.3).

Behavior spec
-------------
:func:`build` takes a validated :class:`~openfactory.core.schema.Scene` and:

1. creates one :class:`~openfactory.core.iotable.Tag` per bound port, in scene order, with the kind
   from the tag suffix, the analog range and units from the port declaration, the owning component
   id, a description of the form ``"<component description> — <port description>"``, and the
   binding's ``initial`` value when it has one (DO/AO only; the PLC's first write replaces it),
   then the simulator's own ``SIM_HEARTBEAT_DI`` (:mod:`openfactory.core.heartbeat`);
2. creates the :class:`~openfactory.core.world.World` with the scene seed;
3. resolves each component's ``type`` string through the registry and instantiates it.

Components are kept in scene-file order, which is also their step order (PLAN §3.2).

Scene lint: when one conveyor's discharge end (center of the end at belt-top height) is within
5 mm of another conveyor's infeed origin and no ``connections`` entry joins them, :func:`build`
logs a warning and records it in :attr:`LoadedScene.warnings`. The headless model does not hand
parts across touching belts on its own (PLAN §4), so such a scene usually forgot a connection.
The connection is never inferred: the scene author decides.

Custom meshes (PLAN 2.15): a component's ``mesh.file`` is resolved against the scene file's folder
(``base_dir``; :func:`load` passes it). A file that is missing, outside that folder once symlinks
are followed, or not binary glTF 2.0 (the 12-byte GLB header: magic ``glTF``, version 2, and a
length equal to the file size) gives one warning and is left out of :attr:`LoadedScene.meshes`,
so the viewer draws the primitive. Meshes are visual only and never change the simulation.
:meth:`LoadedScene.step` is one simulation tick: the kinematic model moves parts first, then each
component steps in scene order, then binding transforms (PLAN 2.17) write their conditioned DI tags
from the components' raw values, then exam switches (PLAN 2.11), then the heartbeat.
"""

from __future__ import annotations

import importlib
import logging
import math
import struct
from dataclasses import dataclass, field
from pathlib import Path

import openfactory.components  # noqa: F401 - registers the built-in types and their defaults
from openfactory.core.component import REGISTRY, Component, ComponentRegistry
from openfactory.core.geometry import to_world
from openfactory.core.heartbeat import HEARTBEAT_TAG, heartbeat_tag, heartbeat_value
from openfactory.core.iotable import IOTable, Tag, Writer
from openfactory.core.kinematics import KinematicModel
from openfactory.core.schema import CARRIER_TYPES, TURNING_TYPES, Scene, load_scene
from openfactory.core.sizing import resolve_defaults
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
    #: Custom meshes (PLAN 2.15) that resolved to a usable file: component id -> file.
    meshes: dict[str, Path] = field(default_factory=dict)
    #: Exam mode (PLAN 2.11): ``exam: switch`` tag -> the operator switch's position, written
    #: over what the simulation computed at the end of every tick. None when not in exam mode.
    exam: dict[str, bool] | None = None

    def enable_exam(self) -> dict[str, bool]:
        """Start exam mode: every ``exam: switch`` DI follows its operator switch (all off)."""
        self.exam = {b.name: False for b in self.scene.tag_bindings() if b.exam}
        self._write_exam()
        return self.exam

    def set_exam_switch(self, tag: str, on: bool) -> None:
        """Turn one operator switch; KeyError if exam mode is off or the tag has no switch."""
        if self.exam is None or tag not in self.exam:
            raise KeyError(f"{tag!r} has no exam switch (exam mode off, or not `exam: switch`)")
        self.exam[tag] = bool(on)
        self._write_exam()

    def _write_exam(self) -> None:
        for tag, on in (self.exam or {}).items():
            self.io.set(tag, on, Writer.SIM)

    def step(self, dt: float) -> None:
        """One tick: move parts, step components in scene order, then write transformed tags.
        Never reads the clock."""
        self.world.tick = self.tick + 1  # parts spawned in this tick carry its number
        self.kinematics.step(dt)
        for comp in self.components:
            comp.tick(dt)  # advances an injected fault's clock, then steps
        if self.transforms is not None:
            self.transforms.step(dt)
        self._write_exam()  # the switch, not the sensor, is what the PLC sees
        self.tick += 1
        self.time += dt
        self.io.set(HEARTBEAT_TAG, heartbeat_value(self.tick, dt), Writer.SIM)

    def component(self, component_id: str) -> Component:
        for comp in self.components:
            if comp.id == component_id:
                return comp
        raise KeyError(component_id)


def build_iotable(scene: Scene) -> IOTable:
    """One tag per bound port, in scene order, then ``SIM_HEARTBEAT_DI``."""
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
    table.add(heartbeat_tag())
    return table


def lint(scene: Scene) -> list[str]:
    """Warnings for a valid scene that is probably not what its author meant."""
    # a turntable's connections depend on its angle, so it is left out of the touching check
    carriers = [
        c for c in scene.components if c.type in CARRIER_TYPES and c.type not in TURNING_TYPES
    ]
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


def glb_problem(path: Path) -> str | None:
    """Why ``path`` is not a binary glTF 2.0 file, or None when its header is sound."""
    with path.open("rb") as f:
        header = f.read(12)
    if len(header) < 12 or header[:4] != b"glTF":
        return "is not a binary glTF (.glb) file"
    version, length = struct.unpack("<II", header[4:])
    if version != 2:
        return f"is glTF version {version}, not 2"
    if length != path.stat().st_size:
        return (
            f"is truncated or padded (header says {length} bytes, file has {path.stat().st_size})"
        )
    return None


def resolve_meshes(scene: Scene, base_dir: Path | None) -> tuple[dict[str, Path], list[str]]:
    """Usable mesh files by component id, and one warning per mesh that is not usable."""
    meshes: dict[str, Path] = {}
    warnings: list[str] = []
    for comp in scene.components:
        if comp.mesh is None:
            continue
        name = comp.mesh.file
        fallback = "; drawing the primitive housing"
        if base_dir is None:
            warnings.append(f"{comp.id}: mesh {name} not resolved (scene not loaded from a file)"
                            f"{fallback}")  # fmt: skip
            continue
        root = base_dir.resolve()
        path = (root / name).resolve()
        if not path.is_relative_to(root):
            why = f"resolves outside the scene's folder ({path})"
        elif not path.is_file():
            why = "not found"
        else:
            why = glb_problem(path)
        if why:
            warnings.append(f"{comp.id}: mesh file {name} {why}{fallback}")
        else:
            meshes[comp.id] = path
    return meshes, warnings


def build(
    scene: Scene, registry: ComponentRegistry | None = None, *, base_dir: Path | None = None
) -> LoadedScene:
    """Instantiate a validated scene. Uses the default registry unless one is given.
    ``base_dir`` is the scene file's folder, against which mesh files resolve."""
    if registry is None:
        importlib.import_module("openfactory.components")  # registers the built-in types
        registry = REGISTRY
    io = build_iotable(scene)
    world = World(seed=scene.scene.seed)
    configs = [resolve_defaults(c, scene) for c in scene.components]
    components = [registry.resolve(c.type)(c, io, world) for c in configs]
    world.conveyors = [c for c in components if c.type in CARRIER_TYPES]
    kinematics = KinematicModel(world, components, scene.connections, scene.scene.friction)
    meshes, mesh_warnings = resolve_meshes(scene, base_dir)
    warnings = lint(scene) + mesh_warnings
    for warning in warnings:
        log.warning("%s: %s", scene.scene.name, warning)
    transforms = TransformStage.build(io, components, scene.tag_bindings())
    transforms.prime()
    return LoadedScene(
        scene,
        io,
        world,
        components,
        kinematics,
        warnings=warnings,
        transforms=transforms,
        meshes=meshes,
    )


def load(path: str | Path, registry: ComponentRegistry | None = None) -> LoadedScene:
    """Load, validate, and instantiate a scene file."""
    return build(load_scene(path), registry, base_dir=Path(path).parent)
