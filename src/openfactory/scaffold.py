"""``openfactory takeoff scaffold`` (PLAN 5.1b; design note §3.4): a scene skeleton from a takeoff.

Behavior spec
-------------
:func:`scaffold` turns a takeoff that passes ``takeoff check`` into scene text and a report. It
refuses a takeoff with any error and writes nothing. **Every row has exactly one outcome**, and
the outcomes add up to the takeoff's rows (the report prints the sum, and a test checks it):

- **placed**: a known type with what it needs, at its position; a connection made; an I/O row
  bound to its port, as ``{tag, plc_tag}`` with the row's ``range`` and ``units``;
- **assumed**: a row marked ``assumed`` (an equipment, connection or I/O row). A parameter the
  row leaves out that has a documented default is left out of the scene too, so the type's
  default applies; it is not marked, and does not make the row assumed;
- **stand-in**: ``type: unknown``: a local composite ``standin_<id>`` in the scene's
  ``composites:``, a ``scenery`` body sized from ``params.length/width/height`` (else 1 m) whose
  io are exactly the row's I/O rows (each DO and AO a command; the first DI follows the first
  DO, the first AI the first AO; any other DI reads false and any other AI the low end of its
  range); its description starts ``PLACEHOLDER``. Each of its I/O rows is a stand-in too;
- **unplaced**: no position (no ``layout``, ``mount`` or ``at``), or a parameter with no default
  missing: written in a holding row past the plant's far edge, 1.5 m apart, until 5.5 lays it
  out (with no default, the parameter is left for a person, and the scene will not load);
- **not connected**: a connection scaffold cannot make (a conveyor to a tank), written as a
  comment at the end of the scene.

A row's outcome is the first that applies: unplaced or not connected, then stand-in (the I/O
rows of a stand-in too), then assumed, then placed.

**Nothing in a takeoff becomes a line of the scene by itself** (ruled 2026-11-17): every comment
is written through :func:`comment`, which turns any control character or line break into a
space, and every value through :func:`flow`, which is one line or an error; the takeoff's text
is one line already (``takeoff.Text``). Before returning, the scene text is loaded as a scene
(duplicate keys refused, then the scene loader and :func:`~openfactory.core.loader.build`)
unless a row is unplaced for a parameter with no default, which a person must give first. A
scene that does not load is a bug in scaffold: ValueError, and nothing is written.

Connections (:func:`plan_connections`, which ``takeoff check`` also runs to find a pipe id that
is an equipment row's scene id): two conveyors become a ``connections:`` entry. A connection
into a valve or pump sets that link's ``from``; a connection out of a link to a node (tank
nozzle, supply, drain, junction), or between two nodes, becomes a ``pipe`` (``PIPE_101``,
``PIPE_102``, ... in row order) whose diameter is the line number's leading size in millimetres
(``25-W-101``: 25 mm), and the link's ``to`` names the pipe.

Positions come from :func:`openfactory.takeoff.position_of`: a layout sheet's dimensions
converted, ``place_on_rail`` for a mount (a stand-in on a rail is placed as a photo eye would
be), or ``at`` in scene metres. Each component's comment names its row and sheet; each open
question is a ``# QUESTION (<row>): <text>`` comment above the component it is about. The text
depends only on the takeoff, so two runs write the same bytes. The scene has no ``checks:`` and
no stand-in script: those come from the plant's sequence of operations, which a takeoff does not
hold.

Exit codes of the command: 0 when every row is placed with no stand-in, no assumption and no
question; 3 when the scene was written with rows to resolve; 1 when the takeoff is invalid (or
the output exists without ``--force``, or lies in the project's ``scenes/`` without
``--in-scenes``), and nothing was written.

Not a safety-rated simulation. OpenFactory is a training and demo tool.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import yaml

from openfactory.core.schema import COMPONENT_MODELS
from openfactory.core.schema import _SceneYamlLoader as _SceneLoaderBase
from openfactory.takeoff import (
    CONTROL,
    PLACEMENT,
    RAIL_CONVEYORS,
    Equipment,
    IoRow,
    Loaded,
    Takeoff,
    UniqueKeys,
    check,
    position_of,
    resolve_tags,
    scene_id,
    scene_tag,
)

OUTCOMES = ("placed", "assumed", "stand-in", "unplaced", "not connected")
NODE_TYPES = ("tank", "supply", "drain", "junction")
LINK_TYPES = ("pipe", "valve_onoff", "valve_control", "pump")
CONVEYOR_LIKE = (*RAIL_CONVEYORS, "turntable", "lift")
HOLDING_GAP_M = 1.5


@dataclass
class Outcome:
    row: str
    outcome: str
    note: str = ""
    #: Unplaced because a parameter with no default is missing: the scene cannot load yet.
    missing: bool = False


def comment(text: str) -> str:
    """``# text`` on one line: every control character or line break in it becomes a space, so
    a comment can never end early and start a line of YAML (ruled 2026-11-17)."""
    return "# " + CONTROL.sub(" ", text)


@dataclass
class Scaffold:
    """The scene text and the report."""

    text: str
    outcomes: list[Outcome]
    positions: dict[str, list[str]]  # source -> rows
    questions: list[str]  # rows with open questions
    source_name: str = ""
    scene_name: str = ""
    counts: tuple[int, int, int] = (0, 0, 0)

    def tally(self) -> dict[str, list[Outcome]]:
        out: dict[str, list[Outcome]] = {k: [] for k in OUTCOMES}
        for o in self.outcomes:
            out[o.outcome].append(o)
        return out

    def exit_code(self) -> int:
        clean = all(o.outcome == "placed" for o in self.outcomes) and not self.questions
        return 0 if clean else 3

    def report(self) -> str:
        e, c, i = self.counts
        total = e + c + i
        tally = self.tally()
        lines = [
            f"{self.source_name} -> {self.scene_name}: {total} rows ({e} equipment, {c} "
            f"connections, {i} I/O)"
        ]
        for name in OUTCOMES:
            rows = tally[name]
            detail = "; ".join(f"{o.row}{' ' + o.note if o.note else ''}" for o in rows)
            if name == "placed":
                detail = ""
            lines.append(f"  {name:<14}{len(rows):>3}   {detail}".rstrip())
        parts = " + ".join(str(len(tally[n])) for n in OUTCOMES)
        n = len(self.outcomes)
        if n == total:
            lines.append(f"  {parts} = {n}: every row accounted for")
        else:
            lines.append(f"  {parts} = {n}, but the takeoff has {total} rows")
        where = [f"{len(rows)} {src} ({', '.join(rows)})" for src, rows in self.positions.items()]
        lines.append("positions: " + "; ".join(where))
        lines.append(
            f"open questions, written as # QUESTION comments ({len(self.questions)}): "
            + (", ".join(self.questions) or "none")
        )
        return "\n".join(lines) + "\n"


def flow(value: Any) -> str:
    """A value as one line of flow YAML."""
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        value = float(value)
    text = yaml.safe_dump(value, default_flow_style=True, width=10**9, sort_keys=False)
    text = text.strip()
    text = text[: -len("...")].strip() if text.endswith("...") else text
    if "\n" in text or "\r" in text:
        raise ValueError(f"{value!r} does not fit on one line of the scene")
    return text


def _num(v: float) -> float:
    r = round(float(v), 4)
    return 0.0 if r == 0 else r


def _sheet_note(row: Equipment) -> str:
    refs = [f"{r.sheet} {r.grid}" if r.grid else r.sheet for r in row.ref]
    return ", ".join(refs)


def _missing(row: Equipment, given: set[str]) -> list[str]:
    """The type's parameters with no documented default that neither the row nor scaffold
    gives (a parameter with a default is left to it, as in any scene)."""
    model = COMPONENT_MODELS[row.type]
    return [
        info.alias or name
        for name, info in model.model_fields.items()
        if name not in PLACEMENT and info.is_required() and not {name, info.alias or name} & given
    ]


def _line_diameter(line: str | None) -> float | None:
    m = re.match(r"^(\d+)-", line or "")
    return int(m.group(1)) / 1000 if m else None


@dataclass
class Plan:
    """What the connection rows become (:func:`plan_connections`)."""

    links: dict[str, dict[str, str]]  # link tag -> {"from": ref, "to": ref}
    pipes: list[dict[str, Any]]  # id, row, line, from, to, a, b (tags), sheet, diameter
    conveyor_links: list[tuple[str, str, str]]  # from id, to id, row
    unconnected: list[tuple[Any, str]]  # connection row, why
    outcomes: dict[str, Outcome]


def plan_connections(model: Takeoff) -> Plan:
    """The scene's links, pipes and conveyor connections from a takeoff's connection rows, and
    each row's outcome (assumed for an ``assumed`` row that is made)."""
    ids = {e.tag: scene_id(e) for e in model.equipment}
    by_tag = model.by_tag()
    plan = Plan({}, [], [], [], {})
    links = plan.links

    def end(text: str) -> tuple[Equipment, str]:
        tag, _, port = text.partition(".")
        return by_tag[tag], port

    def made(c: Any) -> Outcome:
        if c.confidence == "assumed":
            return Outcome(c.row, "assumed", f"{c.from_} -> {c.to}")
        return Outcome(c.row, "placed")

    for c in model.connections:
        a, ap = end(c.from_)
        b, bp = end(c.to)
        a_ref = ids[a.tag] + (f".{ap}" if ap and a.type == "tank" else "")
        b_ref = ids[b.tag] + (f".{bp}" if bp and b.type == "tank" else "")
        if a.type in CONVEYOR_LIKE and b.type in CONVEYOR_LIKE:
            plan.conveyor_links.append((ids[a.tag], ids[b.tag], c.row))
            plan.outcomes[c.row] = made(c)
            continue
        process = NODE_TYPES + LINK_TYPES
        if a.type not in process or b.type not in process:
            why = f"a {a.type} to a {b.type} is not a link of the flow network or a conveyor"
            plan.unconnected.append((c, why))
            plan.outcomes[c.row] = Outcome(c.row, "not connected", f"({why})")
            continue
        if b.type in LINK_TYPES and "from" not in links.get(b.tag, {}):
            links.setdefault(b.tag, {})["from"] = a_ref
            if a.type in LINK_TYPES:
                links.setdefault(a.tag, {}).setdefault("to", ids[b.tag])
            plan.outcomes[c.row] = made(c)
            continue
        if a.type in LINK_TYPES and "to" in links.get(a.tag, {}):
            why = f"{a.tag} already has its downstream end"
            plan.unconnected.append((c, why))
            plan.outcomes[c.row] = Outcome(c.row, "not connected", f"({why})")
            continue
        pid = f"PIPE_{101 + len(plan.pipes)}"
        plan.pipes.append({
            "id": pid, "row": c.row, "line": c.line, "from": a_ref, "to": b_ref,
            "a": a.tag, "b": b.tag, "diameter": _line_diameter(c.line), "sheet": _sheet_note(c),
        })  # fmt: skip
        if a.type in LINK_TYPES:
            links.setdefault(a.tag, {})["to"] = pid
        plan.outcomes[c.row] = made(c)
    return plan


class _SceneKeys(UniqueKeys, _SceneLoaderBase):  # type: ignore[misc, valid-type]
    """The scene loader's YAML reading, refusing a key written twice."""


def self_load(text: str) -> None:
    """Load scaffold's own output as a scene; any exception is a bug in scaffold."""
    from openfactory.core.loader import build
    from openfactory.core.schema import parse_scene

    yaml.load(text, Loader=_SceneKeys)  # noqa: S506 - a SafeLoader subclass; duplicate keys
    build(parse_scene(text))


def scaffold(loaded: Loaded) -> Scaffold:
    """The scene and report for a takeoff that passes check; ValueError otherwise, and when the
    scene written would not load (a bug in scaffold)."""
    errors = [f for f in check(loaded) if f.level == "error"]
    if errors or loaded.model is None:
        raise ValueError(
            "the takeoff does not pass check: " + "; ".join(str(f) for f in errors[:5])
        )
    model: Takeoff = loaded.model
    ids = {e.tag: scene_id(e) for e in model.equipment}
    io_of: dict[str, list[IoRow]] = {}
    for io in model.io:
        io_of.setdefault(io.equipment, []).append(io)
    positions: dict[str, list[str]] = {}
    questions = [r.row for r in model.rows() if r.questions]
    q_of: dict[str, list[tuple[str, str]]] = {}
    for r in model.rows():
        target = r.tag if isinstance(r, Equipment) else getattr(r, "equipment", None)
        for q in r.questions:
            q_of.setdefault(target or "", []).append((r.row, q))

    # positions
    placed_pos: dict[str, tuple[tuple[float, float, float], float]] = {}
    unplaced: list[Equipment] = []
    for e in model.equipment:
        try:
            if e.type == "unknown" and e.mount is not None:
                stand = e.model_copy(update={"type": "photo_eye"})
                placed_pos[e.tag] = position_of(stand, model)
            else:
                placed_pos[e.tag] = position_of(e, model)
        except ValueError:
            unplaced.append(e)
            continue
        if e.layout is not None:
            src = f"from {e.layout.sheet} dimensions"
        elif e.mount is not None:
            sheet = f" ({e.mount.sheet})" if e.mount.sheet else ""
            src = f"on {e.mount.conveyor}'s {e.mount.rail} rail{sheet}"
        else:
            src = "in scene metres with no layout sheet"
        positions.setdefault(src, []).append(f"{e.row} {e.tag}" if e.at is not None else e.row)
    far = max((p[0][0] for p in placed_pos.values()), default=0.0)
    for k, e in enumerate(unplaced):
        placed_pos[e.tag] = ((_num(far + HOLDING_GAP_M * (k + 1)), 0.0, 0.0), 0.0)
        positions.setdefault("in the holding row (no position)", []).append(e.row)
    unplaced_tags = {u.tag for u in unplaced}

    # connections
    plan = plan_connections(model)
    links, pipes = plan.links, plan.pipes
    outcomes: dict[str, Outcome] = dict(plan.outcomes)
    for pipe in pipes:
        (pa, _), (pb, _) = placed_pos[pipe["a"]], placed_pos[pipe["b"]]
        pipe["position"] = [_num((x + y) / 2) for x, y in zip(pa, pb, strict=True)]

    # components
    out: list[str] = []
    composites: list[str] = []
    for e in model.equipment:
        sid = ids[e.tag]
        pos, heading = placed_pos[e.tag]
        rows_io = io_of.get(e.tag, [])
        for row, q in q_of.get(e.tag, []):
            out.append("  " + comment(f"QUESTION ({row}): {q}"))
        if e.type == "unknown":
            ctype = f"standin_{sid.lower()}"
            composites += _standin(ctype, e, rows_io)
            out.append(f"  - id: {sid}  " + comment(
                f"PLACEHOLDER: takeoff {e.row} {e.tag} ({_sheet_note(e) or 'no sheet'}), "
                "type unknown"))  # fmt: skip
            out.append(f"    type: {ctype}")
            out.append(f"    description: {flow('PLACEHOLDER ' + e.description)}")
            out.append(f"    position: {flow([_num(v) for v in pos])}")
            if heading:
                out.append(f"    rotation_deg: {flow(_num(heading))}")
            for name in ("length", "width", "height"):
                if name in e.params:
                    out.append(f"    {name}: {flow(e.params[name])}")
            out += _io_lines(e, rows_io, model, standin=True)
            outcomes[e.row] = Outcome(e.row, "stand-in", f"{e.tag} (type unknown: {ctype})")
            for io in rows_io:
                outcomes[io.row] = Outcome(io.row, "stand-in", f"-> {io.port}")
            continue
        missing = _missing(e, set(e.params) | set(links.get(e.tag, {})))
        note = _sheet_note(e) or "no sheet"
        if e.mount is not None:
            note = (f"on {e.mount.conveyor}, {_num(_along(e, model))} m from the infeed, "
                    f"{e.mount.rail} rail")  # fmt: skip
        assumed = e.confidence == "assumed"
        mark = ", assumed" if assumed else ""
        out.append(f"  - id: {sid}  " + comment(f"takeoff {e.row} {e.tag} ({note}{mark})"))
        out.append(f"    type: {e.type}")
        out.append(f"    description: {flow(e.description)}")
        out.append(f"    position: {flow([_num(v) for v in pos])}")
        if heading:
            out.append(f"    rotation_deg: {flow(_num(heading))}")
        for key, val in links.get(e.tag, {}).items():
            out.append(f"    {key}: {flow(val)}")
        for key, val in e.params.items():
            out.append(f"    {key}: {flow(resolve_tags(val, ids))}")
        for name in missing:
            out.append("    " + comment(f"{name}: no default; give it from the drawing "
                                        f"(takeoff {e.row})"))  # fmt: skip
        out += _io_lines(e, rows_io, model, standin=False)
        if e.tag in unplaced_tags:
            outcomes[e.row] = Outcome(e.row, "unplaced", f"{e.tag} (no position)", bool(missing))
        elif missing:
            why = "missing " + ", ".join(missing) + ", which has no default"
            outcomes[e.row] = Outcome(e.row, "unplaced", f"{e.tag} ({why})", True)
        elif assumed:
            outcomes[e.row] = Outcome(e.row, "assumed", e.tag)
        else:
            outcomes[e.row] = Outcome(e.row, "placed")
        for io in rows_io:
            if io.confidence == "assumed":
                outcomes[io.row] = Outcome(io.row, "assumed", f"{io.equipment}.{io.port}")
            else:
                outcomes[io.row] = Outcome(io.row, "placed")
        # a pipe the connections made goes right after its upstream component
        for pipe in [p for p in pipes if p["from"].split(".")[0] == sid]:
            out += _pipe_lines(pipe)

    text = _header(loaded, model)
    if composites:
        text += "composites:\n" + "\n".join(composites) + "\n"
    text += "components:\n" + "\n".join(out) + "\n"
    if plan.conveyor_links:
        text += "connections:\n" + "".join(
            f"  - {{from: {a}, to: {b}}}  {comment(f'takeoff {row}')}\n"
            for a, b, row in plan.conveyor_links
        )
    for c, why in plan.unconnected:
        text += comment(f"NOT CONNECTED (takeoff {c.row}): {c.from_} -> {c.to}, line {c.line}: "
                        f"{why}") + "\n"  # fmt: skip
    ordered = [outcomes[r.row] for r in model.rows()]
    if not any(o.missing for o in ordered):
        try:
            self_load(text)
        except Exception as exc:  # noqa: BLE001 - whatever the loader raises is reported
            raise ValueError(
                f"the scene scaffold wrote does not load, which is a bug in scaffold; nothing "
                f"was written. The scene loader says: {exc}"
            ) from exc
    return Scaffold(
        text, ordered, positions, questions, loaded.path.name, f"{model.plant.name}.yaml",
        (len(model.equipment), len(model.connections), len(model.io)),
    )  # fmt: skip


def _along(e: Equipment, model: Takeoff) -> float:
    from openfactory.takeoff import mount_along_m

    assert e.mount is not None
    return mount_along_m(e.mount, model)


def _header(loaded: Loaded, model: Takeoff) -> str:
    p = model.plant
    lines = [
        comment(f"Written by `openfactory takeoff scaffold {loaded.path.name}` (PLAN 5.1b). Every"),
        comment("component names its takeoff row; positions come from the layout sheets or from"),
        comment(
            "core/mounts.place_on_rail. Training and demo use only. Not a safety-rated simulation."
        ),  # fmt: skip
        'openfactory: "0.1"',
        "scene:",
        f"  name: {p.name}",
        f"  description: {flow(p.description)}",
    ]
    if p.author:
        lines.append(f"  author: {flow(p.author)}")
    lines += ["  seed: 1", "plc:", "  protocol: modbus_tcp"]
    return "\n".join(lines) + "\n"


def _binding(io: IoRow, by_tag: dict[str, Equipment], with_range: bool) -> str:
    spec: dict[str, Any] = {"tag": scene_tag(io, by_tag)}
    if io.plc_tag:
        spec["plc_tag"] = io.plc_tag
    if with_range and io.range is not None:
        spec["range"] = [float(io.range[0]), float(io.range[1])]
    if with_range and io.units:
        spec["units"] = io.units
    return flow(spec) if len(spec) > 1 else spec["tag"]


def _io_lines(e: Equipment, rows: list[IoRow], model: Takeoff, *, standin: bool) -> list[str]:
    if not rows:
        return []
    by_tag = model.by_tag()
    out = ["    io:"]
    for io in rows:
        out.append(f"      {io.port}: {_binding(io, by_tag, not standin)}  "
                   + comment(f"takeoff {io.row}"))  # fmt: skip
    return out


def _standin(ctype: str, e: Equipment, rows: list[IoRow]) -> list[str]:
    """The stand-in type: a scenery body and exactly the row's I/O (note §3.4)."""
    out = [
        f"  - type: {ctype}",
        f"    description: {flow(f'PLACEHOLDER for takeoff {e.row} {e.tag} (type unknown), with its I/O rows')}",  # noqa: E501
        "    params: {length: 1.0, width: 1.0, height: 1.0}",
        "    parts:",
        "      - {id: body, type: scenery, description: Body,",
        '         position: [0, "{height} / 2", 0], size: ["{length}", "{height}", "{width}"]}',
    ]
    if not rows:
        out.append("    io: {}")
        return out
    out.append("    io:")
    first_do = next((r.port for r in rows if r.signal == "DO"), None)
    first_ao = next((r for r in rows if r.signal == "AO"), None)
    seen_di = seen_ai = False
    for r in rows:
        rng = [float(r.range[0]), float(r.range[1])] if r.range else [0.0, 100.0]
        units = f", units: {flow(r.units)}" if r.units else ""
        if r.signal == "DO":
            line, why = "{command: DO}", "a command"
        elif r.signal == "AO":
            line, why = f"{{command: AO, range: {flow(rng)}{units}}}", "a command"
        elif r.signal == "DI":
            if not seen_di and first_do:
                follows, why = first_do, "follows the first DO"
            else:
                follows, why = "false", "reads false"
            seen_di = True
            line = f"{{from: {flow(follows)}}}"
        else:
            if not seen_ai and first_ao:
                follows, why = first_ao.port, "follows the first AO"
            else:
                follows, why = repr(rng[0]), "reads the low end of its range"
            seen_ai = True
            line = f"{{from: {flow(str(follows))}, kind: AI, range: {flow(rng)}{units}}}"
        out.append(f"      {r.port}: {line}  " + comment(f"{r.row}: {why}"))
    return out


def _pipe_lines(pipe: dict[str, Any]) -> list[str]:
    lines = [
        f"  - id: {pipe['id']}  "
        + comment(
            f"takeoff {pipe['row']} (line {pipe['line'] or 'not numbered'}"
            f"{', ' + pipe['sheet'] if pipe['sheet'] else ''})"
        ),  # fmt: skip
        "    type: pipe",
        f"    description: {flow('Line ' + (pipe['line'] or pipe['id']))}",
        f"    position: {flow(pipe['position'])}",
        f"    from: {flow(pipe['from'])}",
        f"    to: {flow(pipe['to'])}",
    ]
    if pipe["diameter"]:
        lines.append(f"    diameter: {flow(pipe['diameter'])}")
    return lines
