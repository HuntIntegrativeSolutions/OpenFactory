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
``x-range``, and ``x-units`` so the exported JSON Schema carries it too. A binding is either the tag
name or a mapping ``{tag: NAME, address: N, initial: V}`` with both keys optional: ``address`` pins
the tag's Modbus address (ADR-0004), and ``initial`` (DO and AO tags only) seeds the I/O table at
startup, before any PLC write, as a bool for a DO or an engineering value inside the port's range
for an AO. The PLC's first write replaces it; ``openfactory run --force`` overrides it.

Not a safety-rated simulation. OpenFactory is a training and demo tool.
"""

from __future__ import annotations

import ipaddress
import json
import re
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from openfactory.core.heartbeat import HEARTBEAT_TAG

SCHEMA_VERSION = "0.1"
TAG_MAX_LEN = 40
IO_KINDS = ("DI", "DO", "AI", "AO")
#: Kinds written by the PLC and read by the simulation.
PLC_WRITES = frozenset({"DO", "AO"})

REPO_ROOT = Path(__file__).resolve().parents[3]
JSON_SCHEMA_PATH = REPO_ROOT / "web" / "public" / "scene.schema.json"


def _tag_name(kind: str) -> Any:
    return Annotated[
        str,
        StringConstraints(pattern=rf"^[A-Z][A-Z0-9_]*_{kind}$", max_length=TAG_MAX_LEN),
    ]


class StrictModel(BaseModel):
    """Base for every scene model: unknown keys are errors (PLAN §4)."""

    model_config = ConfigDict(extra="forbid")


class TagSpec(StrictModel):
    """A binding written as a mapping, ``{tag: CONV_01_RUN_DO, address: 12}``.

    ``address`` pins the tag's Modbus address instead of letting allocation choose (ADR-0004). It
    is zero-based in the tag's table (coils for DO, discrete inputs for DI, holding registers for
    AO, input registers for AI); for a float32 analog tag it is the first of the two registers.
    DO and AO specs also take ``initial`` (see :class:`DoTagSpec`, :class:`AoTagSpec`).
    """

    tag: str
    address: int | None = Field(
        None, ge=0, le=65535, description="Pinned zero-based Modbus address (ADR-0004)."
    )


PortRef = Annotated[
    str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]*\.[a-z][a-z0-9_]*$", max_length=80)
]


class DiTagSpec(TagSpec):
    """A DI binding, optionally conditioned before the PLC sees it (PLAN 2.17).

    Applied in this order: ``combine`` the ``from`` ports (``or`` / ``and``), then ``invert``
    (NO/NC), then ``on_delay_s`` / ``off_delay_s``. Delays count from the first tick that sees
    the change, like a TON / TOF in a PLC.
    """

    tag: _tag_name("DI")  # type: ignore[valid-type]
    invert: bool = Field(False, description="Normally closed: the PLC sees the opposite state.")
    on_delay_s: float = Field(0.0, ge=0, description="Turn on only after true this long.")
    off_delay_s: float = Field(0.0, ge=0, description="Turn off only after false this long.")
    combine: Literal["or", "and"] | None = Field(
        None, description="Combine several DI ports into this one tag (with `from`)."
    )
    from_: list[PortRef] | None = Field(
        None,
        alias="from",
        min_length=2,
        description="Ports combined into this tag, as COMPONENT.port; must include this port.",
    )
    exam: Literal["switch"] | None = Field(
        None,
        description="Exam mode (PLAN 2.11, `run --exam`): an operator switch in the viewer drives "
        "this tag instead of the simulation.",
    )

    @model_validator(mode="after")
    def _combine_needs_from(self) -> DiTagSpec:
        if (self.combine is None) != (self.from_ is None):
            raise ValueError(
                "`combine` and `from` go together: combine: or, from: [A.port, B.port]"
            )
        return self


class DoTagSpec(TagSpec):
    tag: _tag_name("DO")  # type: ignore[valid-type]
    invert: bool = Field(
        False, description="The component acts on the opposite of what the PLC writes."
    )
    initial: bool | None = Field(
        None, description="Value at startup, before any PLC write (default false)."
    )


class AiTagSpec(TagSpec):
    """An AI binding. With ``window: [lo, hi]`` the port feeds a DI tag instead, true while the
    value is inside the window (a distance sensor's switching window, PLAN 2.17)."""

    tag: Annotated[
        str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]*_(?:AI|DI)$", max_length=TAG_MAX_LEN)
    ]
    window: tuple[float, float] | None = Field(
        None, description="[lo, hi] in the port's units: bind a _DI tag, true inside the window."
    )

    @model_validator(mode="after")
    def _window_means_di(self) -> AiTagSpec:
        if (self.window is not None) != self.tag.endswith("_DI"):
            raise ValueError(
                f"{self.tag}: an AI port binds an _AI tag, or a _DI tag with window: [lo, hi]"
            )
        if self.window is not None and self.window[0] > self.window[1]:
            raise ValueError(f"{self.tag}: window [lo, hi] needs lo <= hi")
        return self


class AoTagSpec(TagSpec):
    tag: _tag_name("AO")  # type: ignore[valid-type]
    initial: float | None = Field(
        None,
        description="Engineering value at startup, before any PLC write (default 0); must be "
        "inside the port's range.",
    )


#: A port binding: a tag name, or a mapping with the tag and its options.
DiTag = _tag_name("DI") | DiTagSpec
DoTag = _tag_name("DO") | DoTagSpec
AiTag = _tag_name("AI") | AiTagSpec
AoTag = _tag_name("AO") | AoTagSpec


def binding_tag(value: str | TagSpec) -> str:
    """The tag name of a port binding, plain or mapping."""
    return value.tag if isinstance(value, TagSpec) else value


def binding_address(value: str | TagSpec) -> int | None:
    """The pinned Modbus address of a port binding, or None when allocation chooses."""
    return value.address if isinstance(value, TagSpec) else None


def binding_initial(value: str | TagSpec) -> bool | float | None:
    """The startup value of a DO/AO port binding, or None for the kind's default."""
    return getattr(value, "initial", None)


@dataclass(frozen=True)
class Transform:
    """Signal conditioning on one binding (PLAN 2.17); all defaults mean "none"."""

    invert: bool = False
    on_delay_s: float = 0.0
    off_delay_s: float = 0.0
    combine: str | None = None
    sources: tuple[str, ...] = ()  # COMPONENT.port, for combine
    window: tuple[float, float] | None = None

    def __bool__(self) -> bool:
        return self != Transform()

    def describe(self, units: str | None = None) -> str:
        """Human-readable form for the I/O list CSV, e.g. ``or(PE_01.detect, PE_02.detect); NC``
        or ``window 0.25 to 0.35 m`` (``units``: the bound port's)."""
        parts = []
        if self.combine:
            parts.append(f"{self.combine}({', '.join(self.sources)})")
        if self.window is not None:
            unit = f" {units}" if units else ""
            parts.append(f"window {self.window[0]:g} to {self.window[1]:g}{unit}")
        if self.invert:
            parts.append("NC (inverted)")
        if self.on_delay_s:
            parts.append(f"on-delay {self.on_delay_s:g} s")
        if self.off_delay_s:
            parts.append(f"off-delay {self.off_delay_s:g} s")
        return "; ".join(parts)


def binding_transform(value: str | TagSpec) -> Transform:
    if not isinstance(value, TagSpec):
        return Transform()
    return Transform(
        invert=getattr(value, "invert", False),
        on_delay_s=getattr(value, "on_delay_s", 0.0),
        off_delay_s=getattr(value, "off_delay_s", 0.0),
        combine=getattr(value, "combine", None),
        sources=tuple(getattr(value, "from_", None) or ()),
        window=getattr(value, "window", None),
    )


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
    alias: str | None = None,
    range_from: str | None = None,
) -> Any:
    """Declare an I/O port field. ``kind`` must agree with the field's tag annotation. ``alias``
    is the port's name in scene files when it is a Python keyword (``stopper.io.raise``).
    ``range_from`` names a component parameter that sets an analog port's range per instance,
    0 to its value (a distance sensor's 0 to ``beam_length``), instead of a fixed ``range``."""
    if kind not in IO_KINDS:
        raise ValueError(f"unknown I/O kind {kind!r}")
    if (range is None and range_from is None) != (kind in ("DI", "DO")):
        raise ValueError(
            f"{kind} port {description!r}: analog ports need a range, discrete ports must not"
        )
    extra: dict[str, Any] = {"x-io-kind": kind}
    if range is not None:
        extra["x-range"] = list(range)
    if range_from is not None:
        extra["x-range-from"] = range_from
    if range is not None or range_from is not None:
        extra["x-units"] = units or ""
    default: Any = ... if required else None
    return Field(default, alias=alias, description=description, json_schema_extra=extra)


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
    #: Attribute on the ``io`` model, when the port name is a keyword (``raise`` -> ``raise_``).
    attr: str = ""
    #: Component parameter giving the range per instance, 0 to its value (:func:`instance_ports`).
    range_from: str | None = None

    def value(self, io_model: Any) -> Any:
        """The port's binding on a validated ``io`` model, or None when unbound."""
        return getattr(io_model, self.attr or self.name)


def port_specs(io_model: type[IOModel]) -> dict[str, PortSpec]:
    """Return the ports declared by an ``io`` model, in declaration order."""
    specs: dict[str, PortSpec] = {}
    for name, field in io_model.model_fields.items():
        extra = field.json_schema_extra
        assert isinstance(extra, dict), f"{io_model.__name__}.{name} is not declared with port()"
        rng = extra.get("x-range")
        port_name = field.alias or name
        specs[port_name] = PortSpec(
            name=port_name,
            kind=str(extra["x-io-kind"]),
            description=field.description or name,
            required=field.is_required(),
            range=(float(rng[0]), float(rng[1])) if isinstance(rng, list) else None,
            units=str(extra["x-units"]) if "x-units" in extra else None,
            attr=name,
            range_from=extra.get("x-range-from"),  # type: ignore[arg-type]
        )
    return specs


def instance_ports(component: Any) -> dict[str, PortSpec]:
    """A component's ports with per-instance ranges filled in (``range_from``)."""
    specs = port_specs(type(component.io))
    for name, spec in specs.items():
        if spec.range_from is not None:
            specs[name] = replace(spec, range=(0.0, float(getattr(component, spec.range_from))))
    return specs


# --- Scene metadata and PLC connection -------------------------------------------------------


class SceneMeta(StrictModel):
    name: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]*$", max_length=64)]
    description: Annotated[str, StringConstraints(min_length=1)]
    author: str | None = None
    seed: int = Field(0, description="Seed for all random behavior (emitter jitter, part choice).")
    units: Literal["meters"] = "meters"
    friction: float = Field(
        0.8,
        gt=0,
        le=2,
        description="Belt-to-part friction coefficient (mu). In the headless model it caps part "
        "acceleration on a belt at mu x 9.81 m/s2 and limits the belt speed a landing part takes "
        "at impact to mu x its fall speed; the viewer gives belts and parts this friction.",
    )


class PlcConfig(StrictModel):
    protocol: Literal["modbus_tcp", "logix", "opcua", "none"] = "none"
    role: Literal["server", "client"] = "server"
    bind: str | None = Field(
        None,
        description="Optional listen-address override for openfactory.toml. Loopback only "
        "(127.0.0.0/8 or ::1); listening on a network needs the --bind CLI flag. Never a PLC's IP.",
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

    @field_validator("bind")
    @classmethod
    def _bind_is_loopback(cls, value: str | None) -> str | None:
        """A scene file must not open a server to the network (PLAN §6.4); only the CLI can."""
        if value is None:
            return value
        try:
            loopback = ipaddress.ip_address(value).is_loopback
        except ValueError:
            loopback = False
        if not loopback:
            raise ValueError(
                f"plc.bind {value!r} is not a loopback address (127.0.0.0/8 or ::1). A scene may "
                "only move the server within this machine; to listen on a network interface, pass "
                "--bind on the command line (for example: openfactory run SCENE --bind 0.0.0.0)"
            )
        return value


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


class RollerConveyor(ComponentBase):
    """Origin: center of the infeed end, at roller-top height. Flow is local +x (as belt_conveyor).

    Driven rollers: the same run, speed, and running ports and the same speed ramp as a belt. Parts
    accelerate at ``friction`` × g (default: the scene's friction)."""

    type: Literal["roller_conveyor"]
    length: PositiveFloat
    width: PositiveFloat
    speed_mps: PositiveFloat = Field(description="Roller surface speed at 100 % speed command.")
    roller_pitch: PositiveFloat = Field(
        0.1, description="Roller spacing along the flow, meters (viewer only)."
    )
    friction: Annotated[float, Field(gt=0, le=2)] | None = Field(
        None,
        description="Roller-to-part friction coefficient (mu); default: the scene's friction. "
        "Lower values make parts take longer to reach roller speed.",
    )
    io: BeltConveyorIO


class TurntableIO(IOModel):
    run: DoTag = port("DO", "Deck conveyor run command", required=True)
    turn: DoTag = port("DO", "Turn command (held: to turn_deg; released: back to 0)", required=True)
    running: DiTag | None = port("DI", "Deck conveyor running feedback")
    at_0: DiTag | None = port("DI", "At 0 degrees")
    at_90: DiTag | None = port("DI", "At turn_deg (90 degrees)")


class Turntable(ComponentBase):
    """Origin: deck centre (the pivot), at deck-belt top height. At 0 degrees the deck flows along
    local +x; it turns about +y by ``turn_deg`` (90: counter-clockwise seen from above)."""

    type: Literal["turntable"]
    length: PositiveFloat
    width: PositiveFloat
    speed_mps: PositiveFloat = Field(description="Deck belt surface speed while running.")
    turn_deg: Literal[90, -90] = Field(
        90, description="Turn angle: 90 counter-clockwise from above, -90 clockwise."
    )
    turn_time_s: PositiveFloat = Field(1.0, description="Time for the full turn, seconds.")
    io: TurntableIO


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
    plate_width: PositiveFloat = Field(0.3, description="Push plate width along local x, meters.")
    plate_height: PositiveFloat | None = Field(
        None,
        description="Push plate height along y, centered on the origin, meters. Unset: the plate "
        "top reaches 3/4 of the tallest emitted part's height above the belt it sweeps (0.15 m "
        "with no emitter or no belt in its path).",
    )
    io: PusherIO


class StopperIO(IOModel):
    raise_: DoTag = port("DO", "Raise command", required=True, alias="raise")
    raised: DiTag | None = port("DI", "Raised limit switch")
    lowered: DiTag | None = port("DI", "Lowered limit switch")


class Stopper(ComponentBase):
    """Origin: center of the blade's upstream face, at belt-top height. The blade spans local z
    (across the flow) and stops parts moving along local +x."""

    type: Literal["stopper"]
    width: PositiveFloat | None = Field(
        None,
        description="Blade width across the flow, meters. Unset: the width of the belt under it.",
    )
    height: PositiveFloat | None = Field(
        None,
        description="Raised blade top above the belt, meters. Unset: sized from the tallest "
        "emitted part (its centre of mass, at least 0.05 m).",
    )
    raise_time_s: PositiveFloat = Field(0.2, description="Time to raise or lower, seconds.")
    io: StopperIO


class DiverterArmIO(IOModel):
    extend: DoTag = port("DO", "Extend command", required=True)
    extended: DiTag | None = port("DI", "Extended limit switch")
    retracted: DiTag | None = port("DI", "Retracted limit switch")


class DiverterArm(ComponentBase):
    """Origin: the arm's pivot, at belt-top height, at the edge of the belt it diverts from. At
    rest the arm lies along local +x; extending swings it by ``swing_deg`` (positive toward local
    +z, negative toward -z)."""

    type: Literal["diverter_arm"]
    length: PositiveFloat = Field(description="Arm length from the pivot, meters.")
    height: PositiveFloat | None = Field(
        None,
        description="Arm top above the belt, meters. Unset: 3/4 of the tallest emitted part's "
        "height (0.15 m with no emitter).",
    )
    swing_deg: Annotated[float, Field(ge=-90, le=90)] = Field(
        45.0, description="Extended angle from rest, degrees: positive toward local +z."
    )
    extend_time_s: PositiveFloat = Field(0.5, description="Time to swing out or back, seconds.")
    io: DiverterArmIO

    @model_validator(mode="after")
    def _swings(self) -> DiverterArm:
        if self.swing_deg == 0:
            raise ValueError("swing_deg must not be 0")
        return self


class PickAndPlaceIO(IOModel):
    traverse: DoTag = port(
        "DO", "Traverse command (held: to the place side; released: back to pick)", required=True
    )
    lower: DoTag = port("DO", "Lower command", required=True)
    grip: DoTag = port("DO", "Grip command", required=True)
    at_pick: DiTag | None = port("DI", "At the pick side")
    at_place: DiTag | None = port("DI", "At the place side")
    lowered: DiTag | None = port("DI", "Lowered limit switch")
    holding: DiTag | None = port("DI", "Holding a part")


class PickAndPlace(ComponentBase):
    """Origin: bottom centre of the gripper pad, raised, over the pick point. Traverse runs along
    local +x for ``travel``; lowering moves the pad down by ``stroke``."""

    type: Literal["pick_and_place"]
    travel: PositiveFloat = Field(description="Pick to place distance along local +x, meters.")
    stroke: PositiveFloat = Field(description="Vertical lowering distance, meters.")
    travel_time_s: PositiveFloat = Field(1.0, description="Time for the full traverse, seconds.")
    lower_time_s: PositiveFloat = Field(0.3, description="Time to lower or raise, seconds.")
    grip_size: Size3 = Field((0.25, 0.05, 0.25), description="Gripper pad size x, y, z in meters.")
    io: PickAndPlaceIO


class DistanceSensorIO(IOModel):
    distance: AiTag = port(
        "AI",
        "Distance to the nearest part or belt along the beam",
        required=True,
        units="m",
        range_from="beam_length",
    )
    switching: AiTag | None = port(
        "AI",
        "Switching output (the distance, windowed to a DI)",
        units="m",
        range_from="beam_length",
    )


class DistanceSensor(ComponentBase):
    """Origin: the sensor face. The beam runs ``beam_length`` from it along ``axis``: ``+z``
    across the flow (rotated with the sensor), or ``-y`` straight down."""

    type: Literal["distance_sensor"]
    axis: Literal["+z", "-y"] = Field("+z", description="Beam direction in the sensor's frame.")
    beam_length: PositiveFloat = Field(
        1.0, description="Measuring range, meters; the distance ports' range is 0 to this."
    )
    io: DistanceSensorIO


#: Colors a part may have (PLAN 2.7, sorting-by-color); a part without one is plain cardboard.
PartColor = Literal["red", "green", "blue", "yellow", "white", "black"]


class ColorSensorIO(IOModel):
    match: DiTag = port("DI", "Taught color seen", required=True)
    detect: DiTag | None = port("DI", "Part in range")


class ColorSensor(ComponentBase):
    """Origin: the sensor face. A diffuse sensor: the beam runs ``beam_length`` from it along
    ``axis`` (``-y`` straight down, or ``+z`` across the flow) to the nearest part or belt."""

    type: Literal["color_sensor"]
    teach: PartColor = Field(description="The taught color: match is true while it is seen.")
    axis: Literal["+z", "-y"] = Field("-y", description="Beam direction in the sensor's frame.")
    beam_length: PositiveFloat = Field(0.5, description="Sensing range, meters.")
    io: ColorSensorIO


class PartSpec(StrictModel):
    name: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=32)]
    size: Size3 = Field(description="Box size x, y, z in meters.")
    weight: PositiveFloat = Field(description="Relative selection weight within the emitter.")
    color: PartColor | None = Field(
        None, description="Part color, seen by a color_sensor; unset: plain cardboard."
    )


class BoxEmitterIO(IOModel):
    enable: DoTag | None = port("DO", "Emitter enable")


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
    | RollerConveyor
    | Stopper
    | Turntable
    | DiverterArm
    | PickAndPlace
    | DistanceSensor
    | ColorSensor
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
        RollerConveyor,
        Stopper,
        Turntable,
        DiverterArm,
        PickAndPlace,
        DistanceSensor,
        ColorSensor,
        PhotoEye,
        Pusher,
        BoxEmitter,
        PartRemover,
        Pushbutton,
        PilotLight,
        SelectorSwitch,
    )
}


#: Component types that carry parts on their top surface (PLAN §5). Connections join these.
CARRIER_TYPES = frozenset({"belt_conveyor", "roller_conveyor", "turntable"})
#: Conveyors that may feed, and be fed by, several others: the active connection is the aligned one.
TURNING_TYPES = frozenset({"turntable"})


class Connection(StrictModel):
    """One conveyor feeding another: parts leaving ``from``'s discharge end land on ``to``'s infeed.

    The handoff bridges any gap, height step, or turn between the two, like a transfer plate.
    """

    from_: ComponentId = Field(alias="from", description="Upstream conveyor id.")
    to: ComponentId = Field(description="Downstream conveyor id.")


@dataclass(frozen=True)
class TagBinding:
    """One tag declared by a scene: which component port it is bound to, and its port metadata."""

    name: str
    kind: str
    component_id: str
    port: PortSpec
    #: Pinned Modbus address (ADR-0004), or None when allocation chooses.
    address: int | None = None
    #: Startup value for a DO/AO tag, or None for False / 0.
    initial: bool | float | None = None
    #: Signal conditioning applied in the I/O layer (PLAN 2.17).
    transform: Transform = Transform()
    #: Marked ``exam: switch``: in exam mode an operator switch drives the tag (PLAN 2.11).
    exam: bool = False

    @property
    def description(self) -> str:
        return self.port.description


class Checks(StrictModel):
    """Exercise checks (PLAN 2.11), evaluated by ``openfactory test`` at the end of a run."""

    duration_s: PositiveFloat = Field(60.0, description="How long `openfactory test` runs.")
    parts_reached: dict[ComponentId, str | list[str]] = Field(
        default_factory=dict,
        description="Part remover -> part name(s): at least one part of each name reaches that "
        "remover, and no part of that name reaches any other.",
    )
    none_lost: bool = Field(False, description="No part falls out of the scene.")
    max_cycle_s: PositiveFloat | None = Field(
        None,
        description="Every removed part spent at most this long from spawn to remover, and no "
        "part still in the scene at the end is older.",
    )

    def reached(self) -> dict[str, list[str]]:
        return {r: [n] if isinstance(n, str) else list(n) for r, n in self.parts_reached.items()}


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
    connections: list[Connection] = Field(
        default_factory=list,
        description="Which conveyor feeds which. Loops (recirculation) are allowed; "
        "each conveyor feeds at most one downstream conveyor, except a turntable, which may "
        "feed several and hands parts across only while its deck is aligned with one.",
    )
    checks: Checks | None = Field(None, description="Exercise checks for `openfactory test`.")

    @model_validator(mode="after")
    def _unique_ids_and_tags(self) -> Scene:
        seen_ids: set[str] = set()
        for comp in self.components:
            if comp.id in seen_ids:
                raise ValueError(f"duplicate component id {comp.id!r}")
            seen_ids.add(comp.id)
        types = {c.id: c.type for c in self.components}
        downstream: dict[str, str] = {}
        for conn in self.connections:
            for end in (conn.from_, conn.to):
                if end not in types:
                    raise ValueError(f"connection references unknown component {end!r}")
                if types[end] not in CARRIER_TYPES:
                    raise ValueError(
                        f"connection end {end!r} is a {types[end]}, not a conveyor "
                        f"({', '.join(sorted(CARRIER_TYPES))})"
                    )
            if conn.from_ == conn.to:
                raise ValueError(f"conveyor {conn.from_!r} cannot feed itself")
            if types[conn.from_] in TURNING_TYPES:
                continue  # a turntable feeds whichever connected conveyor its deck lines up with
            if conn.from_ in downstream:
                raise ValueError(
                    f"conveyor {conn.from_!r} feeds both {downstream[conn.from_]!r} and "
                    f"{conn.to!r}; a conveyor has one discharge"
                )
            downstream[conn.from_] = conn.to
        owners: dict[str, str] = {}
        for binding in self.tag_bindings():
            where = f"{binding.component_id}.io.{binding.port.name}"
            if binding.name == HEARTBEAT_TAG:
                raise ValueError(
                    f"{where}: {HEARTBEAT_TAG} is reserved for the simulator's own heartbeat "
                    "(every scene has it); use another tag name"
                )
            if binding.name in owners:
                raise ValueError(
                    f"tag {binding.name!r} bound twice ({owners[binding.name]} and {where}); "
                    "tag names are globally unique"
                )
            owners[binding.name] = where
            sources = binding.transform.sources
            if sources:
                host = f"{binding.component_id}.{binding.port.name}"
                if host not in sources:
                    raise ValueError(
                        f"{where}: `from` must list this port ({host}) among the ports it combines"
                    )
                for ref in sources:
                    cid, _, port_name = ref.partition(".")
                    comp = next((c for c in self.components if c.id == cid), None)
                    if comp is None:
                        raise ValueError(f"{where}: `from` names unknown component {cid!r}")
                    ports = port_specs(type(comp.io))  # type: ignore[attr-defined]
                    if port_name not in ports or ports[port_name].kind != "DI":
                        di_ports = ", ".join(n for n, p in ports.items() if p.kind == "DI")
                        raise ValueError(
                            f"{where}: `from` entry {ref!r} is not a DI port of {cid} "
                            f"(DI ports: {di_ports})"
                        )
            rng = binding.port.range
            if (
                isinstance(binding.initial, float)
                and rng
                and not rng[0] <= binding.initial <= rng[1]
            ):
                raise ValueError(
                    f"{where}: initial {binding.initial:g} for {binding.name} is outside the "
                    f"port's range {rng[0]:g} to {rng[1]:g} {binding.port.units or ''}".rstrip()
                )
        return self

    def tag_bindings(self) -> list[TagBinding]:
        """All bound tags in scene order (component order, then port declaration order)."""
        out: list[TagBinding] = []
        for comp in self.components:
            io = comp.io  # type: ignore[attr-defined]
            for spec in instance_ports(comp).values():
                value = spec.value(io)
                if value is not None:
                    transform = binding_transform(value)
                    out.append(
                        TagBinding(
                            binding_tag(value),
                            "DI" if transform.window is not None else spec.kind,
                            comp.id,
                            spec,
                            binding_address(value),
                            binding_initial(value),
                            transform,
                            getattr(value, "exam", None) == "switch",
                        )
                    )
        return out

    @model_validator(mode="after")
    def _checks_name_real_removers_and_parts(self) -> Scene:
        if self.checks is None:
            return self
        types = {c.id: c.type for c in self.components}
        names = {p.name for c in self.components if isinstance(c, BoxEmitter) for p in c.parts}
        claimed: dict[str, str] = {}
        for remover, wanted in self.checks.reached().items():
            for name in wanted:
                if name in claimed:
                    raise ValueError(
                        f"checks.parts_reached: {name!r} is listed for both {claimed[name]!r} and "
                        f"{remover!r}; each part name may reach only one remover"
                    )
                claimed[name] = remover
            if types.get(remover) != "part_remover":
                found = f"a {types[remover]}" if remover in types else "not in the scene"
                raise ValueError(
                    f"checks.parts_reached: {remover!r} is {found}, not a part_remover"
                )
            for name in wanted:
                if name not in names:
                    raise ValueError(
                        f"checks.parts_reached.{remover}: no box_emitter makes a part named "
                        f"{name!r} (it makes {', '.join(sorted(names)) or 'none'})"
                    )
        return self


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
