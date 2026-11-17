"""The plant in words (PLAN 5.14b): ``openfactory look`` and ``GET /api/look``.

Behavior spec
-------------
:func:`look` writes a Markdown report of the plant at one tick. It is a pure function of:

- the scene document (``/api/scene``, as a recording's header holds it);
- one snapshot (``/api/state``, or a recording's frame);
- the tag change log up to that tick (PLAN 5.14a, or a recording's I/O lines);
- the scene's declared ``checks:`` and its link ends (:class:`Plant`), taken from the parsed
  scene in a live run and from the scene's YAML in a recording's header.

It reads no clock and writes nothing machine-specific: no path, host, port or version. So a
live run and its own recording give the same text at the same tick.

The sections, in this order:

1. **The scene in one line:** name, time and tick, description, and how many components and
   parts.
2. **Checks:** each declared check in plain words, with what the snapshot shows of it now, such
   as each remover's count of each part name, the oldest part's age, or a tank's level. The
   grade itself is ``openfactory test``'s (or ``GET /api/checks``'s, live) at ``duration_s``:
   the snapshot does not hold what grading needs (the removal log, lost parts, a pump's starts),
   and a recording holds no grades.
3. **Active faults:** every component whose state carries a fault. In exam mode (PLAN 2.11)
   the snapshot hides faults from trainees, so the section says only that faults are hidden in
   exam mode; it never says "None" while a fault may be active.
4. **Components:** one line each, the type's own sentence (:meth:`Component.describe`), then
   each bound tag and its value.
5. **Parts:** each part's id and name, the component carrying it, its distance along that
   component from the infeed end, and its speed. A part carried by nothing is held by a
   gripper, falling, or at rest, with its position. At most :data:`MAX_PARTS` are listed, then a
   count per carrier.
6. **Changes in the last N seconds** (default 5): each DI and DO edge on its own line, and each
   AI and AO tag on one line (first and last value and how many changes), so a process scene
   whose analog inputs move every tick still fits. A DI or DO that changed more than
   :data:`MAX_TOGGLES` times in the window (the heartbeat, a chattering eye) is one line too.
   Tick 0 holds the starting values, not changes, and is left out. When the window reaches
   back past what the change log still holds (a live log keeps 600 s or 20,000 changes,
   whichever is fewer), the heading gives the time the listing really starts from and says older
   changes are no longer held. A recording holds every change, so it is never short.

Tags no component owns (the simulator heartbeat) are listed under the components as scene
tags.

``component`` narrows the report to one component: its line, what it is connected to (conveyor
connections, pipe and valve ends, references such as a transmitter's ``measures``, and the
devices standing on a belt by W214's footprint rule), the parts it carries, and the changes of
its own tags.

Distances along a carrier use the carrier's own belt frame (``KinematicModel._frame``): a belt,
roller conveyor or lift from its origin at the infeed end, a turntable from its deck's current
infeed end, turned by the deck's angle.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

import openfactory.components  # noqa: F401 - registers the built-in types and their sentences
from openfactory.core.component import REGISTRY, num
from openfactory.core.geometry import rotate_y, to_local
from openfactory.core.schema import Checks

#: Parts listed one by one; the rest are counted by carrier.
MAX_PARTS = 30
#: Discrete edges listed one by one in the changes section; the rest are counted.
MAX_EDGES = 40
#: A DI or DO with more changes than this in the window is summed up on one line.
MAX_TOGGLES = 4
#: Default window of the changes section, simulated seconds.
SINCE_S = 5.0

#: Each kind of change: (tick, tag, value).
Change = tuple[int, str, Any]


@dataclass(frozen=True)
class Plant:
    """What the report needs that the scene document does not carry."""

    checks: Checks | None = None
    #: Component id -> the ids its link ends name (``from``, ``to``), without a port.
    links: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def from_scene(cls, scene: Any) -> Plant:
        """From a parsed :class:`~openfactory.core.schema.Scene` (a live run)."""
        ends = [(c.id, getattr(c, "from_", None), getattr(c, "to", None)) for c in scene.components]
        return cls(scene.checks, _links(ends))

    @classmethod
    def from_yaml(cls, raw: Mapping[str, Any]) -> Plant:
        """From the scene's YAML as loaded (a recording's ``scene_yaml``)."""
        checks = Checks.model_validate(raw["checks"]) if raw.get("checks") else None
        comps = raw.get("components") or []
        ends = [(c.get("id"), c.get("from"), c.get("to")) for c in comps if isinstance(c, dict)]
        return cls(checks, _links(ends))


def _links(ends: Iterable[tuple[Any, Any, Any]]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for cid, a, b in ends:
        names = [str(x).split(".")[0] for x in (a, b) if isinstance(x, str)]
        if cid and names:
            out[str(cid)] = names
    return out


def value_text(value: Any) -> str:
    """A tag value as the report prints it."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return num(value, 3)
    return str(value)


def describe(component: Mapping[str, Any], state: Mapping[str, Any]) -> str:
    """The component type's own sentence, or its raw state for a type not in this release."""
    kind = str(component["type"])
    if kind in REGISTRY:
        cls = REGISTRY.resolve(kind)
        try:
            text = cls.describe(dict(state), dict(component))
        except (KeyError, TypeError, ValueError):
            text = ""
        if text:
            return text
    return ", ".join(f"{k} {value_text(v)}" for k, v in sorted(state.items())) or "no state"


def carrier_frame(
    component: Mapping[str, Any], state: Mapping[str, Any]
) -> tuple[tuple[float, float, float], float]:
    """``(origin, rotation_deg)`` of a carrier's belt frame, from the document and its state
    (as ``KinematicModel._frame``: a turntable's deck turns, a lift's rises)."""
    x, y, z = (float(v) for v in component["position"])
    rot = float(component.get("rotation_deg", 0.0))
    if component["type"] == "turntable":
        heading = rot + float(state.get("angle_deg", 0.0))
        dx, _, dz = rotate_y((float(component["length"]) / 2, 0.0, 0.0), heading)
        return (x - dx, y, z - dz), heading
    if component["type"] == "lift":
        return (x, y + float(state.get("height_m", 0.0)), z), rot
    return (x, y, z), rot


def _where(p: Mapping[str, Any]) -> str:
    return "(" + ", ".join(num(v) for v in p["position"]) + ")"


def part_line(
    part: Mapping[str, Any],
    components: Mapping[str, Mapping[str, Any]],
    states: Mapping[str, Mapping[str, Any]],
) -> str:
    """One part: id and name, where it is, and how fast it moves."""
    head = f"`{part['id']}` {part['name']}"
    speed = math.sqrt(sum(float(v) ** 2 for v in part.get("velocity", (0, 0, 0))))
    carrier = part.get("carrier")
    if carrier:
        comp = components.get(carrier)
        along = ""
        if comp is not None:
            origin, rot = carrier_frame(comp, states.get(carrier, {}))
            x = to_local(origin, rot, tuple(float(v) for v in part["position"]))[0]
            along = f", {num(x)} m from the infeed end"
        moving = f"moving {num(speed)} m/s" if speed >= 0.005 else "at rest"
        return f"{head}, on {carrier}{along}, {moving}"
    if part.get("follow"):
        return f"{head}, held by a gripper at {_where(part)}"
    if speed >= 0.005:
        return f"{head}, carried by nothing: falling at {_where(part)}, {num(speed)} m/s"
    return f"{head}, carried by nothing: at rest at {_where(part)}"


def _check_lines(
    checks: Checks, snapshot: Mapping[str, Any], dt: float, components: Mapping[str, Any]
) -> list[str]:
    states = snapshot["components"]
    out: list[str] = []
    for remover, names in checks.reached().items():
        by_name = states.get(remover, {}).get("by_name", {})
        got = ", ".join(f"{n} {by_name.get(n, 0)}" for n in names)
        others = sorted(
            f"{r} {n} {count}"
            for r, s in states.items()
            if r != remover and "by_name" in s
            for n, count in s["by_name"].items()
            if n in names and count
        )
        wrong = f"; elsewhere so far: {', '.join(others)}" if others else ""
        out.append(
            f"- `parts_reached`: {' and '.join(names)} to {remover}, and nowhere else. "
            f"So far {remover} has {got}{wrong}."
        )
    if checks.none_lost:
        out.append("- `none_lost`: no part falls out of the scene.")
    if checks.max_cycle_s is not None:
        now = int(snapshot["tick"])
        ages = sorted(((now - int(p["spawn_tick"])) * dt, p["id"]) for p in snapshot["parts"])
        oldest = f"the oldest part now is `{ages[-1][1]}`, {num(ages[-1][0])} s" if ages else (
            "no part is in the scene now"
        )  # fmt: skip
        out.append(
            f"- `max_cycle_s`: every part is removed within {num(checks.max_cycle_s, 1)} s of "
            f"being made; {oldest}."
        )

    def tank_now(tid: str, key: str, unit: str, digits: int = 2) -> str:
        value = states.get(tid, {}).get(key)
        return f"now {num(value, digits)} {unit}" if value is not None else "not in the snapshot"

    for tid, band in checks.level_within.items():
        out.append(
            f"- `level_within`: {tid} level stays in {num(band.band[0])} to {num(band.band[1])} m "
            f"from {num(band.after_s, 1)} s; {tank_now(tid, 'level_m', 'm')}."
        )
    for tid in checks.never_overflow:
        over = states.get(tid, {}).get("overflowing")
        out.append(
            f"- `never_overflow`: {tid} never overflows; now {'overflowing' if over else 'not'}."
        )
    for pid in checks.never_dry:
        dry = states.get(pid, {}).get("dry")
        out.append(
            f"- `never_dry`: {pid} never runs dry; now {'running dry' if dry else 'not dry'}."
        )
    for pid, n in checks.max_starts.items():
        out.append(f"- `max_starts`: {pid} starts at most {n} times, and at least once.")
    for vid, n in checks.max_reversals.items():
        out.append(f"- `max_reversals`: {vid}'s command turns back at most {n} times.")
    for tid, bands in checks.fractions_within.items():
        parts = ", ".join(
            f"{k} {num(lo, 1)} to {num(hi, 1)} %" for k, (lo, hi) in bands.fractions.items()
        )
        out.append(
            f"- `fractions_within`: {tid} holds {parts} from {num(bands.after_s, 1)} s, "
            "never empty."
        )
    for tid, band in checks.temperature_within.items():
        out.append(
            f"- `temperature_within`: {tid} stays in {num(band.band[0], 1)} to "
            f"{num(band.band[1], 1)} degC from {num(band.after_s, 1)} s; "
            f"{tank_now(tid, 'temperature_c', 'degC', 1)}."
        )
    return out


def _connected(cid: str, document: Mapping[str, Any], plant: Plant) -> list[str]:
    """What a component is connected to: conveyor connections, link ends, and references."""
    found: set[str] = set()
    for conn in document.get("connections", []):
        if conn["from"] == cid:
            found.add(conn["to"])
        if conn["to"] == cid:
            found.add(conn["from"])
    found.update(plant.links.get(cid, []))
    found.update(other for other, ends in plant.links.items() if cid in ends)
    ref_keys = ("measures", "tank", "drives")
    for comp in document["components"]:
        refs = [str(comp[k]).split(".")[0] for k in ref_keys if isinstance(comp.get(k), str)]
        if comp["id"] == cid:
            found.update(refs)
        elif cid in refs:
            found.add(comp["id"])
    comps = {c["id"]: c for c in document["components"]}
    for belt, device in _standing(comps):
        if cid == device:
            found.add(belt)
        elif cid == belt:
            found.add(device)
    found.discard(cid)
    return sorted(found)


def _standing(comps: Mapping[str, Mapping[str, Any]]) -> list[tuple[str, str]]:
    """``(belt, device)`` for each device standing on a belt or roller conveyor, by W214's rule
    (``core/mounts.py``): the device's origin inside the belt's footprint widened by
    ``BESIDE_M`` across the flow, with the belt top 0 to ``ABOVE_M`` below it."""
    from openfactory.core.mounts import ABOVE_M, BESIDE_M, RAIL_TYPES

    out = []
    belts = [c for c in comps.values() if c["type"] in RAIL_TYPES]
    for dev in comps.values():
        if dev["type"] in RAIL_TYPES:
            continue
        point = tuple(float(v) for v in dev["position"])
        for belt in belts:
            origin = tuple(float(v) for v in belt["position"])
            x, y, z = to_local(origin, float(belt.get("rotation_deg", 0.0)), point)
            half = float(belt["width"]) / 2 + BESIDE_M
            if 0 <= x <= float(belt["length"]) and abs(z) <= half and 0 <= y <= ABOVE_M:
                out.append((belt["id"], dev["id"]))
    return out


def look(
    document: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    changes: Iterable[Change],
    plant: Plant | None = None,
    *,
    since_s: float = SINCE_S,
    component: str | None = None,
    exam: bool = False,
    dropped_to: int = -1,
) -> str:
    """The report (see the module docstring). ``changes`` are ``(tick, tag, value)`` in order,
    up to the snapshot's tick; later ones are ignored. ``exam``: the run is in exam mode, so the
    snapshot hides faults. ``dropped_to``: the newest tick whose change the log no longer holds
    (:attr:`openfactory.core.iolog.IoLog.dropped_to`; -1 when it holds every change). KeyError
    for an unknown ``component``."""
    plant = plant or Plant()
    dt = float(document["dt"])
    tick, t = int(snapshot["tick"]), float(snapshot["t"])
    comps = {c["id"]: c for c in document["components"]}
    states = snapshot["components"]
    io = snapshot["io"]
    kinds = {tag["name"]: tag["kind"] for tag in document["tags"]}
    owned: dict[str, list[str]] = {}
    for tag in document["tags"]:
        owned.setdefault(tag["owner"], []).append(tag["name"])
    if component is not None and component not in comps:
        raise KeyError(f"no component {component!r} in this scene")

    scene = document["scene"]
    parts = sorted(snapshot["parts"], key=lambda p: str(p["id"]))
    lines = [
        f"# {scene['name']} at t = {num(t)} s (tick {tick})",
        "",
        f"{scene.get('description', '').strip()} {len(comps)} components, "
        f"{len(parts)} parts in the scene.",
    ]
    if component is None:
        lines += ["", "## Checks", ""]
        checks = plant.checks
        if checks is None:
            lines.append("The scene declares no checks.")
        else:
            graded = (
                f"graded at {num(checks.duration_s, 1)} s by `openfactory test`"
                " (live: `GET /api/checks`)"
            )
            lines.append(
                f"Declared, {graded}; at {num(t)} s the run "
                + ("has reached that time." if t >= checks.duration_s - 1e-9 else "has not.")
            )
            lines += _check_lines(checks, snapshot, dt, comps)
        lines += ["", "## Active faults", ""]
        if exam:  # the snapshot hides them from trainees (PLAN 2.11): never say "None"
            lines.append("Faults are hidden in exam mode.")
        else:
            faults = [f"- {cid}: {s['fault']}" for cid, s in states.items() if s.get("fault")]
            lines += faults or ["None."]

    shown = [component] if component is not None else list(comps)
    lines += ["", "## Components" if component is None else f"## {component}", ""]
    for cid in shown:
        comp = comps[cid]
        inside = f", in {comp['composite']['id']}" if comp.get("composite") else ""
        lines.append(f"- `{cid}` ({comp['type']}{inside}): {describe(comp, states.get(cid, {}))}.")
        tags = owned.get(cid, [])
        if tags:
            lines.append("  " + ", ".join(f"{name} = {value_text(io[name])}" for name in tags))
    others = [name for owner, names in owned.items() if owner not in comps for name in names]
    if component is None and others:
        lines.append("- Scene tags: " + ", ".join(f"{n} = {value_text(io[n])}" for n in others))
    if component is not None:
        near = _connected(component, document, plant)
        lines.append(f"- Connected to: {', '.join(near) if near else 'nothing'}.")

    listed = parts if component is None else [p for p in parts if p.get("carrier") == component]
    lines += ["", "## Parts" if component is None else f"## Parts on {component}", ""]
    if not listed:
        lines.append("None.")
    for p in listed[:MAX_PARTS]:
        lines.append("- " + part_line(p, comps, states))
    if len(listed) > MAX_PARTS:
        rest = Counter(p.get("carrier") or "nothing" for p in listed[MAX_PARTS:])
        counts = ", ".join(f"{c} {n}" for c, n in sorted(rest.items()))
        lines.append(f"- and {len(listed) - MAX_PARTS} more, by carrier: {counts}")

    since_ticks = round(since_s / dt)
    first = max(1, tick - since_ticks + 1)  # tick 0 holds starting values, not changes
    short = dropped_to >= first  # the log no longer holds the start of the window
    if short:
        first = dropped_to + 1
    wanted = None if component is None else set(owned.get(component, []))
    window = [
        (k, tag, v)
        for k, tag, v in changes
        if first <= k <= tick and (wanted is None or tag in wanted)
    ]
    if short:
        heading = (
            f"## Changes after {num((first - 1) * dt)} s, up to {num(t)} s (the last "
            f"{num(since_s, 1)} s were asked for; older changes are no longer held)"
        )
    else:
        heading = (
            f"## Changes in the last {num(since_s, 1)} s (after {num((first - 1) * dt)} s, "
            f"up to {num(t)} s)"
        )
    lines += ["", heading, ""]
    by_tag: dict[str, list[Change]] = {}
    for c in window:
        by_tag.setdefault(c[1], []).append(c)
    discrete = {tag for tag in by_tag if kinds.get(tag) in ("DI", "DO")}
    # a DI or DO that changed more than MAX_TOGGLES times (a heartbeat, a chattering eye): one line
    busy = {tag for tag in discrete if len(by_tag[tag]) > MAX_TOGGLES}
    edges = [c for c in window if c[1] in discrete and c[1] not in busy]
    analog = {tag: seen for tag, seen in by_tag.items() if tag not in discrete or tag in busy}
    if not window:
        lines.append("None.")
    for k, tag, v in edges[:MAX_EDGES]:
        lines.append(f"- {num(k * dt)} s (tick {k}): {tag} = {value_text(v)}")
    if len(edges) > MAX_EDGES:
        lines.append(f"- and {len(edges) - MAX_EDGES} more DI and DO changes")
    for tag in sorted(analog):
        seen = analog[tag]
        lines.append(
            f"- {tag}: {value_text(seen[0][2])} at {num(seen[0][0] * dt)} s to "
            f"{value_text(seen[-1][2])} at {num(seen[-1][0] * dt)} s ({len(seen)} changes)"
        )
    return "\n".join(lines) + "\n"


def recording_plant(header: Mapping[str, Any]) -> Plant:
    """The :class:`Plant` of a recording, from the scene's YAML in its header."""
    import yaml

    raw = yaml.safe_load(header.get("scene_yaml") or "") or {}
    return Plant.from_yaml(raw if isinstance(raw, dict) else {})


def frame_at(rec: Any, at_s: float) -> tuple[int, dict[str, Any]]:
    """The recording's frame at or before ``at_s`` simulated seconds (its first frame before
    that). Frames are every ``frame_every`` ticks (10 Hz at 50 Hz)."""
    best = rec.frames[0]
    for tick, frame in rec.frames:
        if tick * rec.dt > at_s + 1e-9:
            break
        best = (tick, frame)
    return best


def look_recording(
    rec: Any,
    at_s: float,
    *,
    since_s: float = SINCE_S,
    component: str | None = None,
) -> str:
    """``openfactory look FILE.ofrec --at T``: the report at the frame at or before ``at_s``."""
    tick, frame = frame_at(rec, at_s)
    changes = [(k, tag, v) for k, items in rec.io if k <= tick for tag, v in items.items()]
    return look(
        rec.header["scene"],
        frame,
        changes,
        recording_plant(rec.header),
        since_s=since_s,
        component=component,
        exam=bool(rec.header.get("exam")),
    )
