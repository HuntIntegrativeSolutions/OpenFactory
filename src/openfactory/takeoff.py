"""The takeoff file and ``openfactory takeoff check`` (PLAN 5.1a; design note §3.2, §3.3).

Behavior spec
-------------
A takeoff (``<plant>.takeoff.yaml``, ``takeoff: "0.1"``) is the reviewed table between a plant's
drawings and a scene: equipment, connections and I/O points, each row with where it was read
(``ref``: sheet and grid zone or list row), how sure the reader was (``confidence``: ``read``,
``inferred`` or ``assumed``) and its open ``questions``. A person checks it against the drawings
before any scene exists, and records that with one file-level ``checked`` mark.

**It is data, never code.** :func:`load` reads at most :data:`MAX_BYTES` with PyYAML's safe
loader (no tags, no objects), and :class:`Takeoff` refuses unknown keys at every level. Nothing
in a takeoff is evaluated: a parameter is a literal value handed on to the scene as written.

**The checked mark** (ruled 2026-11-15, addition A): ``checked: {by, date, drawings:
[<sheet>@<rev>, ...], rows: <digest>}``. :func:`rows_digest` is the first 16 hex characters of
the SHA-256 of ``{"connections", "drawings", "equipment", "io"}`` as YAML loads them, written as
JSON with sorted keys, no spaces and dates as ISO text. Comments, key order and flow or block
style do not change it; any changed value does. The mark is stale when a sheet's ``rev`` is not
the one it names, or when the rows' digest is not its ``rows``; :func:`mark_state` says which.
:func:`write_mark` (``check --mark INITIALS``) writes the mark through the 4.7 ``Document``
writer, replacing only the ``checked`` key (or adding it at the end), so no other byte moves.

**Positions as the drawing gives them** (ruled 2026-11-15, addition B). A layout sheet carries
``frame: {units, datum, rotation_deg}``. An equipment row gives ``layout: {sheet, x, y,
elevation}`` as printed, converted by :func:`layout_position`::

    scene = datum + rotate_y((x·u, elevation·u, -y·u), rotation_deg)

with ``u`` the unit in metres and ``rotate_y`` from :mod:`openfactory.core.geometry`. The row's
scene heading is the frame's ``rotation_deg`` plus its own ``heading_deg`` on the sheet (default
0; an addition of this build, so a belt drawn up a turned sheet keeps its scene heading). A
``mount: {conveyor, sheet, along, rail}`` places a device on a belt's rail by
:func:`openfactory.core.mounts.place_on_rail`, ``along`` in the sheet's units (metres without a
sheet). ``at: [x, y, z]`` in scene metres is for a row on no layout sheet.

**check** (:func:`check`) writes nothing and returns findings, each naming its row and its sheet
(:class:`Finding`): the errors of note §3.3 (schema, duplicates, references, types, ports,
signals, ``plc_tag`` grammar, positions and mounts, an ``along`` past the belt never clamped) and
its two warnings (``type: unknown``; an ``at:`` row whose ``ref`` names a layout sheet). Strict
mode adds the open questions and a missing or stale mark.

Not a safety-rated simulation. OpenFactory is a training and demo tool.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Any, Literal

import yaml
from pydantic import AfterValidator, Field, StringConstraints, ValidationError, field_validator

from openfactory.core.geometry import rotate_y
from openfactory.core.schema import COMPONENT_MODELS, StrictModel, port_specs

TAKEOFF_VERSION = "0.1"
#: The largest takeoff file read, bytes.
MAX_BYTES = 2_000_000
#: A frame's units, in metres.
UNITS = {"mm": 0.001, "m": 1.0, "in": 0.0254, "ft": 0.3048}
#: Where the exported JSON Schema lives, beside the scene's.
JSON_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "web" / "public" / "takeoff.schema.json"
#: Conveyors a ``mount`` may name.
RAIL_CONVEYORS = ("belt_conveyor", "roller_conveyor")
#: Port name -> CODE in a derived scene tag (note §3.2); others use their own name in capitals.
PORT_CODES = {
    "run": "RUN", "running": "RUN", "speed": "SPD", "pv": "PV", "position": "POS",
    "position_fb": "ZT", "hi_sw": "LSH", "lo_sw": "LSL", "extend": "EXT", "extended": "EXT",
    "retracted": "RET", "detect": "",
}  # fmt: skip
TAG_MAX_LEN = 40

#: What a takeoff's text may not hold: C0 and C1 control characters, DEL, and the line and
#: paragraph separators PyYAML also reads as line breaks. Text is one line, so nothing in it can
#: become a line of the scene that ``scaffold`` writes (ruled 2026-11-17).
CONTROL = re.compile("[\x00-\x1f\x7f-\x9f\u2028\u2029]")


def _one_line(value: str) -> str:
    found = CONTROL.search(value)
    if found:
        raise ValueError(
            f"holds the control character U+{ord(found.group()):04X} (a line break, tab or other "
            "control); a takeoff's text is one line"
        )
    return value


#: One line of text: no control character, no line break.
Text = Annotated[str, AfterValidator(_one_line)]

RowId = Annotated[str, StringConstraints(pattern=r"^[A-Z]+-[0-9]+$", max_length=20)]
SheetId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$", max_length=40)]
Confidence = Literal["read", "inferred", "assumed"]
Vec3 = tuple[float, float, float]


class Frame(StrictModel):
    """A layout sheet's frame: its units, its datum in the scene, its rotation (addition B)."""

    units: Literal["mm", "m", "in", "ft"] = Field(description="What dimensions are printed in.")
    datum: Vec3 = Field(description="Where the sheet's datum is, in scene metres.")
    rotation_deg: float = Field(0.0, description="The sheet's axes turned about +y, degrees.")


class Drawing(StrictModel):
    id: SheetId
    kind: Literal["pid", "layout", "io_list", "other"]
    title: Text
    rev: Text | int = Field(description="The sheet's revision.")
    frame: Frame | None = Field(None, description="Layout sheets only (addition B).")


class Checked(StrictModel):
    """The file-level review mark, written by ``takeoff check --mark`` (decision 6, addition A)."""

    by: Annotated[Text, StringConstraints(min_length=1, max_length=40)]
    date: _dt.date | Text
    drawings: list[Text]
    rows: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{16}$")]


class Ref(StrictModel):
    sheet: SheetId
    grid: Text | None = Field(None, description="The zone on a drawing, e.g. C3.")
    row: int | None = Field(None, description="The row on a list.")


class Layout(StrictModel):
    """A position as printed on a layout sheet (addition B)."""

    sheet: SheetId
    x: float = Field(description="To the right on the sheet, from its datum, in its units.")
    y: float = Field(description="Up the sheet, from its datum, in its units.")
    elevation: float = Field(description="Above the datum, in the sheet's units.")
    heading_deg: float = Field(
        0.0, description="The item's direction on the sheet, turned as rotation_deg is."
    )


class Mount(StrictModel):
    """A device on a conveyor's rail (decision 8)."""

    conveyor: Text = Field(description="The conveyor's tag.")
    sheet: SheetId | None = Field(None, description="The layout sheet `along` is printed on.")
    along: float = Field(description="From the infeed end, in the sheet's units (else metres).")
    rail: Text = Field(description="left or right, looking from the infeed.")


class _Row(StrictModel):
    ref: list[Ref] = Field(default_factory=list)
    confidence: Confidence
    questions: list[Text] = Field(default_factory=list)


class Equipment(_Row):
    row: RowId
    tag: Annotated[Text, StringConstraints(min_length=1, max_length=40)]
    id: Text | None = Field(None, description="The scene id, if not the tag with '-' as '_'.")
    type: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")] = Field(
        "unknown", description="A built-in type, or unknown (the default): a stand-in."
    )
    description: Annotated[Text, StringConstraints(min_length=1)]
    params: dict[str, Any] = Field(default_factory=dict)
    layout: Layout | None = None
    mount: Mount | None = None
    at: Vec3 | None = Field(None, description="Scene metres: only for a row on no layout sheet.")


class Connection(_Row):
    row: RowId
    from_: Text = Field(alias="from")
    to: Text
    line: Text | None = None


class IoRow(_Row):
    row: RowId
    equipment: Text
    port: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")]
    signal: Literal["DI", "DO", "AI", "AO"]
    scene_tag: Text | None = None
    plc_tag: Text | None = None
    range: tuple[float, float] | None = None
    units: Text | None = None


class PlantMeta(StrictModel):
    name: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]*$", max_length=64)]
    description: Annotated[Text, StringConstraints(min_length=1)]
    author: Text | None = None


class Takeoff(StrictModel):
    """A takeoff file (PLAN 5.1a)."""

    takeoff: Literal["0.1"]
    plant: PlantMeta
    drawings: list[Drawing]
    checked: Checked | None = None
    equipment: list[Equipment] = Field(default_factory=list)
    connections: list[Connection] = Field(default_factory=list)
    io: list[IoRow] = Field(default_factory=list)

    @field_validator("takeoff", mode="before")
    @classmethod
    def _version_text(cls, value: Any) -> Any:
        return str(value) if isinstance(value, int | float) else value

    def rows(self) -> list[Any]:
        return [*self.equipment, *self.connections, *self.io]

    def sheets(self) -> dict[str, Drawing]:
        return {d.id: d for d in self.drawings}

    def by_tag(self) -> dict[str, Equipment]:
        return {e.tag: e for e in self.equipment}


@dataclass(frozen=True)
class Finding:
    """One finding of ``takeoff check``: its level, its row, its sheet, and what is wrong."""

    level: Literal["error", "warning"]
    row: str
    sheet: str
    message: str

    def __str__(self) -> str:
        word = "ERROR" if self.level == "error" else "WARN "
        return f"{word} {self.row} ({self.sheet}): {self.message}"


@dataclass
class Loaded:
    """A takeoff as read: its path, its text, the YAML data and, when valid, its model."""

    path: Path
    text: str
    data: Any
    model: Takeoff | None
    findings: list[Finding]


# --- loading ----------------------------------------------------------------------------------


class UniqueKeys:
    """A loader mixin that refuses a mapping holding one key twice (PyYAML keeps the last one
    silently) and the merge key ``<<``, naming the key and its line (ruled 2026-11-17)."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> Any:
        first: dict[Any, int] = {}
        for key_node, _ in node.value:
            line = key_node.start_mark.line + 1
            if key_node.tag == "tag:yaml.org,2002:merge":
                raise ValueError(f"line {line}: the merge key << is not allowed")
            key = self.construct_object(key_node, deep=True)  # type: ignore[attr-defined]
            try:
                hash(key)
            except TypeError:
                raise ValueError(f"line {line}: a key must be text or a number") from None
            if key in first:
                raise ValueError(
                    f"line {line}: key {key!r} appears twice in one mapping (first on line "
                    f"{first[key]}); YAML would keep only the last"
                )
            first[key] = line
        return super().construct_mapping(node, deep)  # type: ignore[misc]


class TakeoffLoader(UniqueKeys, yaml.SafeLoader):
    """PyYAML's safe loader with no duplicate key, no merge key and no anchor or alias. A
    takeoff has no use for them, and an alias chain can make the data (and so the digest and the
    validation) far larger than the file. Refused as they are met, before anything expands."""

    def compose_node(self, parent: Any, index: Any) -> Any:
        event = self.peek_event()
        if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None) is not None:
            raise ValueError(
                f"line {event.start_mark.line + 1}: anchors and aliases (& and *) are not "
                "allowed in a takeoff"
            )
        return super().compose_node(parent, index)


def load(path: str | Path) -> Loaded:
    """Read a takeoff: the safe loader (no duplicate keys, anchors or aliases), a size limit,
    the schema. Schema problems become error findings naming the row; nothing in the file is
    evaluated."""
    path = Path(path)
    size = path.stat().st_size
    if size > MAX_BYTES:
        raise ValueError(f"{path}: {size} bytes; a takeoff is at most {MAX_BYTES} bytes")
    text = path.read_bytes().decode("utf-8")  # line endings as written, for --mark
    data = yaml.load(text, Loader=TakeoffLoader)  # noqa: S506 - a SafeLoader subclass
    if not isinstance(data, dict):
        raise ValueError(f"{path}: not a YAML mapping at the top level")
    try:
        return Loaded(path, text, data, Takeoff.model_validate(data), [])
    except ValidationError as exc:
        return Loaded(path, text, data, None, schema_findings(data, exc))


def schema_findings(data: dict[str, Any], exc: ValidationError) -> list[Finding]:
    """One error finding per schema problem, naming the row and its first sheet."""
    out = []
    for err in exc.errors():
        loc = list(err["loc"])
        row, sheet = "file", "no sheet"
        if (
            len(loc) >= 2
            and loc[0] in ("equipment", "connections", "io")
            and isinstance(loc[1], int)
        ):
            items = data.get(loc[0]) or []
            item = items[loc[1]] if loc[1] < len(items) else {}
            item = item if isinstance(item, dict) else {}
            row = str(item.get("row") or f"{loc[0]}[{loc[1]}]")
            sheet = _first_sheet(item)
            loc = loc[2:]
        elif len(loc) >= 2 and loc[0] == "drawings" and isinstance(loc[1], int):
            items = data.get("drawings") or []
            item = items[loc[1]] if loc[1] < len(items) else {}
            row = "drawings"
            sheet = str(item.get("id")) if isinstance(item, dict) and item.get("id") else sheet
            loc = loc[2:]
        where = ".".join(str(p) for p in loc) or "the row"
        out.append(Finding("error", row, sheet, f"{where}: {err['msg']}"))
    return out


def _first_sheet(item: Any) -> str:
    """The sheet a row is best found on: its position's sheet, else its first ref's."""
    if not isinstance(item, dict):
        return "no sheet"
    for key in ("layout", "mount"):
        pos = item.get(key)
        if isinstance(pos, dict) and pos.get("sheet"):
            return str(pos["sheet"])
    refs = item.get("ref") or []
    if refs and isinstance(refs[0], dict) and refs[0].get("sheet"):
        return str(refs[0]["sheet"])
    return "no sheet"


# --- the rows digest and the checked mark ------------------------------------------------------


def _json_default(value: Any) -> str:
    if isinstance(value, _dt.date | _dt.datetime):
        return value.isoformat()
    raise TypeError(f"{type(value).__name__} is not JSON")


def rows_digest(data: dict[str, Any]) -> str:
    """The rows digest of addition A (see the module docstring)."""
    rows = {k: data.get(k) or [] for k in ("connections", "drawings", "equipment", "io")}
    text = json.dumps(
        rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_json_default
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def sheet_revs(model: Takeoff) -> list[str]:
    return [f"{d.id}@{d.rev}" for d in model.drawings]


def mark_state(loaded: Loaded) -> tuple[str, list[str]]:
    """``("missing" | "current" | "stale", reasons)``: why the mark is stale, if it is."""
    model = loaded.model
    assert model is not None
    mark = model.checked
    if mark is None:
        return "missing", []
    reasons = []
    marked = dict(entry.split("@", 1) for entry in mark.drawings if "@" in entry)
    for d in model.drawings:
        if d.id not in marked:
            reasons.append(f"{d.id} was not checked")
        elif marked[d.id] != str(d.rev):
            reasons.append(f"{d.id} is rev {d.rev}, checked at rev {marked[d.id]}")
    for sheet in marked:
        if sheet not in model.sheets():
            reasons.append(f"{sheet} was checked and is no longer listed")
    if rows_digest(loaded.data) != mark.rows:
        reasons.insert(0, "rows changed since checking")
    return ("stale" if reasons else "current"), reasons


def mark_text(loaded: Loaded, initials: str, today: _dt.date | None = None) -> str:
    """The flow mapping ``--mark`` writes as the ``checked`` value."""
    model = loaded.model
    assert model is not None
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9.]{0,9}", initials):
        raise ValueError(f"initials {initials!r}: a letter, then up to nine letters or digits")
    day = (today or _dt.date.today()).isoformat()
    sheets = ", ".join(_scalar(s) for s in sheet_revs(model))
    return (
        f"{{by: {_scalar(initials)}, date: {day}, drawings: [{sheets}], "
        f"rows: {_scalar(rows_digest(loaded.data))}}}"
    )


def _scalar(text: str) -> str:
    """``text`` as a plain YAML scalar when it reads back as itself, else quoted."""
    plain = yaml.safe_load(f"k: {text}") if text and text.strip() == text else None
    if isinstance(plain, dict) and plain.get("k") == text and not re.search(r"[,\[\]{}#:]", text):
        return text
    return json.dumps(text)


def write_mark(loaded: Loaded, initials: str, today: _dt.date | None = None) -> str:
    """The takeoff's text with its ``checked`` mark written (or replaced); nothing else moves."""
    from openfactory.core.migrate import Document

    doc = Document(loaded.text)
    literal = mark_text(loaded, initials, today)
    if doc.has(["checked"]):
        _, node = doc.find(["checked"])
        if isinstance(node, yaml.MappingNode) and not node.flow_style:
            key, _ = doc.find(["checked"])
            assert key is not None
            doc.splice(key.end_mark.index, doc._leaf_end(node), f": {literal}")
        else:
            doc.set_node(["checked"], literal)
    else:
        text = doc.text if doc.text.endswith("\n") else doc.text + "\n"
        doc = Document(text + f"checked: {literal}\n")
    return doc.text


# --- positions ---------------------------------------------------------------------------------


def unit(frame: Frame) -> float:
    return UNITS[frame.units]


def layout_position(frame: Frame, layout: Layout) -> tuple[Vec3, float]:
    """Scene position and heading of a row printed on a layout sheet (addition B)."""
    u = unit(frame)
    local = (layout.x * u, layout.elevation * u, -layout.y * u)
    turned = rotate_y(local, frame.rotation_deg)
    pos = tuple(round(d + t, 9) + 0.0 for d, t in zip(frame.datum, turned, strict=True))
    return pos, _heading(frame.rotation_deg + layout.heading_deg)  # type: ignore[return-value]


def _heading(deg: float) -> float:
    h = (deg + 180.0) % 360.0 - 180.0
    return 0.0 if abs(h) < 1e-9 else round(h, 9)


def scene_id(row: Equipment) -> str:
    return row.id or row.tag.replace("-", "_").upper()


def scene_tag(io: IoRow, by_tag: dict[str, Equipment]) -> str:
    """The I/O row's scene tag: ``scene_tag:``, else ``<ID>_<CODE>_<KIND>`` (note §3.2)."""
    if io.scene_tag:
        return io.scene_tag
    item = by_tag.get(io.equipment)
    ident = scene_id(item) if item else io.equipment.replace("-", "_").upper()
    code = PORT_CODES.get(io.port, io.port.upper())
    return "_".join(p for p in (ident, code, io.signal) if p)


#: Component fields a takeoff row sets itself, never through ``params`` (ruled 2026-11-17).
PLACEMENT = ("id", "type", "description", "position", "rotation_deg", "mesh", "io")


def resolve_tags(value: Any, ids: dict[str, str]) -> Any:
    """A parameter naming another row's tag (``TK-101.level``) names its scene id instead."""
    if isinstance(value, str):
        tag, dot, rest = value.partition(".")
        if tag in ids:
            return ids[tag] + dot + rest
    if isinstance(value, list):
        return [resolve_tags(v, ids) for v in value]
    if isinstance(value, dict):
        return {k: resolve_tags(v, ids) for k, v in value.items()}
    return value


def param_problems(row: Equipment, ids: dict[str, str]) -> list[str]:
    """What is wrong with a known-type row's ``params``, checked against that type's model: a
    placement key, a name the type does not have, a value of the wrong type. A parameter the row
    leaves out is not a problem here (scaffold leaves it to its default, or reports the row
    unplaced when it has none)."""
    model = COMPONENT_MODELS[row.type]
    names = {x for n, info in model.model_fields.items() for x in (n, info.alias or n)}
    out, given = [], {}
    for key, value in row.params.items():
        if key in PLACEMENT:
            out.append(f"params.{key}: set by the row itself, not a parameter")
        elif key not in names:
            out.append(f"params.{key}: a {row.type} has no parameter {key!r}")
        else:
            given[key] = resolve_tags(value, ids)
    candidate = {"id": "TAKEOFF_ROW", "type": row.type, "description": "x",
                 "position": [0.0, 0.0, 0.0], **given}  # fmt: skip
    try:
        model.model_validate(candidate)
    except ValidationError as exc:
        errors = exc.errors()
        missing = any(e["type"] == "missing" for e in errors)
        for e in errors:
            loc = [str(p) for p in e["loc"]]
            if e["type"] == "missing" or (loc and loc[0] not in given) or (not loc and missing):
                continue
            out.append(f"params.{'.'.join(loc) or row.type}: {e['msg']}")
    return out


def _model_default(type_name: str, field: str) -> Any:
    info = COMPONENT_MODELS[type_name].model_fields.get(field)
    return None if info is None else info.default


def conveyor_config(row: Equipment, model: Takeoff) -> Any:
    """What ``place_on_rail`` needs of a conveyor row, as a plain object."""
    pos, heading = position_of(row, model)
    params = row.params

    def value(name: str) -> Any:
        return params.get(name, _model_default(row.type, name))

    return SimpleNamespace(
        id=scene_id(row), type=row.type, position=list(pos), rotation_deg=heading,
        length=float(value("length")), width=float(value("width")),
    )  # fmt: skip


def mount_along_m(mount: Mount, model: Takeoff) -> float:
    sheet = model.sheets().get(mount.sheet) if mount.sheet else None
    return mount.along * (unit(sheet.frame) if sheet and sheet.frame else 1.0)


def tallest_part(model: Takeoff) -> float | None:
    """The tallest part any emitter in the takeoff makes (a pusher's plate height)."""
    heights = [
        float(p["size"][1])
        for e in model.equipment
        if e.type == "box_emitter"
        for p in e.params.get("parts") or []
        if isinstance(p, dict) and isinstance(p.get("size"), list) and len(p["size"]) == 3
    ]
    return max(heights) if heights else None


def position_of(row: Equipment, model: Takeoff) -> tuple[Vec3, float]:
    """A row's scene position and heading, from its layout, mount or ``at``. ValueError when
    it has none or its sheet cannot convert it (``check`` reports those first)."""
    from openfactory.core.mounts import place_on_rail

    sheets = model.sheets()
    if row.layout is not None:
        frame = sheets[row.layout.sheet].frame
        if frame is None:
            raise ValueError(f"{row.layout.sheet} has no frame")
        return layout_position(frame, row.layout)
    if row.mount is not None:
        belt = model.by_tag()[row.mount.conveyor]
        conveyor = conveyor_config(belt, model)
        along = mount_along_m(row.mount, model)
        if not -1e-9 <= along <= conveyor.length + 1e-9:
            raise ValueError(f"along {along:g} m is outside {belt.tag} (0 to {conveyor.length} m)")
        placed = place_on_rail(
            row.type, dict(row.params), conveyor, f"rail_{row.mount.rail}", along,
            tallest_part(model),
        )  # fmt: skip
        return tuple(placed.position), placed.rotation_deg  # type: ignore[return-value]
    if row.at is not None:
        return tuple(float(v) for v in row.at), 0.0  # type: ignore[return-value]
    raise ValueError("no layout, mount or at")


def position_source(row: Equipment) -> str:
    """Where a row's position comes from, for the scaffold's positions line."""
    if row.layout is not None:
        return f"layout {row.layout.sheet}"
    if row.mount is not None:
        return f"mount {row.mount.conveyor} {row.mount.rail}"
    return "at" if row.at is not None else "none"


# --- check -------------------------------------------------------------------------------------


def type_ports(type_name: str) -> dict[str, str] | None:
    """Port name -> kind for a built-in type; None for ``unknown`` (a stand-in takes any)."""
    if type_name not in COMPONENT_MODELS:
        return None
    return {
        n: s.kind
        for n, s in port_specs(COMPONENT_MODELS[type_name].model_fields["io"].annotation).items()
    }  # noqa: E501


def link_ports(row: Equipment) -> tuple[str, ...]:
    """The ports a connection may name on a row: pipe ports (4.1g2) or a tank's nozzles."""
    from openfactory.core.snaps import PIPE_PORT_NAMES

    if row.type == "tank":
        return ("bottom", "top", *(row.params.get("nozzles") or {}))
    return PIPE_PORT_NAMES.get(row.type, ())


def check(loaded: Loaded) -> list[Finding]:
    """Every error and warning of note §3.3, each naming its row and its sheet."""
    if loaded.model is None:
        return list(loaded.findings)
    from openfactory.core.mounts import MOUNT_TYPES
    from openfactory.core.plctag import parse_plc_tag, plc_tag_key

    model = loaded.model
    out: list[Finding] = []
    sheets = model.sheets()
    by_tag = model.by_tag()

    def err(row: Any, message: str, sheet: str | None = None) -> None:
        out.append(Finding("error", row.row, sheet or _row_sheet(row), message))

    def warn(row: Any, message: str, sheet: str | None = None) -> None:
        out.append(Finding("warning", row.row, sheet or _row_sheet(row), message))

    for d in model.drawings:  # a layout sheet with no frame is an error only when used (below)
        if d.frame is not None and d.kind != "layout":
            out.append(Finding("error", "drawings", d.id, "a frame is for a layout sheet only"))
    seen_ids: dict[str, str] = {}
    for d in model.drawings:
        if d.id in seen_ids:
            out.append(Finding("error", "drawings", d.id, f"sheet {d.id} is listed twice"))
        seen_ids[d.id] = d.id

    def duplicates(rows: list[Any], name: Any, what: str, fold: Any = None) -> None:
        first: dict[str, tuple[str, str]] = {}
        for r in rows:
            n = name(r)
            if n is None:
                continue
            k = fold(n) if fold else n
            if k in first:
                row, was = first[k]
                case = "" if was == n else f", {was!r} differing only in case"
                err(r, f"duplicate {what} {n!r} (also row {row}{case})")
            else:
                first[k] = (r.row, n)

    duplicates(model.rows(), lambda r: r.row, "row")
    duplicates(model.equipment, lambda r: r.tag, "tag")
    duplicates(model.equipment, scene_id, "scene id")
    duplicates(model.io, lambda r: scene_tag(r, by_tag), "scene tag")
    # tag names are not case sensitive (1756-PM004L-EN-P, ch. 2, Guidelines for tags)
    duplicates(model.io, lambda r: r.plc_tag, "plc_tag", plc_tag_key)

    for r in model.rows():
        for ref in r.ref:
            if ref.sheet not in sheets:
                err(r, f"ref names sheet {ref.sheet}, which is not in drawings", ref.sheet)

    ids = {e.tag: scene_id(e) for e in model.equipment}
    bad_params: set[str] = set()
    for e in model.equipment:
        if e.type in COMPONENT_MODELS:
            for problem in param_problems(e, ids):
                err(e, problem)
                bad_params.add(e.tag)

    for e in model.equipment:
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", scene_id(e)) or len(scene_id(e)) > TAG_MAX_LEN:
            err(e, f"scene id {scene_id(e)!r} is not capitals, digits and _ (give `id:`)")
        if e.type == "unknown":
            warn(e, f"{e.tag} has type unknown: scaffold places a stand-in carrying its I/O")
        elif e.type not in COMPONENT_MODELS:
            err(e, f"unknown type {e.type!r}; write `unknown` for an item with no type yet")
        forms = [k for k in ("layout", "mount", "at") if getattr(e, k) is not None]
        if len(forms) > 1:
            err(e, f"more than one position: {', '.join(forms)}; give one")
            continue
        if e.layout is not None:
            sheet = sheets.get(e.layout.sheet)
            if sheet is None:
                err(e, f"layout names sheet {e.layout.sheet}, which is not in drawings")
            elif sheet.frame is None:
                err(e, f"layout sheet {sheet.id} has no frame (units, datum, rotation_deg)")
        if e.mount is not None and e.tag not in bad_params:
            _check_mount(e, model, err, MOUNT_TYPES, bad_params)
        if e.at is not None and any(sheets.get(r.sheet) and sheets[r.sheet].kind == "layout"
                                    for r in e.ref):  # fmt: skip
            warn(e, "at: is in scene metres, yet ref names a layout sheet: give layout: as "
                    "printed", next(r.sheet for r in e.ref if sheets.get(r.sheet)
                                    and sheets[r.sheet].kind == "layout"))  # fmt: skip

    for c in model.connections:
        for end in (c.from_, c.to):
            tag, _, port = end.partition(".")
            item = by_tag.get(tag)
            if item is None:
                err(c, f"connection end {end!r}: {tag} is not an equipment tag")
                continue
            if port and item.type in COMPONENT_MODELS and port not in link_ports(item):
                ports = ", ".join(link_ports(item)) or "none"
                err(c, f"connection end {end!r}: a {item.type} has no port {port!r} ({ports})")

    for io in model.io:
        item = by_tag.get(io.equipment)
        if item is None:
            err(io, f"I/O row on {io.equipment}, which is not an equipment tag")
            continue
        ports = type_ports(item.type)
        if ports is not None:
            if io.port not in ports:
                err(io, f"a {item.type} has no port {io.port!r} ({', '.join(ports) or 'none'})")
            elif ports[io.port] != io.signal:
                err(io, f"{io.equipment}.{io.port} is a {ports[io.port]} port, not {io.signal}")
        if io.signal in ("DI", "DO") and (io.range is not None or io.units is not None):
            err(io, "range and units are for an analog signal")
        if io.range is not None and not io.range[0] < io.range[1]:
            err(io, "range [lo, hi] needs lo < hi")
        if io.plc_tag is not None:
            try:
                parse_plc_tag(io.plc_tag)
            except ValueError as exc:
                err(io, str(exc))
        tag = scene_tag(io, by_tag)
        if len(tag) > TAG_MAX_LEN or not re.fullmatch(rf"[A-Z][A-Z0-9_]*_{io.signal}", tag):
            err(io, f"scene tag {tag!r} is not a valid tag (capitals, at most 40 characters, "
                    f"ending _{io.signal}); give scene_tag:")  # fmt: skip

    if not any(f.level == "error" for f in out):  # the pipes scaffold would make (ruled 2026-11-17)
        from openfactory.scaffold import plan_connections

        rows_by_id = {scene_id(e): e for e in model.equipment}
        for pipe in plan_connections(model).pipes:
            clash = rows_by_id.get(pipe["id"])
            if clash is not None:
                c = next(r for r in model.connections if r.row == pipe["row"])
                err(c, f"scaffold makes this line pipe {pipe['id']}, which is also the scene id "
                       f"of {clash.row} {clash.tag}; give {clash.row} another `id:`")  # fmt: skip
    return out


def _row_sheet(row: Any) -> str:
    for key in ("layout", "mount"):
        pos = getattr(row, key, None)
        if pos is not None and getattr(pos, "sheet", None):
            return str(pos.sheet)
    return row.ref[0].sheet if row.ref else "no sheet"


def _check_mount(
    e: Equipment, model: Takeoff, err: Any, mount_types: Any, bad_params: set[str]
) -> None:
    m = e.mount
    assert m is not None
    sheets = model.sheets()
    if m.sheet is not None:
        sheet = sheets.get(m.sheet)
        if sheet is None:
            err(e, f"mount names sheet {m.sheet}, which is not in drawings")
            return
        if sheet.frame is None:
            err(e, f"mount sheet {m.sheet} has no frame, so its units are unknown")
            return
    if m.rail not in ("left", "right"):
        err(e, f"rail {m.rail!r}: left or right, looking from the infeed")
    if e.type in COMPONENT_MODELS and e.type not in mount_types:
        err(e, f"a {e.type} is not placed on a rail ({', '.join(sorted(mount_types))} are)")
    belt = model.by_tag().get(m.conveyor)
    if belt is None or belt.type not in RAIL_CONVEYORS:
        what = f"a {belt.type}" if belt else "not an equipment tag"
        err(e, f"mount conveyor {m.conveyor} is {what}, not a belt or roller conveyor")
        return
    if belt.mount is not None:
        err(e, f"mount conveyor {m.conveyor} has no position of its own")
        return
    # a belt with no position at all is laid out (PLAN 5.5), and the device follows it
    if belt.tag in bad_params:
        return  # its params are reported on its own row
    length = float(belt.params.get("length", _model_default(belt.type, "length")))
    along = mount_along_m(m, model)
    if not -1e-9 <= along <= length + 1e-9:
        printed = f"{m.along:g}" + (f" {sheets[m.sheet].frame.units}" if m.sheet else " m")  # type: ignore[union-attr]
        err(e, f"along {printed} is {along:g} m, outside {m.conveyor} (0 to {length:g} m); "
               "it is not clamped")  # fmt: skip


@dataclass
class Report:
    """What ``takeoff check`` prints and whether it passes."""

    findings: list[Finding]
    mark: str
    reasons: list[str]
    questions: list[tuple[str, str, str]]  # row, sheet, text
    by_confidence: dict[str, int]
    counts: tuple[int, int, int]  # equipment, connections, io

    @property
    def errors(self) -> int:
        return sum(f.level == "error" for f in self.findings)

    def strict_failures(self) -> list[str]:
        out = []
        if self.questions:
            rows = sorted({q[0] for q in self.questions}, key=_row_key)
            out.append(f"{len(rows)} rows have open questions: {', '.join(rows)}")
        if self.mark == "missing":
            out.append("checked: missing; a person checks the takeoff, then --mark INITIALS")
        elif self.mark == "stale":
            out.append("checked: stale, " + "; ".join(self.reasons))
        return out


def _row_key(row: str) -> tuple[str, int]:
    kind, _, n = row.partition("-")
    return (kind, int(n) if n.isdigit() else 0)


def report(loaded: Loaded) -> Report:
    findings = check(loaded)
    model = loaded.model
    if model is None:
        return Report(findings, "missing", [], [], {}, (0, 0, 0))
    mark, reasons = mark_state(loaded)
    questions = [(r.row, _row_sheet(r), q) for r in model.rows() for q in r.questions]
    conf = {c: 0 for c in ("read", "inferred", "assumed")}
    for r in model.rows():
        conf[r.confidence] += 1
    counts = (len(model.equipment), len(model.connections), len(model.io))
    return Report(findings, mark, reasons, questions, conf, counts)


def format_report(loaded: Loaded, rep: Report, *, strict: bool) -> str:
    lines = []
    e, c, i = rep.counts
    lines.append(f"{loaded.path.name}: {e + c + i} rows ({e} equipment, {c} connections, {i} I/O)")
    if rep.by_confidence:
        lines.append("  confidence: " + ", ".join(f"{n} {k}" for k, n in rep.by_confidence.items()))
    for f in rep.findings:
        lines.append(f"  {f}")
    if rep.questions:
        lines.append(f"  open questions ({len({q[0] for q in rep.questions})} rows):")
        for row, sheet, text in rep.questions:
            lines.append(f"    {row} ({sheet}): {text}")
    if loaded.model is not None:
        if rep.mark == "missing":
            lines.append("checked: missing")
        elif rep.mark == "current":
            m = loaded.model.checked
            assert m is not None
            lines.append(f"checked: current ({m.by}, {m.date})")
        else:
            lines.append("checked: stale, " + "; ".join(rep.reasons))
    warnings = len(rep.findings) - rep.errors
    if rep.errors:
        lines.append(f"FAIL: {rep.errors} error(s), {warnings} warning(s)")
    elif strict and rep.strict_failures():
        for failure in rep.strict_failures():
            lines.append(f"FAIL (--strict): {failure}")
    else:
        lines.append(f"OK{' (--strict)' if strict else ''}: 0 errors, {warnings} warning(s)")
    return "\n".join(lines) + "\n"


def json_schema() -> dict[str, Any]:
    schema = Takeoff.model_json_schema(by_alias=True)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "OpenFactory takeoff file"
    schema["description"] = (
        f'A takeoff file, takeoff: "{TAKEOFF_VERSION}" (PLAN 5.1; docs/takeoff.md). Training and '
        "demo tool, not safety-rated."
    )
    return schema


def json_schema_text() -> str:
    return json.dumps(json_schema(), indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    """``python -m openfactory.takeoff [--check]``: write (or check) the takeoff JSON Schema."""
    import sys

    args = sys.argv[1:] if argv is None else argv
    text = json_schema_text()
    if "--check" in args:
        if not JSON_SCHEMA_PATH.is_file() or JSON_SCHEMA_PATH.read_text("utf-8") != text:
            print(f"{JSON_SCHEMA_PATH} is not current; run: uv run python -m openfactory.takeoff",
                  file=sys.stderr)  # fmt: skip
            return 1
        return 0
    JSON_SCHEMA_PATH.write_text(text, encoding="utf-8")
    print(f"wrote {JSON_SCHEMA_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
