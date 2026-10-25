"""Scene file schema (PLAN §4, scene format v0.1).

The pydantic models here are the contract between the core, the viewer, the exporters, and any
future editor (ADR-0001). Every model forbids unknown keys so typos surface at load time. The JSON
Schema exported from :class:`Scene` is written to ``web/public/scene.schema.json`` for the viewer
and for editor autocompletion; regenerate it with ``python -m openfactory.core.schema``.

I/O bindings
------------
Each component type has an ``io`` model whose fields are its *ports*. A port is bound to a tag name.
The tag's kind comes from its suffix (``_DI``, ``_DO``, ``_AI``, ``_AO``) and must match the port's
direction: inputs from the PLC are DO/AO, outputs to the PLC are DI/AI. Analog ports declare their
engineering range here (for example belt speed 0–100 %), which ``int16_scaled`` encoding needs
(ADR-0003). Port metadata lives in each field's ``json_schema_extra`` under ``x-io-kind``,
``x-range``, and ``x-units`` so the exported JSON Schema carries it too.

Not a safety-rated simulation. OpenFactory is a training and demo tool.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

SCHEMA_VERSION = "0.1"
TAG_MAX_LEN = 40
IO_KINDS = ("DI", "DO", "AI", "AO")
#: Kinds written by the PLC and read by the simulation.
PLC_WRITES = frozenset({"DO", "AO"})

REPO_ROOT = Path(__file__).resolve().parents[3]
JSON_SCHEMA_PATH = REPO_ROOT / "web" / "public" / "scene.schema.json"


def _tag_type(kind: str) -> Any:
    return Annotated[
        str,
        StringConstraints(pattern=rf"^[A-Z][A-Z0-9_]*_{kind}$", max_length=TAG_MAX_LEN),
    ]


DiTag = _tag_type("DI")
DoTag = _tag_type("DO")
AiTag = _tag_type("AI")
AoTag = _tag_type("AO")

ComponentId = Annotated[
    str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]*$", max_length=TAG_MAX_LEN)
]
PositiveFloat = Annotated[float, Field(gt=0)]
Vec3 = tuple[float, float, float]
Size3 = tuple[PositiveFloat, PositiveFloat, PositiveFloat]


def port(
    kind: str,
    description: str,
    *,
    required: bool = False,
    range: tuple[float, float] | None = None,  # noqa: A002 - matches the scene vocabulary
    units: str | None = None,
) -> Any:
    """Declare an I/O port field. ``kind`` must agree with the field's tag annotation."""
    if kind not in IO_KINDS:
        raise ValueError(f"unknown I/O kind {kind!r}")
    if (range is None) != (kind in ("DI", "DO")):
        raise ValueError(
            f"{kind} port {description!r}: analog ports need a range, discrete ports must not"
        )
    extra: dict[str, Any] = {"x-io-kind": kind}
    if range is not None:
        extra["x-range"] = list(range)
        extra["x-units"] = units or ""
    default: Any = ... if required else None
    return Field(default, description=description, json_schema_extra=extra)


class StrictModel(BaseModel):
    """Base for every scene model: unknown keys are errors (PLAN §4)."""

    model_config = ConfigDict(extra="forbid")


class IOModel(StrictModel):
    """Base for a component's port bindings."""


@dataclass(frozen=True)
class PortSpec:
    """Static description of one component port."""

    name: str
    kind: str
    description: str
    required: bool
    range: tuple[float, float] | None
    units: str | None


def port_specs(io_model: type[IOModel]) -> dict[str, PortSpec]:
    """Return the ports declared by an ``io`` model, in declaration order."""
    specs: dict[str, PortSpec] = {}
    for name, field in io_model.model_fields.items():
        extra = field.json_schema_extra
        assert isinstance(extra, dict), f"{io_model.__name__}.{name} is not declared with port()"
        rng = extra.get("x-range")
        specs[name] = PortSpec(
            name=name,
            kind=str(extra["x-io-kind"]),
            description=field.description or name,
            required=field.is_required(),
            range=(float(rng[0]), float(rng[1])) if isinstance(rng, list) else None,
            units=str(extra["x-units"]) if "x-units" in extra else None,
        )
    return specs


# --- Scene metadata and PLC connection -------------------------------------------------------


class SceneMeta(StrictModel):
    name: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]*$", max_length=64)]
    description: Annotated[str, StringConstraints(min_length=1)]
    author: str | None = None
    seed: int = Field(0, description="Seed for all random behavior (emitter jitter, part choice).")
    units: Literal["meters"] = "meters"


class PlcConfig(StrictModel):
    protocol: Literal["modbus_tcp", "logix", "opcua", "none"] = "none"
    role: Literal["server", "client"] = "server"
    bind: str | None = Field(
        None,
        description="Optional listen-address override for openfactory.toml. Never a PLC's IP.",
    )
    port: int | None = Field(
        None, ge=1, le=65535, description="Optional port override for openfactory.toml."
    )
    analog_encoding: Literal["float32", "int16_scaled"] = Field(
        "float32",
        description="Modbus analog encoding for every AI/AO tag in the scene (ADR-0003).",
    )
    connection: str | None = Field(
        None, description="Named connection resolved from openfactory.toml (Logix client role)."
    )


# --- Components (Milestone 1 types, PLAN §5) -------------------------------------------------


class ComponentBase(StrictModel):
    id: ComponentId
    description: Annotated[str, StringConstraints(min_length=1)]
    position: Vec3 = Field(description="Local origin in meters, x along flow, y up.")
    rotation_deg: float = Field(0.0, description="Rotation about the vertical (y) axis, degrees.")


class BeltConveyorIO(IOModel):
    run: DoTag = port("DO", "Run command", required=True)
    speed: AoTag | None = port("AO", "Speed command", range=(0.0, 100.0), units="%")
    running: DiTag | None = port("DI", "Running feedback")


class BeltConveyor(ComponentBase):
    """Origin: center of the infeed end, at belt top-surface height. Flow is local +x."""

    type: Literal["belt_conveyor"]
    length: PositiveFloat
    width: PositiveFloat
    speed_mps: PositiveFloat = Field(description="Surface speed at 100 % speed command.")
    io: BeltConveyorIO


class PhotoEyeIO(IOModel):
    detect: DiTag = port("DI", "Part detected", required=True)


class PhotoEye(ComponentBase):
    """Origin: beam midpoint. The beam spans local z (across the flow), centered on the origin."""

    type: Literal["photo_eye"]
    beam_length: PositiveFloat
    io: PhotoEyeIO


class PusherIO(IOModel):
    extend: DoTag = port("DO", "Extend command", required=True)
    extended: DiTag | None = port("DI", "Extended limit switch")
    retracted: DiTag | None = port("DI", "Retracted limit switch")


class Pusher(ComponentBase):
    """Origin: center of the retracted push plate face. Extends along local +z."""

    type: Literal["pusher"]
    stroke: PositiveFloat
    extend_time_s: PositiveFloat
    io: PusherIO


class PartSpec(StrictModel):
    name: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=32)]
    size: Size3 = Field(description="Box size x, y, z in meters.")
    weight: PositiveFloat = Field(description="Relative selection weight within the emitter.")


class BoxEmitterIO(IOModel):
    enable: DoTag | None = port("DO", "Emitter enable (always enabled when unbound)")


class BoxEmitter(ComponentBase):
    """Origin: spawn point, the center of each new part."""

    type: Literal["box_emitter"]
    interval_s: PositiveFloat
    jitter_s: float = Field(0.0, ge=0, description="Uniform ± jitter added to each interval.")
    parts: list[PartSpec] = Field(min_length=1)
    io: BoxEmitterIO = BoxEmitterIO()

    @model_validator(mode="after")
    def _jitter_below_interval(self) -> BoxEmitter:
        if self.jitter_s >= self.interval_s:
            raise ValueError("jitter_s must be smaller than interval_s")
        if len({p.name for p in self.parts}) != len(self.parts):
            raise ValueError("part names must be unique within an emitter")
        return self


class NoIO(IOModel):
    """A component with no PLC-visible ports."""


class PartRemover(ComponentBase):
    """Origin: center of the removal volume."""

    type: Literal["part_remover"]
    size: Size3
    io: NoIO = NoIO()


class PushbuttonIO(IOModel):
    pressed: DiTag = port("DI", "Pushbutton pressed", required=True)


class Pushbutton(ComponentBase):
    type: Literal["pushbutton"]
    mode: Literal["momentary", "maintained"] = "momentary"
    color: Literal["green", "red", "amber", "white", "blue", "black"] = "green"
    io: PushbuttonIO


class PilotLightIO(IOModel):
    on: DoTag = port("DO", "Light on command", required=True)


class PilotLight(ComponentBase):
    type: Literal["pilot_light"]
    color: Literal["green", "red", "amber", "white", "blue"] = "green"
    io: PilotLightIO


class SelectorSwitchIO(IOModel):
    pos_a: DiTag = port("DI", "Selector in position A", required=True)
    pos_b: DiTag | None = port("DI", "Selector in position B")


class SelectorSwitch(ComponentBase):
    type: Literal["selector_switch"]
    positions: Literal[2, 3] = 2
    initial: Literal["a", "center", "b"] = "a"
    io: SelectorSwitchIO

    @model_validator(mode="after")
    def _center_needs_three_positions(self) -> SelectorSwitch:
        if self.initial == "center" and self.positions != 3:
            raise ValueError("initial: center needs positions: 3")
        return self


Component = Annotated[
    BeltConveyor
    | PhotoEye
    | Pusher
    | BoxEmitter
    | PartRemover
    | Pushbutton
    | PilotLight
    | SelectorSwitch,
    Field(discriminator="type"),
]

#: Component type name → model, for the registry and loader.
COMPONENT_MODELS: dict[str, type[ComponentBase]] = {
    model.model_fields["type"].annotation.__args__[0]: model  # type: ignore[union-attr]
    for model in (
        BeltConveyor,
        PhotoEye,
        Pusher,
        BoxEmitter,
        PartRemover,
        Pushbutton,
        PilotLight,
        SelectorSwitch,
    )
}


@dataclass(frozen=True)
class TagBinding:
    """One tag declared by a scene: which component port it is bound to, and its port metadata."""

    name: str
    kind: str
    component_id: str
    port: PortSpec

    @property
    def description(self) -> str:
        return self.port.description


class Scene(StrictModel):
    """A complete scene file (PLAN §4)."""

    model_config = ConfigDict(
        extra="forbid",
        title="OpenFactory scene",
        json_schema_extra={"$id": "urn:openfactory:scene:0.1"},
    )

    openfactory: Literal["0.1"]
    scene: SceneMeta
    plc: PlcConfig = PlcConfig()
    components: list[Component] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_ids_and_tags(self) -> Scene:
        seen_ids: set[str] = set()
        for comp in self.components:
            if comp.id in seen_ids:
                raise ValueError(f"duplicate component id {comp.id!r}")
            seen_ids.add(comp.id)
        owners: dict[str, str] = {}
        for binding in self.tag_bindings():
            where = f"{binding.component_id}.io.{binding.port.name}"
            if binding.name in owners:
                raise ValueError(
                    f"tag {binding.name!r} bound twice ({owners[binding.name]} and {where}); "
                    "tag names are globally unique"
                )
            owners[binding.name] = where
        return self

    def tag_bindings(self) -> list[TagBinding]:
        """All bound tags in scene order (component order, then port declaration order)."""
        out: list[TagBinding] = []
        for comp in self.components:
            io = comp.io  # type: ignore[attr-defined]
            for name, spec in port_specs(type(io)).items():
                tag = getattr(io, name)
                if tag is not None:
                    out.append(TagBinding(tag, spec.kind, comp.id, spec))
        return out


# --- YAML loading ------------------------------------------------------------------------------


class _SceneYamlLoader(yaml.SafeLoader):
    """SafeLoader where only true/false are booleans.

    PyYAML follows YAML 1.1, which turns ``on``, ``off``, ``yes``, and ``no`` into booleans. That
    would break port names such as ``pilot_light.io.on``.
    """


_SceneYamlLoader.yaml_implicit_resolvers = {
    first: [r for r in resolvers if r[0] != "tag:yaml.org,2002:bool"]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_SceneYamlLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)


def parse_scene(text: str) -> Scene:
    """Parse and validate scene YAML text."""
    data = yaml.load(text, Loader=_SceneYamlLoader)  # noqa: S506 - SafeLoader subclass
    return Scene.model_validate(data)


def load_scene(path: str | Path) -> Scene:
    """Load and validate a scene file."""
    return parse_scene(Path(path).read_text(encoding="utf-8"))


def scene_json_schema() -> str:
    """The scene JSON Schema as the exact text written to ``web/public/scene.schema.json``."""
    return json.dumps(Scene.model_json_schema(), indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Write the JSON Schema, or with ``--check`` exit 1 if the committed copy is stale."""
    args = sys.argv[1:] if argv is None else argv
    text = scene_json_schema()
    if "--check" in args:
        current = JSON_SCHEMA_PATH.read_text(encoding="utf-8") if JSON_SCHEMA_PATH.exists() else ""
        if current != text:
            print(
                f"{JSON_SCHEMA_PATH} is stale; run: python -m openfactory.core.schema",
                file=sys.stderr,
            )
            return 1
        return 0
    JSON_SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
    JSON_SCHEMA_PATH.write_text(text, encoding="utf-8")
    print(f"wrote {JSON_SCHEMA_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
