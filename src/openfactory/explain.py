"""Why it happened (PLAN 5.14g): ``openfactory explain`` and ``POST /api/explain``.

Behavior spec
-------------
:func:`explain` reads one recording (a ``.ofrec`` file, a capture's ``last-60s.ofrec``, or, in a
live run, the last 60 s the capture buffer holds, turned into the same piece of recording a
capture writes) and tells the story of one part, or of a failed check, in order with times. It
is a pure function of the recording: it reads no clock, and **every claim names the tick and the
tag, event or frame it rests on**. Where the cause cannot be told from the data, it says so.

What a recording holds, and so what the story can rest on (PLAN 2.12): every tag change at its
tick, a full state frame every ``frame_every`` ticks (each part's position, velocity and carrier),
and every runtime event. So:

- **Spawned:** the tick and place the frames give (``spawn_tick``, ``spawn_position``), and the
  emitter standing there, if one is within :data:`AT_M`.
- **Carriers:** the carrier in the first frame that shows the part, then each change of carrier
  between two frames ("between ticks A and B"): a recording does not hold the tick between.
- **Sensors:** each photo eye whose detect tag went true while this part's box touched its beam
  at that tick, from positions: whichever part was inside the beam is the one that tripped it.
  Its going false again is listed when this part tripped it.
- **Commands and feedback:** every change of a tag of the device carrying the part, or of a
  device within :data:`NEAR_M` of it at that tick, with where the part was at that moment: the
  carrier, its distance from the infeed end, and how far it was past (downstream of) or before
  the device along the carrier's flow. Whether a command was meant for this part cannot be told
  from the data; the report says it lists commands by nearness, not by intent.
- **The program's own state (PLAN 5.14i):** each watch value the stand-in script published that
  changed between two frames while the part was in the scene ("between ticks A and B"), at most
  :data:`MAX_WATCH_STEPS`.
- **Faults and events:** a fault, a clear or a button press on the carrier or a device within
  :data:`NEAR_M`. In exam mode (the recording's header says so) fault, clear and comms events
  are left out, as ``look`` leaves them out, and the report says so.
- **Moved across the flow:** a run of frames in which something moved the part
  :data:`ACROSS_M` or more across its carrier's flow while it stayed on it (a push that did not
  take it off, a guide).
- **The end:** removed by the remover whose count of the part's name rose between the last frame
  that shows the part and the next ("between ticks A and B"); gone with no remover counting it
  (lost); or still in the scene where the recording ends.

Positions between two frames are interpolated between them (belts move parts at a constant
speed, so this is exact on a belt; a part dropping onto a belt between two frames is placed
within 2 cm, measured); after the last frame that shows a part, carried on from it with its
velocity. Each such claim names the frames it rests on. A part that changed carrier between
two frames is placed with no carrier at the ticks between: which carrier held it is not known.

**Checks.** A recording holds no grades, so ``--check`` works out the part checks again from the
frames: ``parts_reached`` (the first part of that name to reach another remover is the subject,
or none reached any), ``none_lost`` (the first part gone with no remover counting it) and
``max_cycle_s`` (the part that took longest, spawn to remover, to frame resolution). The process
checks (levels, starts, reversals, recipes) are not about parts: the report names the check and
says its cause cannot be told by ``explain``. A capture's piece holds 60 s: a part seen in its
first frame may have been made before it, and the report says the window it covers.

:class:`Explanation` carries the Markdown text, the steps (``tick``, ``t``, ``text``,
``basis``, ``position``) and the part's trail, the positions every frame gives, for the viewer.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from openfactory.core.component import num
from openfactory.core.geometry import segment_hits_aabb, to_local, to_world
from openfactory.observe import carrier_frame, recording_plant, value_text

#: A device this close to a part (horizontally, metres) has its tag changes in the part's story.
NEAR_M = 1.0
#: An emitter this close to where a part appeared is named as where it was made.
AT_M = 0.5
#: The most watch-value changes (PLAN 5.14i) one story lists.
MAX_WATCH_STEPS = 40
#: A part moved this far across its carrier's flow (and kept on it) gets a step of its own.
ACROSS_M = 0.02
#: The margin a photo eye's beam test gives a part's box (as ``components/photo_eye.py``).
TOUCH_M = 0.001
#: Event types that show a fault; left out in exam mode (PLAN 2.11).
FAULT_EVENTS = frozenset({"fault", "clear", "comms"})
#: The part checks ``--check`` can work out again from the frames.
PART_CHECKS = ("parts_reached", "none_lost", "max_cycle_s")


@dataclass
class Step:
    """One claim of the story: what happened, when, and the data it rests on."""

    tick: int
    text: str
    basis: str
    position: list[float] | None = None
    #: The later tick of a claim known only between two frames.
    until: int | None = None

    def document(self, dt: float) -> dict[str, Any]:
        out: dict[str, Any] = {"tick": self.tick, "t": round(self.tick * dt, 6)}
        if self.until is not None:
            out["until_tick"] = self.until
        out.update(text=self.text, basis=self.basis)
        if self.position is not None:
            out["position"] = [round(v, 4) for v in self.position]
        return out


@dataclass
class Explanation:
    """What :func:`explain` gives back: the report and its parts."""

    text: str
    subject: str | None = None
    steps: list[Step] = field(default_factory=list)
    #: ``(tick, (x, y, z))`` of the subject part in every frame that shows it.
    trail: list[tuple[int, tuple[float, float, float]]] = field(default_factory=list)
    from_tick: int = 0
    to_tick: int = 0
    dt: float = 0.02

    def document(self) -> dict[str, Any]:
        """``POST /api/explain``'s answer."""
        return {
            "text": self.text,
            "subject": self.subject,
            "from_tick": self.from_tick,
            "to_tick": self.to_tick,
            "steps": [s.document(self.dt) for s in self.steps],
            "trail": [[k, *(round(v, 4) for v in p)] for k, p in self.trail],
        }


class Unknown(KeyError):
    """No such part or check in the recording (``POST /api/explain``: 404)."""


# -- reading the recording ---------------------------------------------------------------------


@dataclass
class _Data:
    rec: Any
    dt: float
    doc: Mapping[str, Any]
    comps: dict[str, Mapping[str, Any]]
    owner: dict[str, str]
    kinds: dict[str, str]
    ticks: list[int]
    frames: list[dict[str, Any]]
    tracks: dict[str, list[tuple[int, dict[str, Any]]]]
    exam: bool
    held_from: int
    #: Ticks between two frames (``frame_every``).
    every: int = 5

    @classmethod
    def of(cls, rec: Any) -> _Data:
        doc = rec.header["scene"]
        tracks: dict[str, list[tuple[int, dict[str, Any]]]] = {}
        for tick, frame in rec.frames:
            for p in frame["parts"]:
                tracks.setdefault(str(p["id"]), []).append((tick, p))
        return cls(
            rec=rec,
            dt=float(rec.dt),
            doc=doc,
            comps={c["id"]: c for c in doc["components"]},
            owner={t["name"]: t["owner"] for t in doc["tags"]},
            kinds={t["name"]: t["kind"] for t in doc["tags"]},
            ticks=[k for k, _ in rec.frames],
            frames=[f for _, f in rec.frames],
            tracks=tracks,
            exam=bool(rec.header.get("exam")),
            held_from=rec.held_from,
            every=int(rec.header.get("frame_every", 5)),
        )

    def t(self, tick: int) -> str:
        return f"{num(tick * self.dt)} s"

    def frame_index(self, tick: int) -> int:
        import bisect

        return bisect.bisect_right(self.ticks, tick) - 1


def _vec(v: Any) -> tuple[float, float, float]:
    x, y, z = (float(a) for a in v)
    return (x, y, z)


def where_at(
    d: _Data, track: list[tuple[int, dict[str, Any]]], tick: int
) -> tuple[tuple[float, float, float], str | None, str] | None:
    """``(position, carrier, basis)`` of a part at ``tick``, from its frames (see the module
    docstring), or None outside the frames that show it."""
    before = [(k, p) for k, p in track if k <= tick]
    after = [(k, p) for k, p in track if k >= tick]
    if not before:
        return None
    ka, a = before[-1]
    if ka == tick:
        return _vec(a["position"]), a.get("carrier"), f"the frame at tick {ka}"
    if after:
        kb, b = after[0]
        f = (tick - ka) / (kb - ka)
        pa, pb = _vec(a["position"]), _vec(b["position"])
        pos = (
            pa[0] + (pb[0] - pa[0]) * f,
            pa[1] + (pb[1] - pa[1]) * f,
            pa[2] + (pb[2] - pa[2]) * f,
        )
        if a.get("carrier") == b.get("carrier"):
            return pos, a.get("carrier"), f"the frames at ticks {ka} and {kb}"
        # it changed carrier between the two frames: which one held it at this tick is not known
        return pos, None, f"the frames at ticks {ka} and {kb} (it changed carrier between them)"
    if tick - ka > 2 * d.every:
        return None  # past the part's last frame: it was gone by then
    pa, va = _vec(a["position"]), _vec(a.get("velocity", (0, 0, 0)))
    s = (tick - ka) * d.dt
    pos = (pa[0] + va[0] * s, pa[1] + va[1] * s, pa[2] + va[2] * s)
    return pos, a.get("carrier"), f"the frame at tick {ka} and its velocity"


def _state(d: _Data, tick: int, cid: str) -> Mapping[str, Any]:
    i = max(d.frame_index(tick), 0)
    return d.frames[i]["components"].get(cid, {})


def along(d: _Data, carrier: str, tick: int, point: tuple[float, float, float]) -> float | None:
    """``point``'s distance along ``carrier`` from its infeed end, in the carrier's belt frame
    as it was at ``tick``."""
    comp = d.comps.get(carrier)
    if comp is None or "length" not in comp:
        return None
    origin, rot = carrier_frame(comp, _state(d, tick, carrier))
    return to_local(origin, rot, point)[0]


def _horizontal(a: tuple[float, float, float], b: Any) -> float:
    return math.hypot(a[0] - float(b[0]), a[2] - float(b[2]))


def in_beam(d: _Data, eye: str, tick: int, part: dict[str, Any], pos: Any) -> bool:
    """True when the part's box at ``pos`` touches photo eye ``eye``'s beam at ``tick``."""
    comp = d.comps[eye]
    half = float(comp.get("beam_length", 0.0)) / 2
    offset = float(_state(d, tick, eye).get("beam_offset_m", 0.0))
    at, rot = _vec(comp["position"]), float(comp.get("rotation_deg", 0.0))
    p0, p1 = to_world(at, rot, (offset, 0.0, -half)), to_world(at, rot, (offset, 0.0, half))
    size = _vec(part["size"])
    lo = tuple(pos[i] - size[i] / 2 - TOUCH_M for i in range(3))
    hi = tuple(pos[i] + size[i] / 2 + TOUCH_M for i in range(3))
    return segment_hits_aabb(p0, p1, lo, hi)  # type: ignore[arg-type]


# -- one part's story ---------------------------------------------------------------------------


@dataclass
class Outcome:
    kind: str  # "removed", "gone" or "in_scene"
    last: int  # the last frame tick that shows the part
    next: int | None = None  # the first frame tick that does not
    by: str | None = None
    counts: tuple[int, int] | None = None


def outcome(d: _Data, pid: str) -> Outcome:
    """How the part's story ends (see the module docstring)."""
    track = d.tracks[pid]
    last, part = track[-1]
    i = d.ticks.index(last)
    if i == len(d.ticks) - 1:
        return Outcome("in_scene", last)
    nxt = d.ticks[i + 1]
    before, after = d.frames[i]["components"], d.frames[i + 1]["components"]
    name = part["name"]
    rose = []
    for cid, state in after.items():
        if "by_name" not in state:
            continue
        was = int(before.get(cid, {}).get("by_name", {}).get(name, 0))
        now = int(state["by_name"].get(name, 0))
        if now > was:
            dist = _horizontal(_vec(part["position"]), d.comps[cid]["position"])
            rose.append((dist, cid, (was, now)))
    if rose:
        _, by, counts = min(rose)
        return Outcome("removed", last, nxt, by, counts)
    return Outcome("gone", last, nxt)


def _relative(d: _Data, carrier: str | None, tick: int, pos: Any, device: str) -> str:
    """Where the part was against a device: along the carrier's flow, past or before it."""
    if not carrier or device == carrier:
        return ""
    mine, theirs = (
        along(d, carrier, tick, pos),
        along(d, carrier, tick, _vec(d.comps[device]["position"])),
    )
    if mine is None or theirs is None:
        return f", {num(_horizontal(pos, d.comps[device]['position']))} m from {device}"
    gap = mine - theirs
    side = "past" if gap >= 0 else "before"
    return f", its centre {num(abs(gap))} m {side} {device}'s centre along {carrier}'s flow"


def _place(d: _Data, carrier: str | None, tick: int, pos: Any) -> str:
    if carrier:
        x = along(d, carrier, tick, pos)
        return f"on {carrier}" + (f", {num(x)} m from its infeed end" if x is not None else "")
    return "carried by nothing, at (" + ", ".join(num(v) for v in pos) + ")"


def part_steps(d: _Data, pid: str) -> list[Step]:
    """The part's story, in tick order."""
    track = d.tracks[pid]
    first_tick, first = track[0]
    steps: list[Step] = []
    spawn = int(first.get("spawn_tick", first_tick))
    spawn_at = _vec(first.get("spawn_position", first["position"]))
    emitters = sorted(
        (_horizontal(spawn_at, c["position"]), cid)
        for cid, c in d.comps.items()
        if "emitter" in str(c["type"])
    )
    where = f" where {emitters[0][1]} stands" if emitters and emitters[0][0] <= AT_M else ""
    made = "made" if spawn >= d.ticks[0] else "made before this recording begins, at"
    steps.append(
        Step(
            spawn,
            f"{made} {pid} ({first['name']}) at ("
            + ", ".join(num(v) for v in spawn_at)
            + f"){where}",
            f"spawn_tick and spawn_position in the frame at tick {first_tick}",
            list(spawn_at),
        )
    )
    carrier = first.get("carrier")
    steps.append(
        Step(
            first_tick,
            f"{pid} {_place(d, carrier, first_tick, _vec(first['position']))}",
            f"carrier and position in the frame at tick {first_tick}",
            list(_vec(first["position"])),
        )
    )
    for (ka, a), (kb, b) in zip(track, track[1:], strict=False):
        if a.get("carrier") != b.get("carrier"):
            to = b.get("carrier") or "nothing (held, falling or at rest)"
            steps.append(
                Step(
                    ka,
                    f"{pid} went from {a.get('carrier') or 'nothing'} to {to}",
                    f"carrier in the frames at ticks {ka} and {kb}",
                    list(_vec(b["position"])),
                    until=kb,
                )
            )
    steps += _across(d, pid, track)
    end = outcome(d, pid)
    stop = end.next if end.next is not None else end.last
    steps += _watch_steps(d, max(spawn, d.ticks[0]), stop)
    tripped: dict[str, bool] = {}
    for tick, changes in d.rec.io:
        if tick <= d.held_from or tick < max(spawn, d.ticks[0]) or tick > stop:
            continue  # a capture's first line is the whole table, not changes
        for tag, value in changes.items():
            device = d.owner.get(tag)
            if device not in d.comps:
                continue  # the heartbeat and other scene tags
            found = where_at(d, track, tick)
            if found is None:
                continue
            pos, on, basis = found
            kind = d.kinds.get(tag, "")
            if d.comps[device]["type"] == "photo_eye" and kind == "DI":
                if value and in_beam(d, device, tick, first, pos):
                    tripped[device] = True
                    steps.append(
                        Step(
                            tick,
                            f"{tag} went true ({device}): {pid} was in its beam, "
                            f"{_place(d, on, tick, pos)}",
                            f"the change of {tag} at tick {tick}; {pid}'s position from {basis}",
                            list(pos),
                        )
                    )
                elif not value and tripped.pop(device, False):
                    steps.append(
                        Step(
                            tick,
                            f"{tag} went false ({device}): the beam {pid} tripped cleared",
                            f"the change of {tag} at tick {tick}",
                            list(pos),
                        )
                    )
                continue
            near = _horizontal(pos, d.comps[device]["position"])
            if device != on and near > NEAR_M:
                continue
            who = "the PLC's command" if kind in ("DO", "AO") else "the simulation's signal"
            steps.append(
                Step(
                    tick,
                    f"{tag} went {value_text(value)} ({who}, {device}): {pid} was "
                    f"{_place(d, on, tick, pos)}{_relative(d, on, tick, pos, device)}",
                    f"the change of {tag} at tick {tick}; {pid}'s position from {basis}",
                    list(pos),
                )
            )
    for tick, event in d.rec.events:
        if tick < max(spawn, d.ticks[0]) or tick > stop:
            continue
        if d.exam and event.get("type") in FAULT_EVENTS:
            continue
        device = str(event.get("id"))
        found = where_at(d, track, tick)
        if device not in d.comps or found is None:
            continue
        pos, on, basis = found
        if device != on and _horizontal(pos, d.comps[device]["position"]) > NEAR_M:
            continue
        what = event.get("kind") or event.get("value") or ""
        steps.append(
            Step(
                tick,
                f"event {event.get('type')} on {device}"
                + (f" ({what})" if what != "" else "")
                + f": {pid} was {_place(d, on, tick, pos)}",
                f"the {event.get('type')} event at tick {tick}; {pid}'s position from {basis}",
                list(pos),
            )
        )
    last = _vec(track[-1][1]["position"])
    if end.kind == "removed":
        was, now = end.counts or (0, 0)
        text = (
            f"{pid} left the scene at {end.by}: {end.by}'s count of {track[-1][1]['name']} rose "
            f"from {was} to {now}"
        )
        basis = f"{pid} in the frame at tick {end.last}, not at {end.next}; {end.by}'s by_name"
    elif end.kind == "gone":
        text = f"{pid} left the scene and no remover counted it: lost, or taken by something else"
        basis = f"{pid} in the frame at tick {end.last}, not at {end.next}; every remover's by_name"
    else:
        on = _place(d, track[-1][1].get("carrier"), end.last, last)
        text = f"{pid} was still in the scene at tick {end.last}, the last frame of the data, {on}"
        basis = f"the last frame, tick {end.last}"
    steps.append(Step(end.last, text, basis, list(last), until=end.next))
    steps.sort(key=lambda s: (s.tick, s.until if s.until is not None else s.tick))
    return steps


def _watch_steps(d: _Data, start: int, stop: int) -> list[Step]:
    """The program's watch values (PLAN 5.14i) that changed between two frames from ``start``
    to ``stop``: the program's own state while the part was in the scene. At most
    :data:`MAX_WATCH_STEPS`, then a count."""
    from openfactory.observe import watch_text

    out: list[Step] = []
    more = 0
    for (ka, a), (kb, b) in zip(d.rec.frames, d.rec.frames[1:], strict=False):
        if ka < start or ka > stop:
            continue  # a change in the interval the part was made in is not its story
        before, after = a.get("watch") or {}, b.get("watch") or {}
        for name in after:
            if name in before and before[name] == after[name]:
                continue
            if len(out) >= MAX_WATCH_STEPS:
                more += 1
                continue
            was = watch_text(before[name]) if name in before else "unset"
            out.append(
                Step(
                    ka,
                    f"the program's {name} went {was} to {watch_text(after[name])}",
                    f"watch values in the frames at ticks {ka} and {kb}",
                    until=kb,
                )
            )
    if more:
        out.append(
            Step(stop, f"and {more} more changes of the program's watch values", "the frames")
        )
    return out


def _across(d: _Data, pid: str, track: list[tuple[int, dict[str, Any]]]) -> list[Step]:
    """Each run of frames in which something moved the part across its carrier's flow by
    :data:`ACROSS_M` or more (a push that did not take it off, a guide), as one step."""
    out: list[Step] = []
    run: list[tuple[int, int, float, str]] = []  # (from tick, to tick, metres, carrier)

    def close() -> None:
        if run:
            total = sum(r[2] for r in run)
            if abs(total) >= ACROSS_M:
                a, b, carrier = run[0][0], run[-1][1], run[0][3]
                pos = list(_vec(next(p for k, p in track if k == b)["position"]))
                out.append(
                    Step(
                        a,
                        f"{pid} was moved {num(abs(total))} m across {carrier}'s flow and "
                        "stayed on it",
                        f"position across {carrier} in the frames at ticks {a} to {b}",
                        pos,
                        until=b,
                    )
                )
            run.clear()

    for (ka, a), (kb, b) in zip(track, track[1:], strict=False):
        carrier = a.get("carrier")
        comp = d.comps.get(carrier) if carrier else None
        if carrier is None or carrier != b.get("carrier") or comp is None or "length" not in comp:
            close()
            continue
        za = to_local(*carrier_frame(comp, _state(d, ka, carrier)), _vec(a["position"]))[2]
        zb = to_local(*carrier_frame(comp, _state(d, kb, carrier)), _vec(b["position"]))[2]
        if abs(zb - za) >= 0.002:
            if run and run[-1][1] != ka:
                close()
            run.append((ka, kb, zb - za, carrier))
        else:
            close()
    close()
    return out


# -- checks -------------------------------------------------------------------------------------


@dataclass
class Finding:
    """A check worked out again from the frames."""

    name: str
    passed: bool | None  # None: explain cannot work it out from the data
    detail: str
    subject: str | None = None
    at: int | None = None
    basis: str = ""


def findings(d: _Data) -> list[Finding]:
    """Every check the scene declares, as far as the frames tell (see the module docstring)."""
    checks = recording_plant(d.rec.header).checks
    if checks is None:
        return []
    ends = {pid: outcome(d, pid) for pid in d.tracks}
    name_of = {pid: track[0][1]["name"] for pid, track in d.tracks.items()}
    out: list[Finding] = []
    for remover, names in checks.reached().items():
        for name in names:
            mine = [p for p in ends if name_of[p] == name]
            here = [p for p in mine if ends[p].kind == "removed" and ends[p].by == remover]
            wrong = sorted(
                (ends[p].next or 0, p)
                for p in mine
                if ends[p].kind == "removed" and ends[p].by != remover
            )
            label = f"parts_reached {remover}: {name}"
            if wrong:
                at, pid = wrong[0]
                e = ends[pid]
                out.append(
                    Finding(
                        label, False,
                        f"{len(here)} reached {remover}, {len(wrong)} went elsewhere; the first, "
                        f"{pid}, reached {e.by} between ticks {e.last} and {e.next}",
                        pid, at, f"{e.by}'s by_name in the frames at ticks {e.last} and {e.next}",
                    )
                )  # fmt: skip
            elif not here:
                waiting = [p for p in mine if ends[p].kind == "in_scene"]
                more = f"; {len(waiting)} still in the scene at the end" if waiting else ""
                out.append(
                    Finding(
                        label, False,
                        f"no {name} part reached any remover in the recording{more}; which part"
                        " should have and why cannot be told from the data",
                        waiting[0] if waiting else None, None, "every remover's by_name",
                    )
                )  # fmt: skip
            else:
                out.append(Finding(label, True, f"{len(here)} reached {remover}, none elsewhere"))
    if checks.none_lost:
        gone = sorted((ends[p].next or 0, p) for p in ends if ends[p].kind == "gone")
        if gone:
            at, pid = gone[0]
            e = ends[pid]
            out.append(
                Finding(
                    "none_lost", False,
                    f"{len(gone)} left with no remover counting them; the first, {pid}, between "
                    f"ticks {e.last} and {e.next}",
                    pid, at, f"{pid} in the frame at tick {e.last}, not at {e.next}",
                )
            )  # fmt: skip
        else:
            out.append(Finding("none_lost", True, "no part left without a remover counting it"))
    if checks.max_cycle_s is not None:
        limit = checks.max_cycle_s
        worst: tuple[float, str] | None = None
        for pid, e in ends.items():
            spawn = int(d.tracks[pid][0][1].get("spawn_tick", d.tracks[pid][0][0]))
            took = ((e.next if e.next is not None else e.last) - spawn) * d.dt
            if worst is None or took > worst[0]:
                worst = (took, pid)
        label = f"max_cycle_s {limit:g}"
        if worst is None:
            out.append(Finding(label, True, "no part in the recording"))
        else:
            took, pid = worst
            e = ends[pid]
            over = took > limit + 5 * d.dt  # to frame resolution
            how = "removed" if e.kind == "removed" else "in the scene"
            out.append(
                Finding(
                    label, not over,
                    f"the longest, {pid}, up to {num(took)} s from made to {how} (frames every "
                    f"{d.every} ticks); limit {limit:g} s",
                    pid, e.next or e.last, f"spawn_tick and the frames at ticks {e.last}"
                    + (f" and {e.next}" if e.next else ""),
                )
            )  # fmt: skip
    for name in _process_checks(checks):
        out.append(
            Finding(
                name, None,
                "not a part check: its cause cannot be told by explain; `openfactory look` and "
                "`openfactory strip` show the tank, pump or valve it names",
            )
        )  # fmt: skip
    return out


def _process_checks(checks: Any) -> list[str]:
    names = [f"level_within {k}" for k in checks.level_within]
    names += [f"never_overflow {k}" for k in checks.never_overflow]
    names += [f"never_dry {k}" for k in checks.never_dry]
    names += [f"max_starts {k}" for k in checks.max_starts]
    names += [f"max_reversals {k}" for k in checks.max_reversals]
    names += [f"fractions_within {k}" for k in checks.fractions_within]
    names += [f"temperature_within {k}" for k in checks.temperature_within]
    return names


# -- the report ---------------------------------------------------------------------------------


def _head(d: _Data) -> list[str]:
    scene = d.doc["scene"]["name"]
    first, last = d.ticks[0], d.ticks[-1]
    every = d.every
    lines = [
        f"From the recording of {scene}, ticks {first} to {last} ({d.t(first)} to {d.t(last)}): "
        f"every tag change, a frame every {every} ticks, every event. Each line names the tick "
        "and the data it rests on.",
    ]
    if first > 0:
        lines.append(
            f"The recording starts at tick {first} (a capture's last 60 s): nothing before it is "
            "in the data."
        )
    if d.exam:
        lines.append("Faults are hidden in exam mode: fault, clear and comms events are left out.")
    return lines


def _story(d: _Data, pid: str, steps: list[Step]) -> list[str]:
    out = []
    for s in steps:
        when = (
            f"{d.t(s.tick)} (tick {s.tick})"
            if s.until is None
            else f"between {d.t(s.tick)} and {d.t(s.until)} (ticks {s.tick} to {s.until})"
        )
        out.append(f"- {when}: {s.text}. *[{s.basis}]*")
    out.append(
        f"- Commands are listed by nearness (the carrier, and devices within {num(NEAR_M, 1)} m "
        f"of {pid}), not by intent: whether a command was meant for {pid} cannot be told from "
        "the data."
    )
    return out


def _part(d: _Data, pid: str) -> Explanation:
    steps = part_steps(d, pid)
    track = d.tracks[pid]
    lines = [
        f"# Why: part {pid} ({track[0][1]['name']})",
        "",
        *_head(d),
        "",
        *_story(d, pid, steps),
    ]
    return Explanation(
        "\n".join(lines) + "\n", pid, steps, [(k, _vec(p["position"])) for k, p in track],
        d.ticks[0], d.ticks[-1], d.dt,
    )  # fmt: skip


def _check(d: _Data, wanted: str) -> Explanation:
    found = findings(d)
    names = [f.name for f in found]
    match = [f for f in found if f.name == wanted] or [
        f for f in found if f.name.startswith(wanted)
    ]
    if not match:
        raise Unknown(
            f"no check {wanted!r} in this scene; its checks: {', '.join(names) or 'none'}"
        )
    failed = [f for f in match if f.passed is False]
    f = (failed or match)[0]
    lines = [f"# Why: check `{f.name}`", "", *_head(d), ""]
    state = {True: "passed", False: "failed", None: "cannot be worked out"}[f.passed]
    lines.append(
        f"In this recording the check {state}: {f.detail}." + (f" *[{f.basis}]*" if f.basis else "")
    )
    steps: list[Step] = []
    trail: list[tuple[int, tuple[float, float, float]]] = []
    if f.passed is False and f.subject is not None and f.subject in d.tracks:
        steps = part_steps(d, f.subject)
        trail = [(k, _vec(p["position"])) for k, p in d.tracks[f.subject]]
        lines += [
            "",
            f"## {f.subject}, the first part it failed on",
            "",
            *_story(d, f.subject, steps),
        ]
    elif f.passed is False:
        lines += ["", "No part is the subject: the data does not name one."]
    return Explanation(
        "\n".join(lines) + "\n", f.subject, steps, trail, d.ticks[0], d.ticks[-1], d.dt
    )


def explain(rec: Any, *, check: str | None = None, part: str | None = None) -> Explanation:
    """The story of ``part``, of ``check``, or (neither) of every failed check (see the module
    docstring). :class:`Unknown` for a part or check not in the recording."""
    d = _Data.of(rec)
    if part is not None:
        if part not in d.tracks:
            raise Unknown(
                f"no part {part!r} in this recording (ticks {d.ticks[0]} to {d.ticks[-1]})"
            )
        return _part(d, part)
    if check is not None:
        return _check(d, check)
    failed = [f for f in findings(d) if f.passed is False]
    if not failed:
        known = findings(d)
        body = "No check failed in this recording." if known else "The scene declares no checks."
        return Explanation(
            f"# Why\n\n{_head(d)[0]}\n\n{body}\n", None, [], [], d.ticks[0], d.ticks[-1], d.dt
        )
    parts = [_check(d, f.name) for f in failed]
    first = parts[0]
    text = "\n".join(p.text for p in parts)
    return Explanation(text, first.subject, first.steps, first.trail, d.ticks[0], d.ticks[-1], d.dt)


def up_to(rec: Any, tick: int) -> Any:
    """The recording as it was when the frame at ``tick`` was shown (a replay's explain)."""
    from openfactory.record import Recording

    return Recording(
        rec.header,
        [(k, f) for k, f in rec.frames if k <= tick],
        [(k, c) for k, c in rec.io if k <= tick],
        [(k, e) for k, e in rec.events if k <= tick],
        None,
        True,
    )
