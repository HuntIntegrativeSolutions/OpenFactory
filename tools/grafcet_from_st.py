# ruff: noqa: E501 - the HTML template's legend markup is clearer on one line per item
"""GRAFCET chart of a shipped scene's OpenPLC program (PLAN 2.8; ruled 2026-10-31).

Usage::

    uv run python tools/grafcet_from_st.py sorting-by-height OUT.html
    uv run python tools/grafcet_from_st.py --all kits          # kits/<scene>/<scene>-grafcet.html

Charts are generated, never hand-edited. Two parts:

- **Sequence data** (``SCENES``): for each scene, its partial GRAFCETs (steps with their actions,
  transitions with their conditions), written by a person from ``examples/openplc/<scene>.st``.
  This is the reading of the program; it is not parsed from the ST automatically, so a change to
  a program needs a matching change here (``tests/unit/test_grafcet_from_st.py`` checks that
  every tag a chart names is declared in its program, and that each kit's chart is current).
- **Layout** (``render``): the HIS Controls GRAFCET standards (``grafcet-standards.md``, the
  /grafcet-chart-creator skill's rules), 1-column (none of these programs has fault codes or an
  emergency override): numbered steps, the initial step double-bordered (IEC 60848 §4.2.2),
  transition bars with their conditions above, action blocks at x 380-810, green dashed clean
  returns in the left margin, an alternative branch drawn as a link "from S<n>", and partial
  GRAFCETs (concurrent logic) one under another, each with its own initial step.

The output is deterministic: the same sequence data gives the same bytes. Every chart carries
the "generated, pending PE review" marker (an HTML comment on line 2 and a banner) and a single
footer line, "HIS Controls, OpenFactory, CC-BY-4.0". Charts are engineering references, not PLC
programs; OpenFactory is not a safety-rated simulation.
"""

from __future__ import annotations

import html
import math
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

MARKER = "generated, pending PE review"
FOOTER = "HIS Controls, OpenFactory, CC-BY-4.0"
W = 900  # 1-column canvas
STEP_X0, STEP_X1, CX = 88, 368, 228
ACT_X0, ACT_X1, ACT_TX = 380, 810, 392
LINE = 18
#: a white halo so a connector line passing behind a transition label never cuts its text
HALO = 'paint-order="stroke" stroke="#ffffff" stroke-width="4" stroke-linejoin="round"'

KINDS = {  # fill, border, width, text, action fill, action border
    "initial": ("#dbeafe", "#1d6fb8", 4, "#1a2a4a", "#f8f9fa", "#c5cae9"),
    "normal": ("#fdfefe", "#1d6fb8", 2, "#1a2a4a", "#f8f9fa", "#c5cae9"),
    "running": ("#ecf7ee", "#2e7d32", 2.5, "#1a4a1a", "#f0f8f2", "#a5d6a7"),
    "warning": ("#fff8e1", "#e07b00", 2, "#5a3500", "#fffbf0", "#ffe082"),
}


@dataclass
class Step:
    num: int
    title: str
    actions: list[str]
    kind: str = "normal"
    initial: bool = False
    header: str = "Actions while active"


@dataclass
class Trans:
    num: str  # "T1"
    cond: list[str]  # 1-2 lines
    note: str = ""  # secondary note (timer preset, ST line refs)
    target: int | None = None  # a step number to return to (green dashed arc); None = next step
    source: int | None = None  # drawn after a different step: "from S<n>" reference


@dataclass
class Graph:
    title: str
    items: list = field(default_factory=list)


def step_height(step: Step) -> int:
    n = len(step.actions) + 1  # header line
    if n <= 7:
        h = 170
    elif n <= 9:
        h = 185
    elif n <= 11:
        h = 215
    else:
        h = math.ceil((n * LINE + 50) / 5) * 5
    return h + (10 if step.initial else 0)


def esc(s: str) -> str:
    return html.escape(s, quote=True)


def wrap(text: str, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines


def render(scene: str, seq_name: str, process: str, graphs: list[Graph], source: dict) -> str:
    out: list[str] = []
    y = 70
    step_y: dict[int, tuple[int, int]] = {}  # step -> (top, height)
    returns: list[tuple[float, int]] = []  # (y of horizontal, target step)
    for g in graphs:
        out.append(
            f'<text x="20" y="{y + 18}" font-size="13" font-weight="700" fill="#1a2a4a">'
            f"{esc(g.title)}</text>"
        )
        out.append(
            f'<line x1="20" y1="{y + 26}" x2="{W - 20}" y2="{y + 26}" stroke="#c5cae9" '
            'stroke-width="0.8"/>'
        )
        y += 50
        for item in g.items:
            if isinstance(item, Step):
                s = item
                h = step_height(s)
                fill, border, bw, tcol, afill, aborder = KINDS["initial" if s.initial else s.kind]
                if s.initial and s.kind == "running":
                    fill, border, bw, tcol, afill, aborder = KINDS["initial"]
                step_y[s.num] = (y, h)
                out.append(
                    f'<rect x="{STEP_X0}" y="{y}" width="{STEP_X1 - STEP_X0}" height="{h}" '
                    f'rx="8" fill="{fill}" stroke="{border}" stroke-width="{bw}"/>'
                )
                if s.initial:  # IEC 60848 §4.2.2: double border
                    out.append(
                        f'<rect x="{STEP_X0 + 8}" y="{y + 8}" width="{STEP_X1 - STEP_X0 - 16}" '
                        f'height="{h - 16}" rx="6" fill="none" stroke="{border}" '
                        'stroke-width="1.5"/>'
                    )
                out.append(f'<circle cx="102" cy="{y + 20}" r="14" fill="{border}"/>')
                out.append(
                    f'<text x="102" y="{y + 24}" font-size="{12 if s.num < 100 else 9}" '
                    'font-weight="700" fill="#fff" '
                    f'text-anchor="middle">{s.num}</text>'
                )
                title = wrap(s.title, 24)
                ty = y + h / 2 - (len(title) - 1) * 9 + 5
                for i, line in enumerate(title):
                    out.append(
                        f'<text x="{CX}" y="{ty + i * 18:.0f}" font-size="15" font-weight="700" '
                        f'fill="{tcol}" text-anchor="middle">{esc(line)}</text>'
                    )
                out.append(
                    f'<text x="{CX}" y="{y + h - 14}" font-size="10" fill="#5a6478" '
                    f'text-anchor="middle">Step S{s.num}</text>'
                )
                # action block, same vertical extent as the step
                out.append(
                    f'<line x1="{STEP_X1}" y1="{y + 26}" x2="{ACT_X0}" y2="{y + 26}" '
                    'stroke="#2d3748" stroke-width="1.5"/>'
                )
                out.append(
                    f'<rect x="{ACT_X0}" y="{y}" width="{ACT_X1 - ACT_X0}" height="{h}" '
                    f'fill="{afill}" stroke="{aborder}" stroke-width="1"/>'
                )
                out.append(
                    f'<text x="{ACT_TX}" y="{y + 22}" font-size="12" font-weight="600" '
                    f'fill="#1a2a4a">{esc(s.header)}</text>'
                )
                for i, line in enumerate(s.actions):
                    mono = not line.startswith("# ")
                    text = line[2:] if not mono else line
                    style = (
                        'font-family="Consolas, Menlo, monospace" font-size="11" fill="#1a2a4a" '
                        'xml:space="preserve"'
                        if mono
                        else 'font-size="10" fill="#5a6478" font-style="italic"'
                    )
                    out.append(
                        f'<text x="{ACT_TX}" y="{y + 22 + (i + 1) * LINE}" {style}>'
                        f"{esc(text)}</text>"
                    )
                y += h
            else:
                t = item
                labels = list(t.cond)
                n = len(labels) + (1 if t.note else 0)
                # the top label line keeps >= 12 px below the step above (standard: 40 px minimum)
                before = max(40, 39 + 13 * (n - 1))
                top = y
                if t.source is not None:
                    before += 20
                    out.append(
                        f'<text x="{CX + 8}" y="{top + 12}" font-size="10" font-style="italic" '
                        f'fill="#2d3748">from S{t.source} (alternative branch)</text>'
                    )
                    out.append(
                        f'<path d="M{CX} {top} L{CX} {top + before - 3}" stroke="#2d3748" '
                        'stroke-width="1.5" fill="none" marker-end="url(#ar)"/>'
                    )
                else:
                    out.append(
                        f'<line x1="{CX}" y1="{top}" x2="{CX}" y2="{top + before - 3}" '
                        'stroke="#2d3748" stroke-width="1.5"/>'
                    )
                bar = top + before
                out.append(
                    f'<line x1="{STEP_X0}" y1="{bar}" x2="{STEP_X1}" y2="{bar}" stroke="#2d3748" '
                    'stroke-width="6"/>'
                )
                out.append(
                    f'<text x="{STEP_X0 - 8}" y="{bar + 4}" font-size="10" font-weight="700" '
                    f'fill="#2d3748" text-anchor="end">{esc(t.num)}</text>'
                )
                base = bar - 3 - 14  # last condition line: >= 14 px above the bar top
                lines = labels[::-1]
                for i, line in enumerate(lines):
                    out.append(
                        f'<text x="{ACT_X0}" y="{base - i * 13}" font-size="11" fill="#1a2a4a" '
                        f'text-anchor="end" {HALO}>{esc(line)}</text>'
                    )
                if t.note:
                    out.append(
                        f'<text x="{ACT_X0}" y="{base - len(lines) * 13}" font-size="9" '
                        f'fill="#5a6478" text-anchor="end" {HALO}>{esc(t.note)}</text>'
                    )
                if t.target is None:
                    out.append(
                        f'<line x1="{CX}" y1="{bar + 3}" x2="{CX}" y2="{bar + 30}" '
                        'stroke="#2d3748" stroke-width="1.5"/>'
                    )
                    y = bar + 30
                else:
                    hy = bar + 22
                    out.append(
                        f'<path d="M{CX} {bar + 3} L{CX} {hy} L38 {hy}" stroke="#2e7d32" '
                        'stroke-width="1.5" stroke-dasharray="6 4" fill="none"/>'
                    )
                    returns.append((hy, t.target))
                    y = bar + 60
        y += 30
    # return arcs: one lane at x=38 per target, up to the step's mid height
    for target in sorted({r[1] for r in returns}):
        top, h = step_y[target]
        mid = top + h / 2
        low = max(hy for hy, tg in returns if tg == target)
        out.append(
            f'<path d="M38 {low} L38 {mid} L{STEP_X0 - 2} {mid}" stroke="#2e7d32" '
            'stroke-width="1.5" stroke-dasharray="6 4" fill="none" marker-end="url(#arg)"/>'
        )
        out.append(
            f'<text x="26" y="{(low + mid) / 2:.0f}" font-size="10" fill="#2e7d32" '
            f'text-anchor="middle" transform="rotate(-90 26 {(low + mid) / 2:.0f})">'
            f"→ S{target} (clean return)</text>"
        )
    # footer: one line (ruled 2026-10-31; no deliverable branding in an open-source repo)
    foot_y = y + 20
    foot = [FOOTER]
    out.append(
        f'<line x1="20" y1="{foot_y}" x2="{W - 20}" y2="{foot_y}" stroke="#c5cae9" '
        'stroke-width="0.8"/>'
    )
    out.append(f'<text x="20" y="{foot_y + 18}" font-size="10" fill="#5a6478">{esc(FOOTER)}</text>')
    height = max(1200, foot_y + 18 + len(foot) * 14 + 40)
    svg = "\n".join(out)
    return TEMPLATE.format(
        marker=MARKER,
        title=esc(f"{seq_name} — {process} GRAFCET"),
        subtitle=esc(
            f"OpenFactory demo scene `{scene}` · IEC 60848 GRAFCET Notation · Source: "
            f"{source['file']}"
        ),
        w=W,
        h=height,
        svg=svg,
    )


TEMPLATE = """<!DOCTYPE html>
<!-- {marker}. Generated by tools/grafcet_from_st.py, following the /grafcet-chart-creator
     method, from the OpenPLC program; HIS Controls PE
     review required before any client use. OpenFactory is a training and demo tool, not a
     safety-rated simulation. -->
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  body {{ font-family: Arial, sans-serif; margin: 16px; background: #ffffff; color: #1a2a4a; }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }}
  .subtitle {{ font-size: 12px; color: #5a6478; margin-bottom: 6px; }}
  .review {{ font-size: 12px; color: #7f1010; background: #fff0f0; border: 1px solid #c0392b;
            padding: 4px 8px; display: inline-block; margin-bottom: 8px; }}
  .legend {{ font-size: 11px; display: flex; flex-wrap: wrap; gap: 14px; margin: 6px 0; }}
  .legend span {{ display: inline-flex; align-items: center; gap: 5px; }}
  .sw {{ width: 18px; height: 12px; border: 2px solid #1d6fb8; border-radius: 3px; }}
  .zoom-bar {{ font-size: 12px; margin: 6px 0; }}
  .zoom-bar button {{ font-size: 12px; padding: 1px 8px; }}
  .svg-wrap {{ overflow: auto; border: 1px solid #c5cae9; }}
  .print-note {{ font-size: 10px; color: #5a6478; margin-top: 6px; }}
  @media print {{ .zoom-bar, .print-note {{ display: none; }} }}
</style>
</head>
<body>
<h1>{title}</h1>
<div class="subtitle">{subtitle}</div>
<div class="review">{marker}: a generated engineering reference, not a PLC program. Not safety-rated.</div>
<div class="legend">
  <span><span class="sw" style="background:#dbeafe;border-width:4px;border-style:double"></span>Initial step</span>
  <span><span class="sw" style="background:#fdfefe"></span>Normal step</span>
  <span><span class="sw" style="background:#ecf7ee;border-color:#2e7d32"></span>Running / production</span>
  <span><span class="sw" style="background:#fff8e1;border-color:#e07b00"></span>Transient / caution</span>
  <span><svg width="28" height="10"><line x1="2" y1="5" x2="26" y2="5" stroke="#2d3748" stroke-width="6"/></svg>Transition</span>
  <span><span class="sw" style="background:#f8f9fa;border:1px solid #c5cae9;border-radius:0"></span>Action block</span>
  <span><svg width="28" height="10"><line x1="2" y1="5" x2="26" y2="5" stroke="#2e7d32" stroke-width="1.5" stroke-dasharray="6 4"/></svg>Clean return</span>
</div>
<div class="zoom-bar">Zoom <button onclick="zoomBy(-10)">−</button> <span id="zoom-pct">100%</span>
  <button onclick="zoomBy(10)">+</button> <button onclick="setZoom(100)">Reset</button></div>
<div class="svg-wrap">
<svg id="grafcet" xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}"
     font-family="Arial, sans-serif">
<defs>
  <marker id="ar" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
    <path d="M0 0 L8 4 L0 8 z" fill="#2d3748"/></marker>
  <marker id="arg" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
    <path d="M0 0 L8 4 L0 8 z" fill="#2e7d32"/></marker>
</defs>
<rect x="0" y="0" width="{w}" height="{h}" fill="white"/>
<rect x="0" y="0" width="{w}" height="40" fill="#f0f4fb"/>
<text x="{half}" y="26" font-size="14" font-weight="600" fill="#1a2a4a" text-anchor="middle">Main sequence (partial GRAFCETs run concurrently; every initial step is active at start)</text>
{svg}
</svg>
</div>
<div class="print-note">Print on A3 portrait at 100 % zoom.</div>
<script>
  let zoom = 100;
  const svg = document.getElementById("grafcet");
  const baseW = {w}, baseH = {h};
  function setZoom(pct) {{
    zoom = Math.max(50, Math.min(200, pct));
    svg.setAttribute("width", baseW * zoom / 100);
    svg.setAttribute("height", baseH * zoom / 100);
    document.getElementById("zoom-pct").textContent = zoom + "%";
  }}
  function zoomBy(d) {{ setZoom(zoom + d); }}
</script>
</body>
</html>
"""
TEMPLATE = TEMPLATE.replace("{half}", str(W // 2))


def belts_and_feed(
    belts: list[str],
    feeds: list[str],
    extra: list[str] | None = None,
    s0: int = 0,
    s1: int = 10,
    t0: int = 1,
    rung: str = "",
) -> Graph:
    dis = [b.replace("_DO", "_DI") for b in belts]
    pairs = [" AND ".join(dis[i : i + 2]) for i in range(0, len(dis), 2)]
    timer_in = [f"    IN = {pairs[0]}", *(f"         AND {p}" for p in pairs[1:])]
    cond = [f"NOT ({pairs[0]}", *(f"AND {p}" for p in pairs[1:])]
    cond[-1] += ")"
    return Graph(
        "G1 — Belts and box feed (feed only on proven belt running feedback)",
        [
            Step(
                s0,
                "Belts starting",
                [
                    *(f"{b} := TRUE" for b in belts),
                    *(extra or []),
                    "BELTS_OK_TMR: TON, PT 1 s",
                    *timer_in,
                    *(f"{f} := FALSE" for f in feeds),
                    f"# {rung}",
                ],
                initial=True,
            ),
            Trans(
                f"T{t0}",
                ["BELTS_OK_TMR.Q"],
                note="every belt's running feedback held 1 s (BELTS_OK_DELAY)",
            ),
            Step(
                s1,
                "Feeding boxes",
                [
                    *(f"{b} := TRUE" for b in belts),
                    *(extra or []),
                    *(f"{f} := TRUE" for f in feeds),
                    "# The feed stops the scan a belt stops reporting running",
                ],
                kind="running",
            ),
            Trans(f"T{t0 + 1}", cond, note="BELTS_OK_TMR resets", target=s0),
        ],
    )


SCENES = {}

SCENES["conveyor-start-stop"] = dict(
    program="start_stop",
    seq="Start_Stop",
    process="Conveyor start/stop with seal-in",
    graphs=[
        Graph(
            "G1 — Line start/stop (RUN_LATCH seal-in)",
            [
                Step(
                    0,
                    "Stopped",
                    [
                        "CONV_01_RUN_DO := FALSE",
                        "EMT_01_EN_DO := FALSE",
                        "PL_RUN_DO := CONV_01_RUN_DI",
                        "RUN_OK := SS_01_RUN_DI AND NOT SS_01_OFF_DI",
                        "          AND PB_STOP_DI   (PB_STOP is NC)",
                        "# ST sections 1-3, 5",
                    ],
                    initial=True,
                ),
                Trans(
                    "T1",
                    ["R_TRIG(SS_01_RUN_DI) AND RUN_OK"],
                    note="a turn to RUN (also RUN at first scan); stop released",
                ),
                Step(
                    10,
                    "Belt starting",
                    [
                        "CONV_01_RUN_DO := TRUE   (RUN_LATCH)",
                        "BELT_OK_TMR: TON, PT 1 s, IN = CONV_01_RUN_DI",
                        "EMT_01_EN_DO := FALSE",
                        "PL_RUN_DO := CONV_01_RUN_DI",
                        "# ST sections 3-5",
                    ],
                ),
                Trans("T2", ["BELT_OK_TMR.Q"], note="running feedback held 1 s (BELT_OK_DELAY)"),
                Step(
                    20,
                    "Running, feeding boxes",
                    [
                        "CONV_01_RUN_DO := TRUE   (RUN_LATCH)",
                        "EMT_01_EN_DO := TRUE",
                        "PL_RUN_DO := CONV_01_RUN_DI",
                        "# ST sections 3-5",
                    ],
                    kind="running",
                ),
                Trans(
                    "T3",
                    ["NOT RUN_OK"],
                    note="stop pressed, selector OFF, or both selector contacts",
                    target=0,
                ),
                Trans("T4", ["NOT RUN_OK"], note="stop during the start", source=10, target=0),
            ],
        )
    ],
)

SCENES["sorting-by-height"] = dict(
    program="sorter",
    seq="Sorter",
    process="Sort tall boxes by height",
    graphs=[
        belts_and_feed(
            ["CONV_01_RUN_DO", "CONV_02_RUN_DO"],
            ["EMT_01_EN_DO"],
            ["CONV_01_SPD_AO, CONV_02_SPD_AO := 32767"],
            rung="ST sections 1-2",
        ),
        Graph(
            "G2 — Tall box tracking (a ring of 4 slots, one per box in flight)",
            [
                Step(
                    20,
                    "Waiting for a tall box",
                    [
                        "PE_01_RISE: R_TRIG, CLK = PE_01_DI",
                        "DIVERT_TMR_n: TON, PT 1.98 s, IN = DIVERT_PEND_n",
                        "   (n = 0..3, each slot's eye-to-plate travel)",
                        "# ST sections 3, 3a",
                    ],
                    initial=True,
                ),
                Trans("T3", ["PE_01_RISE.Q"], note="a tall box breaks the beam"),
                Step(
                    21,
                    "Slot assigned",
                    [
                        "DIVERT_OVERRUN := DIVERT_OVERRUN OR DIVERT_PEND_n",
                        "DIVERT_PEND_n := TRUE   (n = NEXT_SLOT)",
                        "NEXT_SLOT := (NEXT_SLOT + 1) MOD 4",
                        "# One scan. DIVERT_OVERRUN latches if a 5th box finds",
                        "# its slot still busy (the box would be lost)",
                        "# ST section 3",
                    ],
                    kind="warning",
                ),
                Trans("T4", ["= 1"], note="unconditional: next scan", target=20),
            ],
        ),
        Graph(
            "G3 — Divert pusher",
            [
                Step(
                    30,
                    "Pusher retracted",
                    [
                        "CYL_01_EXT_DO := PUSH_CMD   (FALSE)",
                        "# ST sections 3a, 4",
                    ],
                    initial=True,
                ),
                Trans(
                    "T5",
                    ["DIVERT_TMR_0.Q OR ... OR DIVERT_TMR_3.Q"],
                    note="a box is due at the plate; its slot clears (DIVERT_PEND_n := FALSE)",
                ),
                Step(
                    31,
                    "Extend pusher",
                    [
                        "PUSH_CMD := TRUE",
                        "CYL_01_EXT_DO := PUSH_CMD   (TRUE)",
                        "# ST sections 3a, 4",
                    ],
                    kind="running",
                ),
                Trans(
                    "T6",
                    ["CYL_01_EXT_DI"],
                    note="extended limit: PUSH_CMD := FALSE, retract",
                    target=30,
                ),
            ],
        ),
    ],
)

SCENES["sorting-by-color"] = dict(
    program="color_sort",
    seq="Color_Sorter",
    process="Divert red boxes to the side lane",
    graphs=[
        belts_and_feed(
            ["CONV_01_RUN_DO", "CONV_RED_RUN_DO"], ["EMT_01_EN_DO"], rung="ST section 1"
        ),
        Graph(
            "G2 — Red box tracking (sensor to arm)",
            [
                Step(
                    20,
                    "Waiting for a red box",
                    [
                        "RED_RISE: R_TRIG, CLK = CS_01_RED_DI",
                        "# ST section 2",
                    ],
                    initial=True,
                ),
                Trans("T3", ["RED_RISE.Q"], note="a red box under the color sensor"),
                Step(
                    21,
                    "Red box travelling",
                    [
                        "PENDING := TRUE",
                        "EXTEND_TMR: TON, PT 1.5 s, IN = PENDING",
                        "# A second red box within 1.5 s rides on this timer",
                        "# ST section 2",
                    ],
                ),
                Trans(
                    "T4",
                    ["EXTEND_TMR.Q"],
                    note="0.75 m past the sensor: PENDING := FALSE, RED_OUT := RED_OUT + 1",
                    target=20,
                ),
            ],
        ),
        Graph(
            "G3 — Diverter arm",
            [
                Step(
                    30,
                    "Arm retracted",
                    [
                        "DIV_01_EXT_DO := FALSE   (RED_OUT = 0)",
                        "LANE_RISE: R_TRIG, CLK = PE_RED_DI",
                        "# ST section 3",
                    ],
                    initial=True,
                ),
                Trans(
                    "T5", ["RED_OUT > 0"], note="a red box is between the extend point and the lane"
                ),
                Step(
                    31,
                    "Arm extended",
                    [
                        "DIV_01_EXT_DO := TRUE   (RED_OUT > 0)",
                        "On LANE_RISE.Q: RED_OUT := RED_OUT - 1",
                        "# ST section 3",
                    ],
                    kind="running",
                ),
                Trans("T6", ["RED_OUT = 0"], note="the last red box reached PE_RED", target=30),
            ],
        ),
    ],
)

SCENES["buffer-and-release"] = dict(
    program="buffer_release",
    seq="Buffer",
    process="Accumulate and release batches of three",
    graphs=[
        belts_and_feed(
            ["CONV_BUF_RUN_DO", "CONV_OUT_RUN_DO"], ["EMT_01_EN_DO"], rung="ST sections 1-2"
        ),
        Graph(
            "G2 — Batch release",
            [
                Step(
                    20,
                    "Accumulating",
                    [
                        "STP_01_RAISE_DO := TRUE   (blade up, NOT RELEASING)",
                        "FULL_TMR: TON, PT 1.5 s, IN = PE_FULL_DI",
                        "PAUSE_TMR: TON, PT 2 s, IN = STP_01_UP_DI",
                        "# ST sections 3-4",
                    ],
                    initial=True,
                ),
                Trans(
                    "T3",
                    ["FULL_TMR.Q AND PAUSE_TMR.Q"],
                    note="three queued; blade up 2 s since the last batch",
                ),
                Step(
                    21,
                    "Releasing a batch",
                    [
                        "RELEASING := TRUE; RELEASED := 0 (on entry)",
                        "STP_01_RAISE_DO := FALSE   (blade down)",
                        "PE_REL_RISE: R_TRIG, CLK = PE_REL_DI",
                        "On PE_REL_RISE.Q: RELEASED := RELEASED + 1",
                        "# ST sections 3-4",
                    ],
                    kind="running",
                ),
                Trans(
                    "T4",
                    ["RELEASED >= BATCH"],
                    note="BATCH = 3: RELEASING := FALSE, blade up",
                    target=20,
                ),
            ],
        ),
    ],
)

SCENES["two-lane-merge"] = dict(
    program="merge",
    seq="Merge",
    process="Merge two lanes through a turntable",
    graphs=[
        belts_and_feed(
            ["CONV_A_RUN_DO", "CONV_B_RUN_DO", "CONV_OUT_RUN_DO"],
            ["EMT_A_EN_DO", "EMT_B_EN_DO"],
            s0=100,
            s1=110,
            t0=1,
            rung="ST section 1",
        ),
        Graph(
            "G2 — Merge sequence (step numbers are the program's STATE values)",
            [
                Step(
                    0,
                    "Idle at 0°",
                    [
                        "STP_A_REL_DO := REL_A   (pulse 0.5 s)",
                        "STP_B_REL_DO := REL_B   (pulse 0.5 s)",
                        "TT_01_TURN_DO := FALSE",
                        "TT_01_RUN_DO := FALSE",
                        "# ST section 2",
                    ],
                    initial=True,
                ),
                Trans(
                    "T3",
                    ["TT_01_AT0_DI AND PE_A_DI", "AND (NOT PE_B_DI OR LAST_B)"],
                    note="lane A waits; alternate when both wait",
                ),
                Step(
                    10,
                    "Lane A passes straight",
                    [
                        "REL_A := TRUE (on entry): STP_A_REL_DO 0.5 s",
                        "SEEN := FALSE; LAST_B := FALSE (on entry)",
                        "TT_01_RUN_DO := TRUE",
                        "If PE_TTA_DI: SEEN := TRUE",
                        "# ST sections 2-3",
                    ],
                    kind="running",
                ),
                Trans("T4", ["SEEN AND NOT PE_TTA_DI"], note="the box came and went", target=0),
                Trans(
                    "T5",
                    ["TT_01_AT0_DI AND PE_B_DI", "AND (NOT PE_A_DI OR NOT LAST_B)"],
                    note="lane B served (T3 not true)",
                    source=0,
                ),
                Step(
                    20,
                    "Turn out to lane B",
                    [
                        "LAST_B := TRUE (on entry)",
                        "TT_01_TURN_DO := TRUE",
                        "TT_01_RUN_DO := FALSE",
                        "# ST sections 2, 4",
                    ],
                    kind="warning",
                ),
                Trans("T6", ["TT_01_AT90_DI"]),
                Step(
                    21,
                    "Load from lane B",
                    [
                        "REL_B := TRUE (on entry): STP_B_REL_DO 0.5 s",
                        "TT_01_TURN_DO := TRUE",
                        "TT_01_RUN_DO := TRUE",
                        "# ST section 4",
                    ],
                    kind="running",
                ),
                Trans("T7", ["PE_TTB_DI"], note="the box is centred on the deck"),
                Step(
                    22,
                    "Turn back to 0°",
                    [
                        "TT_01_TURN_DO := FALSE",
                        "TT_01_RUN_DO := FALSE",
                        "# ST section 4",
                    ],
                    kind="warning",
                ),
                Trans("T8", ["TT_01_AT0_DI"]),
                Step(
                    23,
                    "Lane B box passes",
                    [
                        "SEEN := FALSE (on entry)",
                        "TT_01_RUN_DO := TRUE",
                        "If PE_TTA_DI: SEEN := TRUE",
                        "# ST section 4",
                    ],
                    kind="running",
                ),
                Trans("T9", ["SEEN AND NOT PE_TTA_DI"], note="the box came and went", target=0),
            ],
        ),
    ],
)

SCENES["palletizer-lite"] = dict(
    program="palletizer",
    seq="Palletizer",
    process="Pick and place boxes into layers of four",
    graphs=[
        belts_and_feed(
            ["CONV_IN_RUN_DO", "CONV_LYR_RUN_DO"],
            ["EMT_01_EN_DO"],
            ["STP_IN_RAISE_DO := TRUE"],
            s0=100,
            s1=110,
            t0=1,
            rung="ST section 1",
        ),
        Graph(
            "G2 — Pick and place (step numbers are the program's PICK_STEP values)",
            [
                Step(
                    0,
                    "Waiting at the pick side",
                    [
                        "PP_01_LOWER_DO, PP_01_GRIP_DO := FALSE",
                        "PP_01_TRAV_DO := FALSE",
                        "# ST section 2",
                    ],
                    initial=True,
                ),
                Trans(
                    "T3",
                    ["PE_PICK_DI AND PP_01_AT_PICK_DI", "AND PLACED < 4 AND NOT X210"],
                    note="X210: G3 step 210 (layer leaving) is active",
                ),
                Step(
                    1,
                    "Lower to the box",
                    ["PP_01_LOWER_DO := TRUE", "# ST section 2 (CASE PICK_STEP)"],
                ),
                Trans("T4", ["PP_01_DOWN_DI"]),
                Step(
                    2,
                    "Grip",
                    [
                        "PP_01_LOWER_DO := TRUE",
                        "PP_01_GRIP_DO := TRUE",
                        "# ST section 2 (CASE PICK_STEP)",
                    ],
                ),
                Trans("T5", ["PP_01_HOLD_DI"]),
                Step(
                    3,
                    "Rise with the box",
                    [
                        "PP_01_GRIP_DO := TRUE",
                        "RISE_TMR: TON, PT 0.4 s",
                        "# ST section 2 (CASE PICK_STEP)",
                    ],
                ),
                Trans("T6", ["RISE_TMR.Q"], note="pad up (lower_time_s 0.3 + margin)"),
                Step(
                    4,
                    "Travel to the place side",
                    [
                        "PP_01_GRIP_DO := TRUE",
                        "PP_01_TRAV_DO := TRUE",
                        "# ST section 2 (CASE PICK_STEP)",
                    ],
                    kind="running",
                ),
                Trans("T7", ["PP_01_AT_PLACE_DI"]),
                Step(
                    5,
                    "Lower onto the layer belt",
                    [
                        "PP_01_LOWER_DO := TRUE",
                        "PP_01_GRIP_DO := TRUE",
                        "PP_01_TRAV_DO := TRUE",
                        "# ST section 2 (CASE PICK_STEP)",
                    ],
                ),
                Trans("T8", ["PP_01_DOWN_DI"]),
                Step(
                    6,
                    "Release",
                    [
                        "PP_01_LOWER_DO := TRUE",
                        "PP_01_TRAV_DO := TRUE",
                        "SETTLE_TMR: TON, PT 0.2 s, IN = NOT PP_01_HOLD_DI",
                        "# ST section 2 (CASE PICK_STEP)",
                    ],
                ),
                Trans("T9", ["SETTLE_TMR.Q"], note="the box has settled: PLACED := PLACED + 1"),
                Step(
                    7,
                    "Rise empty",
                    [
                        "PP_01_TRAV_DO := TRUE",
                        "RISE_TMR: TON, PT 0.4 s",
                        "# ST section 2 (CASE PICK_STEP)",
                    ],
                ),
                Trans("T10", ["RISE_TMR.Q"]),
                Step(
                    8,
                    "Return to the pick side",
                    [
                        "PP_01_LOWER_DO, PP_01_GRIP_DO := FALSE",
                        "PP_01_TRAV_DO := FALSE",
                        "# ST section 2 (CASE PICK_STEP)",
                    ],
                ),
                Trans("T11", ["PP_01_AT_PICK_DI"], target=0),
            ],
        ),
        Graph(
            "G3 — Layer out",
            [
                Step(
                    200,
                    "Forming a layer",
                    [
                        "STP_LYR_RAISE_DO := TRUE   (blade up)",
                        "ROW_TMR: TON, PT 1 s, IN = PE_ROW_DI",
                        "# ST section 3",
                    ],
                    initial=True,
                ),
                Trans("T12", ["ROW_TMR.Q AND PLACED >= 4"], note="a standing row of four"),
                Step(
                    210,
                    "Layer leaving",
                    [
                        "LAYER_OUT_ON := TRUE",
                        "STP_LYR_RAISE_DO := FALSE   (blade down)",
                        "LAYER_TMR: TON, PT 3 s",
                        "# ST section 3",
                    ],
                    kind="running",
                ),
                Trans(
                    "T13", ["LAYER_TMR.Q"], note="1.2 m cleared at 0.5 m/s: PLACED := 0", target=200
                ),
            ],
        ),
    ],
)


def chart(scene: str) -> str:
    """The HTML for ``scene``."""
    spec = SCENES[scene]
    st = REPO / "examples" / "openplc" / f"{scene}.st"
    lines = len(st.read_text(encoding="utf-8").splitlines())
    source = {
        "file": f"examples/openplc/{scene}.st",
        "program": spec["program"],
        "lines": f"1-{lines}",
    }
    return render(scene, spec["seq"], spec["process"], spec["graphs"], source)


def write(scene: str, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(chart(scene), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Write a scene's GRAFCET chart (PLAN 2.8).")
    parser.add_argument("scene", nargs="?", help=f"one of: {', '.join(SCENES)}")
    parser.add_argument("out", nargs="?", type=Path, help="output .html")
    parser.add_argument("--all", type=Path, metavar="KITS", help="write every scene's chart")
    args = parser.parse_args(argv)
    if args.all:
        for scene in SCENES:
            write(scene, args.all / scene / f"{scene}-grafcet.html")
        return 0
    if args.scene not in SCENES or args.out is None:
        parser.error("give a known scene and an output file, or --all KITS")
    write(args.scene, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
