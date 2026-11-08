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
import math
import re
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    PrivateAttr,
    StringConstraints,
    Tag,
    ValidationInfo,
    field_validator,
    model_validator,
)

from openfactory.core.expr import parse
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


class AnalogRange(StrictModel):
    """Per-tag range and units on an analog binding (PLAN 3.7): ``range: [lo, hi]`` is the tag's
    engineering span, which ``int16_scaled`` scales raw 0 to 32767 across (ADR-0003) and the
    lockfile, I/O list, CONTEXT.md and Ignition export carry, in place of the port's own range.
    It is in the port's units: the component still reads and writes the port's physical value,
    so ``speed: {tag: X_AO, range: [0, 50]}`` makes full scale 50 %. ``units`` names the units
    of a port that declares none of its own; it cannot convert a port's units."""

    range: tuple[float, float] | None = Field(
        None,
        description="[lo, hi]: the tag's engineering range in the port's units, in place of the "
        "port's own (PLAN 3.7).",
    )
    units: Annotated[str, StringConstraints(min_length=1, max_length=16)] | None = Field(
        None, description="Engineering units of a port that declares none (PLAN 3.7)."
    )

    @model_validator(mode="after")
    def _range_ordered(self) -> AnalogRange:
        if self.range is not None and not self.range[0] < self.range[1]:
            raise ValueError(
                f"{getattr(self, 'tag', '')}: range [lo, hi] needs lo < hi".lstrip(": ")
            )
        return self


class AiTagSpec(TagSpec, AnalogRange):
    """An AI binding. With ``window: [lo, hi]`` the port feeds a DI tag instead, true while the
    value is inside the window (a distance sensor's switching window, PLAN 2.17). ``range`` and
    ``units`` set the tag's own span (PLAN 3.7; not with ``window``, which makes it a DI)."""

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
        if self.window is not None and (self.range is not None or self.units is not None):
            raise ValueError(f"{self.tag}: a windowed binding is a DI and takes no range or units")
        return self


class AoTagSpec(TagSpec, AnalogRange):
    """An AO binding; ``range`` and ``units`` set the tag's own span (PLAN 3.7)."""

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


def binding_port(spec: PortSpec, value: str | TagSpec) -> PortSpec:
    """The port as its tag sees it: the binding's ``range`` and ``units`` (PLAN 3.7) in place of
    the port's own. Components keep the port's own (:func:`instance_ports`)."""
    rng, units = getattr(value, "range", None), getattr(value, "units", None)
    if rng is None and units is None:
        return spec
    return replace(
        spec,
        range=(float(rng[0]), float(rng[1])) if rng is not None else spec.range,
        units=units if units is not None else spec.units,
    )


@dataclass(frozen=True)
class Transform:
    """Signal conditioning on one binding (PLAN 2.17); all defaults mean "none"."""

    invert: bool = False
    on_delay_s: float = 0.0
    off_delay_s: float = 0.0
    combine: str | None = None
    sources: tuple[str, ...] = ()  # COMPONENT.port, for combine (and the refs of `expr`)
    window: tuple[float, float] | None = None
    #: A composite's computed DI (PLAN 2.16): a boolean expression over ``sources``.
    expr: str | None = None

    def __bool__(self) -> bool:
        return self != Transform()

    def describe(self, units: str | None = None) -> str:
        """Human-readable form for the I/O list CSV, e.g. ``or(PE_01.detect, PE_02.detect); NC``
        or ``window 0.25 to 0.35 m`` (``units``: the bound port's)."""
        parts = []
        if self.expr:
            parts.append(f"expr({self.expr})")
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
    units_from: str | None = None,
) -> Any:
    """Declare an I/O port field. ``kind`` must agree with the field's tag annotation. ``alias``
    is the port's name in scene files when it is a Python keyword (``stopper.io.raise``).
    ``range_from`` names a component parameter that sets an analog port's range per instance,
    0 to its value (a distance sensor's 0 to ``beam_length``) or, when the parameter is a
    ``[lo, hi]`` pair, lo to hi (a counter display's ``range``), instead of a fixed ``range``."""
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
    if units_from is not None:
        extra["x-units-from"] = units_from
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
    #: Component parameter giving the units per instance (:func:`instance_ports`).
    units_from: str | None = None

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
            units_from=extra.get("x-units-from"),  # type: ignore[arg-type]
        )
    return specs


def instance_ports(component: Any) -> dict[str, PortSpec]:
    """A component's ports with per-instance ranges filled in (``range_from``)."""
    specs = port_specs(type(component.io))
    for name, spec in specs.items():
        if spec.range_from is not None:
            value = getattr(component, spec.range_from)
            span = (float(value[0]), float(value[1])) if isinstance(value, tuple | list) else (
                0.0, float(value))  # fmt: skip
            specs[name] = replace(spec, range=span)
        if spec.units_from is not None:
            specs[name] = replace(specs[name], units=str(getattr(component, spec.units_from)))
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


#: A URL scheme (``http:``, ``file:``, ``data:``); a Windows drive letter is caught separately.
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]+:")


class MeshSpec(StrictModel):
    """A custom housing for the viewer (PLAN 2.15): a ``.glb`` file beside the scene, drawn in
    place of the component's fixed housing. Visual only; the core, colliders, sensors, and parity
    use the declared dimensions."""

    file: str = Field(
        description="Path relative to the scene file's folder, e.g. models/press.glb. A .glb "
        "(binary glTF 2.0) only; no absolute path, no '..', no URL."
    )
    scale: PositiveFloat | tuple[PositiveFloat, PositiveFloat, PositiveFloat] = Field(
        1.0, description="Uniform scale, or [sx, sy, sz]."
    )
    offset: Vec3 = Field(
        (0.0, 0.0, 0.0),
        description="Meters in the component's local frame, applied after scale and rotation.",
    )
    rotation_deg: float = Field(
        0.0, description="Extra turn about local y, for a model authored facing another way."
    )

    @field_validator("file")
    @classmethod
    def _file_is_a_local_glb(cls, value: str) -> str:
        path = value.replace("\\", "/")
        why = None
        if _SCHEME.match(path) and not re.match(r"^[A-Za-z]:", path):
            why = "is a URL; give a path relative to the scene file"
        elif path.startswith("/") or re.match(r"^[A-Za-z]:", path):
            why = "is absolute; give a path relative to the scene file"
        elif ".." in path.split("/"):
            why = "leaves the scene's folder ('..'); keep meshes beside the scene"
        elif not path.lower().endswith(".glb"):
            why = "is not a .glb file (binary glTF 2.0 is the only mesh format)"
        elif path in ("", ".glb") or path.endswith("/"):
            why = "names no file"
        if why:
            raise ValueError(f"mesh.file {value!r} {why}")
        return path

    def scale3(self) -> tuple[float, float, float]:
        s = self.scale
        return (s, s, s) if isinstance(s, float | int) else tuple(s)  # type: ignore[return-value]


class ComponentBase(StrictModel):
    id: ComponentId
    description: Annotated[str, StringConstraints(min_length=1)]
    position: Vec3 = Field(description="Local origin in meters, x along flow, y up.")
    rotation_deg: float = Field(0.0, description="Rotation about the vertical (y) axis, degrees.")
    mesh: MeshSpec | None = Field(
        None, description="Custom housing drawn by the viewer (PLAN 2.15); visual only."
    )


class BeltConveyorIO(IOModel):
    run: DoTag = port("DO", "Run command", required=True)
    speed: AoTag | None = port("AO", "Speed command", range=(0.0, 100.0), units="%")
    running: DiTag | None = port("DI", "Running feedback")
    reverse: DoTag | None = port("DO", "Reverse command (with run: flow toward the infeed end)")


class _Reversible(ComponentBase):
    """A conveyor that may run backward (PLAN 2.5b): ``bidirectional: true`` with a ``reverse``
    DO bound, and neither without the other."""

    bidirectional: bool = Field(
        False,
        description="Runs both ways: needs io.reverse; with run and reverse on, parts move toward "
        "the infeed end and hand back to the conveyor feeding it.",
    )

    @model_validator(mode="after")
    def _reverse_needs_bidirectional(self) -> _Reversible:
        bound = self.io.reverse is not None  # type: ignore[attr-defined]
        if self.bidirectional and not bound:
            raise ValueError("bidirectional: true needs io.reverse (the reverse command DO)")
        if bound and not self.bidirectional:
            raise ValueError("io.reverse needs bidirectional: true")
        return self


class BeltConveyor(_Reversible):
    """Origin: center of the infeed end, at belt top-surface height. Flow is local +x."""

    type: Literal["belt_conveyor"]
    length: PositiveFloat
    width: PositiveFloat
    speed_mps: PositiveFloat = Field(description="Surface speed at 100 % speed command.")
    io: BeltConveyorIO


class RollerConveyor(_Reversible):
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


class LiftIO(IOModel):
    run: DoTag = port("DO", "Deck conveyor run command", required=True)
    raise_: DoTag = port(
        "DO", "Raise command (held: to the top; released: back to the bottom)", required=True,
        alias="raise",
    )  # fmt: skip
    running: DiTag | None = port("DI", "Deck conveyor running feedback")
    at_top: DiTag | None = port("DI", "At the top")
    at_bottom: DiTag | None = port("DI", "At the bottom")


class Lift(ComponentBase):
    """Origin: centre of the deck's infeed end, at deck-belt top height with the deck at the
    bottom (as belt_conveyor). Flow is local +x; the deck rises ``lift_height`` straight up."""

    type: Literal["lift"]
    length: PositiveFloat
    width: PositiveFloat
    speed_mps: PositiveFloat = Field(description="Deck belt surface speed while running.")
    lift_height: PositiveFloat = Field(description="Rise from the bottom to the top, meters.")
    lift_time_s: PositiveFloat = Field(2.0, description="Time for the full rise, seconds.")
    start: Literal["bottom", "top"] = Field("bottom", description="Where the deck starts.")
    io: LiftIO


class PhotoEyeIO(IOModel):
    detect: DiTag = port("DI", "Part detected", required=True)


class PhotoEye(ComponentBase):
    """Origin: beam midpoint. The beam spans local z (across the flow), centered on the origin."""

    type: Literal["photo_eye"]
    beam_length: PositiveFloat
    debounce_ms: Annotated[float, Field(ge=0, le=1000)] = Field(
        20.0,
        description="On/off debounce of detect, milliseconds (PLAN 2.5b): a change must last this "
        "long to reach the PLC. 20 (one tick at 50 Hz) unless set.",
    )
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
    contact: DiTag | None = port("DI", "Pad touching a part (resting on one, or holding one)")


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
    debounce_ms: Annotated[float, Field(ge=0, le=1000)] = Field(
        0.0,
        description="On/off debounce of match and detect, each on its own, milliseconds "
        "(PLAN 2.5b); 0 (the default) follows the part at the step boundary.",
    )
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


class CounterDisplayIO(IOModel):
    value: AoTag = port("AO", "Value to display", required=True, units="count", range_from="range")


class CounterDisplay(ComponentBase):
    """Origin: the centre of the display face. A panel readout of ``digits`` seven-segment digits
    (PLAN 2.5b)."""

    type: Literal["counter_display"]
    digits: Annotated[int, Field(ge=1, le=6)] = Field(4, description="Number of digits shown.")
    decimals: Annotated[int, Field(ge=0, le=3)] = Field(
        0, description="Digits after the decimal point (must be fewer than digits)."
    )
    range: tuple[float, float] | None = Field(
        None,
        description="The value's range [lo, hi], used to scale int16_scaled (raw 0 and 32767). "
        "Default: 0 to the largest number the digits show (9999 for 4 digits, 0 decimals).",
    )
    color: Literal["red", "green", "amber"] = Field("red", description="Segment color.")
    io: CounterDisplayIO

    @model_validator(mode="after")
    def _range_fits(self) -> CounterDisplay:
        if self.decimals >= self.digits:
            raise ValueError("decimals must be fewer than digits")
        if self.range is None:
            self.range = (0.0, (10**self.digits - 1) / 10**self.decimals)
        elif not self.range[0] < self.range[1]:
            raise ValueError(f"range {list(self.range)} must be [lo, hi] with lo < hi")
        return self


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


# --- Process components (PLAN 3.2, ADR-0005) ---------------------------------------------------

#: A tank nozzle's name: lowercase, as `TK_01.<name>` in a link end.
NozzleName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=32)]


class TankIO(IOModel):
    level: AiTag | None = port("AI", "Level", units="m", range_from="height")
    hi_sw: DiTag | None = port("DI", "High level switch (on at hi_level)")
    lo_sw: DiTag | None = port("DI", "Low level switch (on at lo_level)")
    temp: AiTag | None = port("AI", "Liquid temperature", range=(0.0, 100.0), units="°C")


class Tank(ComponentBase):
    """Origin: the centre of the tank bottom, which stands at the origin's height. An open tank of
    constant cross-section in the flow network (PLAN 3.1)."""

    type: Literal["tank"]
    shape: Literal["vertical_cylinder", "box"] = Field(
        "vertical_cylinder", description="A vertical cylinder (diameter) or a box (length, width)."
    )
    diameter: PositiveFloat = Field(1.0, description="Cylinder diameter, m.")
    length: PositiveFloat | None = Field(None, description="Box length along local x, m.")
    width: PositiveFloat | None = Field(None, description="Box width along local z, m.")
    height: PositiveFloat = Field(2.0, description="Height, m; the tank overflows at this level.")
    initial_level: float = Field(0.5, ge=0, description="Level at start, m.")
    initial_temperature_c: float = Field(20.0, description="Liquid temperature at start, °C.")
    nozzles: dict[NozzleName, float] = Field(
        default_factory=dict,
        description="More nozzles: name -> height above the bottom, m. `bottom` (0) and `top` "
        "(the height) always exist.",
    )
    hi_level: PositiveFloat | None = Field(
        None,
        description="Level where hi_sw turns on, m (off 0.02 m lower). Default 90 % of height.",
    )
    lo_level: float | None = Field(
        None, ge=0, description="Level where lo_sw turns on, m (off 0.02 m higher). Default 10 %."
    )
    io: TankIO = Field(default_factory=TankIO)

    @model_validator(mode="after")
    def _fits(self) -> Tank:
        if self.shape == "box" and (self.length is None or self.width is None):
            raise ValueError(f"{self.id}: a box tank needs length and width")
        if self.shape == "vertical_cylinder" and (self.length or self.width):
            raise ValueError(f"{self.id}: a vertical_cylinder takes diameter, not length or width")
        if self.initial_level > self.height:
            raise ValueError(f"{self.id}: initial_level {self.initial_level:g} m is above height")
        for name, z in self.nozzles.items():
            if not 0 <= z <= self.height:
                raise ValueError(f"{self.id}: nozzle {name} at {z:g} m is outside 0 to height")
        if not 0 <= self.low() < self.high() <= self.height:
            raise ValueError(f"{self.id}: needs 0 <= lo_level < hi_level <= height")
        return self

    def high(self) -> float:
        return self.hi_level if self.hi_level is not None else 0.9 * self.height

    def low(self) -> float:
        return self.lo_level if self.lo_level is not None else 0.1 * self.height

    def area(self) -> float:
        if self.shape == "box":
            return self.length * self.width  # type: ignore[operator]
        return math.pi * self.diameter**2 / 4

    def all_nozzles(self) -> dict[str, float]:
        return {"bottom": 0.0, "top": self.height, **self.nozzles}


#: An ingredient's name in a process scene (PLAN 3.1 mixing): lowercase.
IngredientName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=32)]


class SupplyIO(IOModel):
    """A supply has no ports: a valve in front of it is the control element."""


class Supply(ComponentBase):
    """Origin: the connection point. A source of liquid at a fixed gauge pressure (PLAN 3.2)."""

    type: Literal["supply"]
    pressure_bar: float = Field(2.0, ge=0, description="Gauge pressure at the origin, bar.")
    temperature_c: float = Field(15.0, description="Temperature of what it delivers, °C.")
    ingredient: IngredientName = Field("water", description="What it delivers, for mixing.")
    io: SupplyIO = Field(default_factory=SupplyIO)


class DrainIO(IOModel):
    """A drain has no ports."""


class Drain(ComponentBase):
    """Origin: the connection point. Where liquid leaves the plant: open to atmosphere, or a
    consumer drawing a demand (PLAN 3.2, ADR-0005 decision 4)."""

    type: Literal["drain"]
    demand_m3h: float | None = Field(
        None,
        ge=0,
        description="A consumer's demand, m³/h. Unset (and no schedule): an open drain that takes "
        "whatever arrives.",
    )
    schedule: list[tuple[Annotated[float, Field(ge=0)], Annotated[float, Field(ge=0)]]] | None = (
        Field(
            None,
            description="Demand steps [[time s, m³/h], ...], times rising: each applies from its "
            "time; before the first, demand_m3h (or 0).",
        )
    )
    repeat: bool = Field(
        False, description="Start the schedule over at its last step's time (the period)."
    )
    required_head_m: PositiveFloat = Field(
        2.0, description="Pressure head at which the full demand flows, m."
    )
    minimum_head_m: float = Field(0.0, ge=0, description="Pressure head below which none flows, m.")
    io: DrainIO = Field(default_factory=DrainIO)

    @model_validator(mode="after")
    def _schedule(self) -> Drain:
        if self.minimum_head_m >= self.required_head_m:
            raise ValueError(f"{self.id}: minimum_head_m must be below required_head_m")
        times = [t for t, _ in self.schedule or []]
        if any(b <= a for a, b in zip(times, times[1:], strict=False)):
            raise ValueError(f"{self.id}: schedule times must rise")
        if self.repeat and (not times or times[-1] <= 0):
            raise ValueError(f"{self.id}: repeat needs a schedule whose last step is after 0 s")
        return self

    def has_demand(self) -> bool:
        return self.demand_m3h is not None or bool(self.schedule)

    def demand_at(self, t: float) -> float:
        """Demand (m³/h) at simulated time ``t``."""
        steps = self.schedule or []
        if self.repeat and steps:
            t = t % steps[-1][0]
        value = self.demand_m3h or 0.0
        for start, demand in steps:
            if t >= start:
                value = demand
        return value


class JunctionIO(IOModel):
    """A junction has no ports: a transmitter reads its pressure (``measures: J_01.pressure``)."""


class Junction(ComponentBase):
    """Origin: the junction point. Where links meet; its head is solved (PLAN 3.2)."""

    type: Literal["junction"]
    io: JunctionIO = Field(default_factory=JunctionIO)


#: A link end (PLAN 3.2): a component id, or a tank's nozzle (``TK_01.bottom``).
LinkEnd = Annotated[
    str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]*(\.[a-z][a-z0-9_]*)?$", max_length=80)
]


class _Link(ComponentBase):
    """A link of the flow network between two ends (ADR-0005): a tank's nozzle, a supply, drain
    or junction, or another link (joined through an implicit junction; both name each other)."""

    from_: LinkEnd = Field(
        alias="from", description="Upstream end: TANK.nozzle, a supply, drain, junction, or link."
    )
    to: LinkEnd = Field(description="Downstream end, as `from`.")


class PipeIO(IOModel):
    """A pipe has no ports: a transmitter reads its flow (``measures: PIPE_01.flow``)."""


class Pipe(_Link):
    """Origin: anywhere along it (an implicit junction made with it downstream sits here). A run
    of pipe: a resistance by Darcy-Weisbach with a fixed friction factor (PLAN 3.2)."""

    type: Literal["pipe"]
    diameter: PositiveFloat = Field(0.05, description="Inside diameter, m.")
    length: PositiveFloat | None = Field(
        None, description="Length, m. Default: the drawn length, its ends through `path`."
    )
    path: list[Vec3] = Field(
        default_factory=list, description="Points it runs through, scene coordinates (viewer)."
    )
    friction: PositiveFloat = Field(0.02, description="Darcy friction factor.")
    minor_loss: float = Field(1.5, ge=0, description="Sum of minor-loss coefficients K.")
    check_valve: bool = Field(False, description="Passes flow only from `from` to `to`.")
    io: PipeIO = Field(default_factory=PipeIO)


class _Valve(_Link):
    """A valve: a resistance set by its flow coefficient and opening (ADR-0005 decision 6)."""

    cv: PositiveFloat | None = Field(
        None, description="Flow coefficient, US gpm at a 1 psi drop. Default 10 (with no kv)."
    )
    kv: PositiveFloat | None = Field(
        None, description="Flow coefficient, m³/h at a 1 bar drop, instead of cv (Kv = 0.865 Cv)."
    )

    @model_validator(mode="after")
    def _one_coefficient(self) -> _Valve:
        if self.cv is not None and self.kv is not None:
            raise ValueError(f"{self.id}: give cv or kv, not both")
        return self

    def kv_value(self) -> float:
        """Kv, m³/h at 1 bar: ``kv``, or 0.865 × ``cv`` (default Cv 10)."""
        return (
            self.kv if self.kv is not None else 0.865 * (self.cv if self.cv is not None else 10.0)
        )


class ValveOnOffIO(IOModel):
    open: DoTag = port("DO", "Open command", required=True)
    opened: DiTag | None = port("DI", "Open limit switch")
    closed: DiTag | None = port("DI", "Closed limit switch")


class ValveOnOff(_Valve):
    """Origin: the valve body. An on/off valve with a travel time (PLAN 3.2)."""

    type: Literal["valve_onoff"]
    travel_s: PositiveFloat = Field(2.0, description="Full open or close time, s.")
    io: ValveOnOffIO


class ValveControlIO(IOModel):
    position: AoTag | None = port("AO", "Position command", range=(0.0, 100.0), units="%")
    position_fb: AiTag | None = port("AI", "Position feedback", range=(0.0, 100.0), units="%")


class ValveControl(_Valve):
    """Origin: the valve body. A throttling valve positioned by an AO, or a hand valve held at
    ``opening`` when the AO is unbound (PLAN 3.2, ADR-0005 decision 6)."""

    type: Literal["valve_control"]
    characteristic: Literal["equal_percentage", "linear"] = Field(
        "equal_percentage", description="Inherent flow characteristic."
    )
    rangeability: Annotated[float, Field(gt=1)] = Field(
        50.0, description="Equal percentage: full capacity over the capacity just open."
    )
    stroke_s: PositiveFloat = Field(10.0, description="Full stroke at the rate limit, s.")
    lag_s: float = Field(0.5, ge=0, description="First-order lag of the actuator, s.")
    fail: Literal["closed", "open", "last"] = Field(
        "closed", description="Where it goes while the command reads below 0 % (float32 only)."
    )
    opening: Annotated[float, Field(ge=0, le=100)] = Field(
        100.0, description="Opening held while position is unbound (a hand valve), %."
    )
    io: ValveControlIO = Field(default_factory=ValveControlIO)


class PumpIO(IOModel):
    run: DoTag = port("DO", "Run command", required=True)
    speed: AoTag | None = port("AO", "Speed command", range=(0.0, 100.0), units="%")
    running: DiTag | None = port("DI", "Running feedback")
    flow: AiTag | None = port("AI", "Flow", units="m³/h", range_from="max_flow_m3h")


class Pump(_Link):
    """Origin: the pump centre. A centrifugal pump on EPANET's power curve (PLAN 3.2)."""

    type: Literal["pump"]
    design_flow_m3h: PositiveFloat = Field(6.0, description="Design point flow, m³/h.")
    design_head_m: PositiveFloat = Field(8.0, description="Design point head, m.")
    curve: list[tuple[Annotated[float, Field(ge=0)], Annotated[float, Field(ge=0)]]] | None = Field(
        None,
        description="Three points [[m³/h, m], ...], the first at zero flow, instead of the "
        "single-point curve (shutoff 133 % of the design head, zero head at twice the "
        "design flow).",
    )
    ramp_s: PositiveFloat = Field(1.0, description="Time from stopped to full speed, s.")
    efficiency: Annotated[float, Field(gt=0, le=1)] = Field(
        0.6, description="Pump efficiency, for shaft power (a vfd_motor's overload)."
    )
    io: PumpIO

    @model_validator(mode="after")
    def _curve(self) -> Pump:
        pts = self.curve
        if pts is not None and (
            len(pts) != 3
            or pts[0][0] != 0
            or not pts[0][0] < pts[1][0] < pts[2][0]
            or not pts[0][1] > pts[1][1] > pts[2][1]
        ):
            raise ValueError(
                f"{self.id}: curve needs three points of rising flow and falling head, the "
                "first at zero flow"
            )
        return self

    def points_m3s(self) -> list[tuple[float, float]]:
        """The curve's points in m³/s and m (one point: the design point)."""
        pts = self.curve or [(self.design_flow_m3h, self.design_head_m)]
        return [(q / 3600, h) for q, h in pts]

    @property
    def max_flow_m3h(self) -> float:
        """Flow at zero head: the flow port's range."""
        return self.curve[2][0] if self.curve else 2 * self.design_flow_m3h


class HeaterIO(IOModel):
    on: DoTag = port("DO", "Heater on command", required=True)
    power: AoTag | None = port("AO", "Power command", range=(0.0, 100.0), units="%")
    temp: AiTag | None = port("AI", "Tank temperature", range=(0.0, 100.0), units="°C")


class Heater(ComponentBase):
    """Origin: the element's centre. An immersion heater in one tank (PLAN 3.2)."""

    type: Literal["heater"]
    tank: ComponentId = Field(description="The tank it heats.")
    rated_kw: PositiveFloat = Field(6.0, description="Power at 100 %, kW.")
    min_level: float = Field(
        0.3, ge=0, description="Level below which the element is uncovered and adds no heat, m."
    )
    ua_w_per_k: float = Field(15.0, ge=0, description="The tank's heat loss to ambient, W/K.")
    ambient_c: float = Field(20.0, description="Ambient temperature, °C.")
    io: HeaterIO


#: What a transmitter can measure, and its default units (PLAN 3.2).
MEASURE_UNITS = {
    "level": "m",
    "temperature": "°C",
    "fraction": "%",
    "flow": "m³/h",
    "pressure": "bar",
}
#: Which component types offer each quantity.
MEASURE_ON = {
    "level": {"tank"},
    "temperature": {"tank"},
    "fraction": {"tank"},
    "flow": {"pipe", "valve_onoff", "valve_control", "pump"},
    "pressure": {"junction", "drain"},
}


class TransmitterIO(IOModel):
    pv: AiTag = port(
        "AI",
        "Process value",
        required=True,
        units="",
        range_from="range",
        units_from="units_text",
    )


class Transmitter(ComponentBase):
    """Origin: where it is drawn. A 4-20 mA-style transmitter of one process value (PLAN 3.2)."""

    type: Literal["transmitter"]
    measures: Annotated[
        str,
        StringConstraints(
            pattern=r"^[A-Z][A-Z0-9_]*\.(level|temperature|flow|pressure|fraction\.[a-z][a-z0-9_]*)$"
        ),
    ] = Field(
        description="What it reads: TANK.level, TANK.temperature, TANK.fraction.<ingredient>, "
        "LINK.flow, or JUNCTION.pressure (a consumer drain's too)."
    )
    range: tuple[float, float] = Field(description="[lo, hi]: the span of pv (4 to 20 mA).")
    units: Annotated[str, StringConstraints(min_length=1, max_length=16)] | None = Field(
        None, description="Engineering units of pv. Default: m, °C, %, m³/h or bar by quantity."
    )
    lag_s: float = Field(0.0, ge=0, description="First-order filter time constant, s.")
    noise: float = Field(
        0.0, ge=0, description="Standard deviation of random noise, fraction of the span."
    )
    io: TransmitterIO

    @model_validator(mode="after")
    def _span(self) -> Transmitter:
        if not self.range[0] < self.range[1]:
            raise ValueError(f"{self.id}: range [lo, hi] needs lo < hi")
        return self

    def quantity(self) -> str:
        """``level``, ``temperature``, ``fraction``, ``flow`` or ``pressure``."""
        return self.measures.split(".")[1]

    @property
    def units_text(self) -> str:
        return self.units or MEASURE_UNITS[self.quantity()]


#: Process component types (PLAN 3.2): they live in the flow network, not on conveyors.
PROCESS_TYPES = frozenset(
    {
        "transmitter",
        "tank",
        "supply",
        "drain",
        "junction",
        "pipe",
        "valve_onoff",
        "valve_control",
        "pump",
        "heater",
    }
)
#: Process types that are links of the flow network (``from``/``to``).
LINK_TYPES = frozenset({"pipe", "valve_onoff", "valve_control", "pump"})


Component = Annotated[
    BeltConveyor
    | RollerConveyor
    | Stopper
    | Turntable
    | Lift
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
    | CounterDisplay
    | SelectorSwitch
    | Tank
    | Supply
    | Drain
    | Junction
    | Pipe
    | ValveOnOff
    | ValveControl
    | Pump
    | Heater
    | Transmitter,
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
        Lift,
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
        CounterDisplay,
        SelectorSwitch,
        Tank,
        Supply,
        Drain,
        Junction,
        Pipe,
        ValveOnOff,
        ValveControl,
        Pump,
        Heater,
        Transmitter,
    )
}


# --- Composite components (PLAN 2.16) ---------------------------------------------------------


class CompositeIo(StrictModel):
    """One port of a composite type: a command passed `to` a part, a DI/AI read `from` one, or a
    `command` only the composite's `logic:` reads (PLAN 2.5b)."""

    to: str | None = Field(None, description="part.port: the DO/AO this port drives.")
    from_: str | None = Field(
        None,
        alias="from",
        description="part.port (a DI/AI passed through), or a boolean expression over parts' "
        'DI/AI ports, e.g. "gate.raised and not belt.running" (a computed DI).',
    )
    invert: bool = Field(False, description="With `to` on a DO: the part acts on the opposite.")
    command: Literal["DO"] | None = Field(
        None,
        description="A PLC command (DO) read only by the composite's logic: block, by its io "
        "name (PLAN 2.5b); no part port is bound to it.",
    )

    @model_validator(mode="after")
    def _one_direction(self) -> CompositeIo:
        if sum(v is not None for v in (self.to, self.from_, self.command)) != 1:
            raise ValueError("a composite io entry has exactly one of `to`, `from` or `command`")
        return self


class CompositeConnection(StrictModel):
    """A connection between two of a composite's own conveyor parts, by part id (PLAN 2.5b)."""

    from_: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")] = Field(alias="from")
    to: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")]


class CompositeDef(StrictModel):
    """A component type declared in the scene from built-in parts (PLAN 2.16)."""

    type: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)]
    description: str | None = None
    params: dict[Annotated[str, StringConstraints(pattern=r"^[a-z_][a-z0-9_]*$")], float] = Field(
        default_factory=dict, description="Parameter -> default value (numbers only)."
    )
    parts: list[dict[str, Any]] = Field(
        min_length=1,
        description="Components without io. Ids are lowercase and local; numeric fields may be "
        'expressions such as "{length} - 0.2". Positions are relative to the instance.',
        json_schema_extra={
            "items": {
                "type": "object",
                "required": ["id", "type"],
                "properties": {
                    "id": {"type": "string", "pattern": "^[a-z][a-z0-9_]*$"},
                    "type": {"type": "string"},
                    "description": {"type": "string"},
                },
            }
        },
    )
    io: dict[Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")], CompositeIo] = Field(
        default_factory=dict, description="The instance's ports."
    )
    connections: list[CompositeConnection] = Field(
        default_factory=list,
        description="Which of the composite's conveyor parts feeds which (part ids); expanded "
        "into the scene's connections (PLAN 2.5b).",
    )
    logic: dict[
        Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$")], str
    ] = Field(
        default_factory=dict,
        description="part.port (a built-in part's DO) -> a boolean expression over parts' DI/AI "
        "ports and the composite's `command` io names, evaluated each tick before the parts "
        'step (one tick of delay), e.g. z1.run: "not (z1_eye.detect and z2_eye.detect)" '
        "(PLAN 2.5b).",
    )

    @field_validator("parts")
    @classmethod
    def _parts_have_ids(cls, parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        for part in parts:
            pid, ptype = part.get("id"), part.get("type")
            if not isinstance(pid, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", pid):
                raise ValueError(f"part id {pid!r}: lowercase letters, digits and _ (e.g. belt)")
            if not isinstance(ptype, str):
                raise ValueError(f"part {pid}: `type` is required")
            if pid in seen:
                raise ValueError(f"part id {pid!r} is used twice")
            if "io" in part:
                raise ValueError(
                    f"part {pid}: a part has no io of its own; the composite's io block wires it"
                )
            seen.add(pid)
        return parts


class LibraryComposite(StrictModel):
    """A composite type loaded by name from ``_library/<name>.yaml`` beside the scene or in a
    folder above it (PLAN 2.5b)."""

    library: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)] = Field(
        description="Type name; the file _library/<name>.yaml defines it."
    )


class CompositeInstance(StrictModel):
    """A component whose type is a scene composite; parameters are given as extra fields."""

    model_config = ConfigDict(extra="allow")

    id: ComponentId
    type: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")] = Field(
        description="A type from the scene's composites: block.",
        # without this a built-in component would match two oneOf branches in the JSON Schema
        json_schema_extra={"not": {"enum": sorted(COMPONENT_MODELS)}},
    )
    description: Annotated[str, StringConstraints(min_length=1)]
    position: Vec3
    rotation_deg: float = 0.0
    io: dict[str, Any] = Field(default_factory=dict, description="Composite io name -> tag.")

    def params(self) -> dict[str, Any]:
        return dict(self.model_extra or {})


def _component_kind(value: Any) -> str:
    kind = value.get("type") if isinstance(value, dict) else getattr(value, "type", None)
    return kind if kind in COMPONENT_MODELS else "composite"


#: A scene component: a built-in type, or an instance of one of the scene's composites.
SceneComponent = Annotated[
    Annotated[BeltConveyor, Tag("belt_conveyor")]
    | Annotated[RollerConveyor, Tag("roller_conveyor")]
    | Annotated[Stopper, Tag("stopper")]
    | Annotated[Turntable, Tag("turntable")]
    | Annotated[Lift, Tag("lift")]
    | Annotated[DiverterArm, Tag("diverter_arm")]
    | Annotated[PickAndPlace, Tag("pick_and_place")]
    | Annotated[DistanceSensor, Tag("distance_sensor")]
    | Annotated[ColorSensor, Tag("color_sensor")]
    | Annotated[PhotoEye, Tag("photo_eye")]
    | Annotated[Pusher, Tag("pusher")]
    | Annotated[BoxEmitter, Tag("box_emitter")]
    | Annotated[PartRemover, Tag("part_remover")]
    | Annotated[Pushbutton, Tag("pushbutton")]
    | Annotated[PilotLight, Tag("pilot_light")]
    | Annotated[CounterDisplay, Tag("counter_display")]
    | Annotated[SelectorSwitch, Tag("selector_switch")]
    | Annotated[Tank, Tag("tank")]
    | Annotated[Supply, Tag("supply")]
    | Annotated[Drain, Tag("drain")]
    | Annotated[Junction, Tag("junction")]
    | Annotated[Pipe, Tag("pipe")]
    | Annotated[ValveOnOff, Tag("valve_onoff")]
    | Annotated[ValveControl, Tag("valve_control")]
    | Annotated[Pump, Tag("pump")]
    | Annotated[Heater, Tag("heater")]
    | Annotated[Transmitter, Tag("transmitter")]
    | Annotated[CompositeInstance, Tag("composite")],
    Discriminator(_component_kind),
]


#: Component types that carry parts on their top surface (PLAN §5). Connections join these.
CARRIER_TYPES = frozenset({"belt_conveyor", "roller_conveyor", "turntable", "lift"})
#: Conveyors whose deck turns: a connection to or from one is active while the headings agree.
TURNING_TYPES = frozenset({"turntable"})
#: Conveyors whose deck rises and falls: a connection is active while the tops are level.
LIFTING_TYPES = frozenset({"lift"})
#: Conveyors that may feed, and be fed by, several others: the active connection is the aligned
#: (or level) one.
SWITCHING_TYPES = TURNING_TYPES | LIFTING_TYPES


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
    #: The tag's whole description when it is not "<component> — <port>": a composite's
    #: command, described by its instance (PLAN 2.5b).
    tag_description: str | None = None

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
    composites: list[CompositeDef | LibraryComposite] = Field(
        default_factory=list,
        description="Component types declared from built-in parts (PLAN 2.16), or loaded by name "
        "from a _library/ folder ({library: name}, PLAN 2.5b); a component whose type names one "
        "is expanded into its parts at load.",
    )
    components: list[SceneComponent] = Field(min_length=1)
    connections: list[Connection] = Field(
        default_factory=list,
        description="Which conveyor feeds which. Loops (recirculation) are allowed; "
        "each conveyor feeds at most one downstream conveyor, except a turntable or lift, "
        "which may feed several and hands parts across only while its deck is aligned (a "
        "turntable) or level (a lift) with one.",
    )
    checks: Checks | None = Field(None, description="Exercise checks for `openfactory test`.")

    #: Expanded component id -> composite instance id; instance id -> its type (PLAN 2.16).
    _composite_of: dict[str, str] = PrivateAttr(default_factory=dict)
    _instance_type: dict[str, str] = PrivateAttr(default_factory=dict)
    #: Computed DIs of composite instances, after which component each one is listed.
    _computed: dict[str, list[TagBinding]] = PrivateAttr(default_factory=dict)
    #: Library composite files the scene uses (PLAN 2.5b), for bundles.
    _libraries: list[Path] = PrivateAttr(default_factory=list)
    #: Part ports driven by a composite's logic: block (PLAN 2.5b).
    _logic: list[Any] = PrivateAttr(default_factory=list)

    @model_validator(mode="after")
    def _expand_composites(self, info: ValidationInfo) -> Scene:
        """Runs first: every later check sees only built-in components (PLAN 2.16). Library
        composites resolve against ``context["scene_dir"]`` (PLAN 2.5b)."""
        from openfactory.core.composite import expand

        scene_dir = (info.context or {}).get("scene_dir")
        expansion = expand(self, scene_dir)
        self._composite_of = expansion.composite_of
        self._instance_type = expansion.instance_type
        self._libraries = expansion.libraries
        self._logic = expansion.logic
        for after, name, binding, owner in expansion.commands:
            spec = PortSpec(name, "DO", f"{name}: command read by the composite's logic", False,
                            None, None)  # fmt: skip
            command = TagBinding(binding_tag(binding), "DO", after, spec,
                                 binding_address(binding), binding_initial(binding),
                                 tag_description=f"{owner} — {name} command")  # fmt: skip
            self._computed.setdefault(after, []).append(command)
        for after, name, binding, text, local in expansion.computed:
            t = binding_transform(binding)
            refs = parse(text, refs=True).refs
            spec = PortSpec(name, "DI", f"{name}: {local}", False, None, None)
            computed = TagBinding(binding_tag(binding), "DI", refs[0].split(".")[0], spec,
                                  binding_address(binding), None,
                                  replace(t, sources=refs, expr=text),
                                  getattr(binding, "exam", None) == "switch")  # fmt: skip
            self._computed.setdefault(after, []).append(computed)
        return self

    def library_files(self) -> list[Path]:
        """The ``_library/<name>.yaml`` files this scene's composites came from (PLAN 2.5b)."""
        return list(self._libraries)

    def logic_drives(self) -> list[Any]:
        """Part ports driven by composites' logic: blocks (:class:`composite.LogicDrive`)."""
        return list(self._logic)

    def composite_of(self, component_id: str) -> str | None:
        """The composite instance an expanded component came from, or None (PLAN 2.16)."""
        return self._composite_of.get(component_id)

    def instance_type(self, instance_id: str) -> str | None:
        return self._instance_type.get(instance_id)

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
            if types[conn.from_] in SWITCHING_TYPES:
                continue  # a turntable or lift feeds whichever connected conveyor lines up
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
            if sources and not binding.transform.expr:
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
            comp = next(c for c in self.components if c.id == binding.component_id)
            own_spec = instance_ports(comp).get(binding.port.name)
            own = own_spec.units if own_spec else None
            units = getattr(own_spec.value(comp.io), "units", None) if own_spec else None  # type: ignore[attr-defined]
            if units is not None and own and units != own:
                raise ValueError(
                    f"{where}: units {units!r}: {binding.component_id}.{binding.port.name} is in "
                    f"{own}, and a binding cannot convert units (give range in {own})"
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

    @model_validator(mode="after")
    def _link_ends_resolve(self) -> Scene:
        """Every process link's ``from`` and ``to`` names a node of the flow network, a tank's
        nozzle, or another link that names it back (PLAN 3.2, ADR-0005)."""
        by_id = {c.id: c for c in self.components}
        for comp in self.components:
            if comp.type == "transmitter":
                name = comp.measures.split(".")[0]  # type: ignore[attr-defined]
                target, quantity = by_id.get(name), comp.quantity()  # type: ignore[attr-defined]
                if target is None or target.type not in MEASURE_ON[quantity]:
                    what = f"a {target.type}" if target is not None else "no component"
                    offered = ", ".join(sorted(MEASURE_ON[quantity]))
                    raise ValueError(
                        f"{comp.id}.measures: {name} is {what}; {quantity} is measured on a "
                        f"{offered}"
                    )
                if quantity == "pressure" and target.type == "drain" and not target.has_demand():  # type: ignore[attr-defined]
                    raise ValueError(
                        f"{comp.id}.measures: {name} is an open drain, always at atmospheric "
                        "pressure; measure a junction or a consumer drain"
                    )
            if comp.type == "heater":
                tank = by_id.get(comp.tank)  # type: ignore[attr-defined]
                if tank is None or tank.type != "tank":
                    what = f"a {tank.type}" if tank is not None else "no component"
                    raise ValueError(f"{comp.id}.tank: {comp.tank!r} is {what}, not a tank")  # type: ignore[attr-defined]
                if comp.min_level > tank.height:  # type: ignore[attr-defined]
                    raise ValueError(f"{comp.id}: min_level is above {tank.id}'s height")
            if comp.type not in LINK_TYPES:
                continue
            for side, ref in (("from", comp.from_), ("to", comp.to)):  # type: ignore[attr-defined]
                where = f"{comp.id}.{side}"
                name, _, nozzle = ref.partition(".")
                other = by_id.get(name)
                if other is None:
                    raise ValueError(f"{where}: {ref!r} names no component")
                if other is comp:
                    raise ValueError(f"{where}: a link cannot end on itself")
                if other.type == "tank":
                    nozzles = other.all_nozzles()  # type: ignore[attr-defined]
                    if not nozzle:
                        listed = ", ".join(f"{name}.{n}" for n in nozzles)
                        raise ValueError(f"{where}: {name} is a tank; name a nozzle ({listed})")
                    if nozzle not in nozzles:
                        raise ValueError(
                            f"{where}: {name} has no nozzle {nozzle!r} ({', '.join(nozzles)})"
                        )
                    continue
                if nozzle:
                    raise ValueError(f"{where}: {name} is a {other.type}, not a tank: no nozzles")
                if other.type not in PROCESS_TYPES or other.type in ("heater", "transmitter"):
                    raise ValueError(
                        f"{where}: {name} is a {other.type}, not part of the flow network"
                    )
                if other.type in LINK_TYPES:
                    back = other.from_ if side == "to" else other.to  # type: ignore[attr-defined]
                    if back != comp.id:
                        other_side = "from" if side == "to" else "to"
                        raise ValueError(
                            f"{where} names the link {name}, so {name}.{other_side} must name "
                            f"{comp.id} (it names {back!r})"
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
                            binding_port(spec, value),
                            binding_address(value),
                            binding_initial(value),
                            transform,
                            getattr(value, "exam", None) == "switch",
                        )
                    )
            out += self._computed.get(comp.id, [])  # a composite's computed DIs (PLAN 2.16)
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


def parse_scene(text: str, scene_dir: str | Path | None = None) -> Scene:
    """Parse and validate scene YAML text. ``scene_dir`` (the scene file's folder) is where
    library composites are looked up (PLAN 2.5b); without it a scene using one fails."""
    data = yaml.load(text, Loader=_SceneYamlLoader)  # noqa: S506 - SafeLoader subclass
    context = {"scene_dir": Path(scene_dir)} if scene_dir is not None else None
    return Scene.model_validate(data, context=context)


def load_scene(path: str | Path) -> Scene:
    """Load and validate a scene file."""
    return parse_scene(Path(path).read_text(encoding="utf-8"), Path(path).parent)


def scene_json_schema() -> str:
    """The scene JSON Schema as the exact text written to ``web/public/scene.schema.json``.

    Its ``$id`` names the scene format (``SCHEMA_VERSION``), which scenes declare as
    ``openfactory:``; its ``$comment`` also names the package release that wrote it. The two are
    versioned separately (docs/versioning.md): a release that adds only optional keys keeps the
    format, so the comment, not the ``$id``, changes with the package version.
    """
    from openfactory import __version__

    doc = Scene.model_json_schema()
    doc["$comment"] = (
        f"OpenFactory scene format {SCHEMA_VERSION}, exported by OpenFactory {__version__}. "
        "The package version and the scene format version are independent (docs/versioning.md)."
    )
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


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
