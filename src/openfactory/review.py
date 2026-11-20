"""``openfactory review`` (PLAN 5.6; design note §8): the scene over its layout drawing, and a
table of takeoff rows against scene components.

Behavior spec
-------------
:func:`review` takes a takeoff, the scene made from it and a layout drawing, and gives a table
and an overlay:

- **The table** lists every takeoff row against the scene: an equipment row is ``present`` when
  the scene has its component (a composite instance counts by its parts), else ``missing``; a
  connection row is ``present`` when the scene has the pipe, link end or conveyor connection
  scaffold made of it, ``missing`` when not, ``not made`` when scaffold could not make it; an
  I/O row is ``present`` when its component carries its scene tag. Every scene component no row
  accounts for is listed ``in the scene with no row``. A present equipment row says where its
  position comes from (the sheet's dimensions, a rail, ``at`` in scene metres, or laid out by
  ``openfactory layout``, never a dimension off a sheet) and, for a drawn one, how far the
  scene has it from where the takeoff puts it, in millimetres.
- **The overlay** is a page that draws the drawing **as an image only**: its bytes as a
  ``data:`` URL in an ``<img>``, drawn on a canvas, never inlined as markup, so nothing in a
  drawing file can run (ruled 2026-11-19). Only PNG and SVG are taken, by suffix and by their
  first bytes; any other type is refused by name; a URL is refused (nothing fetches). The page
  carries a content security policy that lets it load nothing but its own ``data:`` image. Over
  the drawing, the scene's top view: each component's body (:mod:`openfactory.core.footprints`)
  and each link's runs, placed through the sheet's ``frame`` (units, datum, rotation) and the
  picture's own registration, which the person gives: the datum's pixel and the pixels per
  sheet unit (the frame says nothing of pixels). Present: green; laid out: dashed grey; in the
  scene with no row: blue; a missing row: dashed red at the takeoff's position, with its tag.
- **Where it is written** (ruled 2026-11-19): beside the takeoff, or in the folder the person
  names; never into this repository by default. The overlay and its table are as private as
  the drawing.

Not a safety-rated simulation. OpenFactory is a training and demo tool.
"""

from __future__ import annotations

import base64
import html
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openfactory import takeoff as tk
from openfactory.core import footprints as fp
from openfactory.core.geometry import rotate_y

#: The largest drawing taken, bytes.
MAX_DRAWING_BYTES = 20_000_000
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
#: A URL, also as a path collapses it (``https:/host``); a drive letter (``C:/``) is not one.
URL = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]+:/")
#: Where this package's own repository is (a source checkout), for the default-location rule.
REPO = Path(__file__).resolve().parents[2]

#: The overlay page's policy: it loads nothing but its own ``data:`` image (ruled 2026-11-19).
POLICY = "default-src 'none'; img-src data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'"
COLORS = {
    "present": "#2e7d32",
    "laid out": "#6b7280",
    "no row": "#1d6fb8",
    "missing": "#c0392b",
}


class DrawingRefused(ValueError):
    """The drawing is not a PNG or SVG file this command can load as an image."""


def drawing_data_url(path: Path) -> str:
    """The drawing's bytes as a ``data:`` URL for an ``<img>``: PNG or SVG only, by suffix and
    by first bytes; anything else refused by name."""
    text = str(path)
    if URL.match(text):
        raise DrawingRefused(f"{text}: a URL; review reads a file you name and fetches nothing")
    suffix = path.suffix.lower()
    if suffix not in (".png", ".svg"):
        named = suffix.lstrip(".").upper() or "a file with no suffix"
        raise DrawingRefused(
            f"{path.name}: {named} is not a drawing review loads; give a PNG or SVG"
        )
    data = path.read_bytes()
    if len(data) > MAX_DRAWING_BYTES:
        raise DrawingRefused(f"{path.name}: over {MAX_DRAWING_BYTES // 1_000_000} MB")
    if suffix == ".png":
        if not data.startswith(PNG_MAGIC):
            raise DrawingRefused(f"{path.name}: named .png, but it is not a PNG file")
        mime = "image/png"
    else:
        head = data[:4096].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
        if not (head.startswith((b"<?xml", b"<svg", b"<!--")) and b"<svg" in head):
            raise DrawingRefused(f"{path.name}: named .svg, but it is not an SVG file")
        if not _sized(head):  # review fix 2e: no silent fallback canvas
            raise DrawingRefused(
                f"{path.name}: the svg element has no width and height of its own (in px, mm or "
                "another absolute unit), so a browser would draw it on a fallback canvas; give it "
                "a width and height, or export the sheet as a PNG"
            )
        mime = "image/svg+xml"
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


#: An absolute length an SVG's own width or height may be given in (no percentages).
SVG_LENGTH = re.compile(rb"^\s*[0-9.]+(e[+-]?[0-9]+)?\s*(px|mm|cm|in|pt|pc)?\s*$")


def _sized(head: bytes) -> bool:
    """Whether the opening ``<svg`` tag gives an absolute width and height."""
    tag = re.search(rb"<svg\b[^>]*>", head)
    if tag is None:
        return False
    attrs = dict(re.findall(rb'\s(width|height)\s*=\s*["\']([^"\']*)["\']', tag.group(0)))
    return all(SVG_LENGTH.match(attrs.get(k, b"%")) for k in (b"width", b"height"))


@dataclass
class Registration:
    """Scene metres to the picture's pixels: the sheet's frame, then the person's datum pixel
    and pixels per sheet unit (sheet y up, picture y down)."""

    frame: Any
    datum_px: tuple[float, float]
    px_per_unit: float

    def sheet(self, x: float, z: float) -> tuple[float, float]:
        """Scene x, z to sheet units from the datum (x right, y up), as the drawing prints."""
        u = tk.unit(self.frame)
        dx, dz = x - self.frame.datum[0], z - self.frame.datum[2]
        lx, _, lz = rotate_y((dx, 0.0, dz), -self.frame.rotation_deg)
        return lx / u, -lz / u

    def px(self, x: float, z: float) -> tuple[float, float]:
        sx, sy = self.sheet(x, z)
        return (
            round(self.datum_px[0] + sx * self.px_per_unit, 2),
            round(self.datum_px[1] - sy * self.px_per_unit, 2),
        )

    def mm(self, pixels: float) -> float:
        return pixels / self.px_per_unit * tk.unit(self.frame) * 1000


@dataclass
class Row:
    row: str
    what: str
    scene: str
    status: str
    position: str = ""
    off_mm: float | None = None
    #: The scene's heading less the takeoff's, degrees in (-180, 180] (review fix 2b).
    off_deg: float | None = None


@dataclass
class Review:
    rows: list[Row]
    shapes: list[dict[str, Any]] = field(default_factory=list)
    sheet: str = ""
    scene_name: str = ""
    takeoff_name: str = ""

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.rows:
            out[r.status] = out.get(r.status, 0) + 1
        return out

    def table(self) -> str:
        def cell(text: str) -> str:
            return text.replace("|", "\\|").replace("`", "'")

        counts = ", ".join(f"{n} {k}" for k, n in self.counts().items())
        lines = [
            f"# Review: {self.scene_name} against {self.takeoff_name}",
            "",
            f"Every takeoff row against the scene, and every scene component no row accounts "
            f"for ({counts}). Laid out means placed by `openfactory layout`, not from a drawing. "
            "As private as the drawings: keep it with them.",
            "",
            "| Row | Takeoff | Scene | Status | Position | Off the takeoff |",
            "|---|---|---|---|---|---|",
        ]
        for r in self.rows:
            off = "" if r.off_mm is None else f"{r.off_mm:.0f} mm"
            if r.off_deg:
                off += f", {r.off_deg:.0f}°"
            lines.append(
                f"| {cell(r.row)} | {cell(r.what)} | {cell(r.scene)} | {r.status} | "
                f"{cell(r.position)} | {off} |"
            )
        return "\n".join(lines) + "\n"


def review(
    loaded: tk.Loaded, scene_path: Path, sheet: str | None, reg_px: tuple[float, float],
    px_per_unit: float,
) -> Review:  # fmt: skip
    """The table and the overlay's shapes for a takeoff, its scene and a layout sheet."""
    from openfactory.core.loader import load
    from openfactory.scaffold import plan_connections

    model = loaded.model
    if model is None:
        raise ValueError("the takeoff does not load: " + "; ".join(map(str, loaded.findings)))
    layouts = [d for d in model.drawings if d.kind == "layout" and d.frame is not None]
    if sheet is None:
        if len(layouts) != 1:
            names = ", ".join(d.id for d in layouts) or "none"
            raise ValueError(f"name the layout sheet with --sheet (layout sheets: {names})")
        drawing = layouts[0]
    else:
        drawing = next((d for d in layouts if d.id == sheet), None)
        if drawing is None:
            raise ValueError(f"{sheet} is not a layout sheet with a frame in the takeoff")
    reg = Registration(drawing.frame, reg_px, px_per_unit)
    plant = load(scene_path)
    scene = plant.scene
    comps = {c.id: c for c in plant.components}
    ids = {e.tag: tk.scene_id(e) for e in model.equipment}
    accounted: set[str] = set()
    rows: list[Row] = []
    shapes: list[dict[str, Any]] = []

    def parts_of(sid: str) -> list[Any]:
        if sid in comps:
            return [comps[sid]]
        return [c for c in plant.components if scene.composite_of(c.id) == sid]

    def shape(comp: Any, kind: str, label: str) -> dict[str, Any]:
        cfg = comp.config
        out: dict[str, Any] = {"id": comp.id, "label": label, "kind": kind}
        if comp.type == "tank" and getattr(cfg, "shape", "cylinder") != "box":
            cx, cy = reg.px(cfg.position[0], cfg.position[2])
            out["circle"] = [cx, cy, round(cfg.diameter / 2 / tk.unit(reg.frame) * px_per_unit, 2)]
        else:
            out["polys"] = [[list(reg.px(x, z)) for x, z in poly]
                            for poly in fp.outline(cfg, comp.type)]  # fmt: skip
        points = getattr(comp, "points", None)
        if points:
            out["line"] = [list(reg.px(p[0], p[2])) for p in points]
        out["at"] = list(reg.px(cfg.position[0], cfg.position[2]))
        return out

    for e in model.equipment:
        sid = ids[e.tag]
        parts = parts_of(sid)
        drawn = e.layout is not None or e.mount is not None or e.at is not None
        if not parts:
            row = Row(e.row, f"{e.tag} ({e.type})", sid, "missing")
            if drawn:
                pos, heading = tk.position_of(
                    e.model_copy(update={"type": "photo_eye"}) if e.type == "unknown" and e.mount
                    else e, model)  # fmt: skip
                row.position = _source(e)
                shapes.append(_missing_shape(e, model, pos, heading, reg))
            else:
                row.position = "no position on any sheet"
            rows.append(row)
            continue
        accounted.add(sid)
        accounted.update(p.id for p in parts)
        laid = all(getattr(p.config, "laid_out", False) for p in parts)
        anchor = comps.get(sid) or parts[0]
        where = "laid out by openfactory layout, not from a drawing" if laid else _source(e)
        # review fix 2a: the row's id with another type is not the row (a stand-in, or anything,
        # stands for a row of unknown type)
        actual = anchor.type if sid in comps else scene.instance_type(sid)
        if e.type != "unknown" and actual != e.type:
            rows.append(Row(e.row, f"{e.tag} ({e.type})", f"{sid} ({actual})", "different type",
                            f"the takeoff says {e.type}, the scene has {actual}"))  # fmt: skip
            for p in parts:
                shapes.append(shape(p, "no row", f"{e.tag}?"))
            continue
        row = Row(e.row, f"{e.tag} ({e.type})", sid, "present", where)
        if drawn and not laid and sid in comps:
            pos, heading = tk.position_of(e, model)
            x, _, z = anchor.config.position
            row.off_mm = round(math.hypot(x - pos[0], z - pos[2]) * 1000, 1)
            if anchor.type not in fp.UNTURNED:  # review fix 2b: the heading too
                turn = (float(anchor.config.rotation_deg) - float(heading)) % 360.0
                row.off_deg = round(turn - 360.0 if turn > 180.0 else turn, 1) + 0.0
        rows.append(row)
        for p in parts:
            shapes.append(shape(p, "laid out" if laid else "present", e.tag))

    plan = plan_connections(model)
    pipe_of = {p["row"]: p for p in plan.pipes}
    joined = {(c.from_, c.to) for c in scene.connections}
    for c in model.connections:
        what = f"{c.from_} -> {c.to}" + (f" ({c.line})" if c.line else "")
        a, b = ids.get(c.from_.partition(".")[0]), ids.get(c.to.partition(".")[0])
        if c.row in pipe_of and pipe_of[c.row]["id"] not in comps and _joined(comps, a, c.to, ids):
            # review fix 2d: a link joined to its node with no pipe, its `to` naming the node
            rows.append(Row(c.row, what, f"{a} -> {b}", "present", "joined with no pipe"))
            continue
        if c.row in pipe_of:
            pid = pipe_of[c.row]["id"]
            ok = pid in comps
            if ok:
                accounted.add(pid)
                laid = bool(getattr(comps[pid].config, "laid_out", False))
                where = "routed by openfactory layout" if laid else "as drawn in the scene"
                shapes.append(shape(comps[pid], "laid out" if laid else "present", c.row))
            rows.append(Row(c.row, what, pid, "present" if ok else "missing",
                            where if ok else ""))  # fmt: skip
            continue
        made = next((u for u in plan.unconnected if u[0].row == c.row), None)
        if made is not None:
            rows.append(Row(c.row, what, "", "not made", made[1]))
            continue
        ok = (a, b) in joined
        link = comps.get(b or "")
        if not ok and link is not None:
            ok = str(getattr(link.config, "from_", "")).partition(".")[0] == a
        rows.append(Row(c.row, what, f"{a} -> {b}", "present" if ok else "missing"))

    owners = {b.name: b.component_id for b in scene.tag_bindings()}
    ports = _ports(scene_path)
    by_tag = model.by_tag()
    for io in model.io:
        name = tk.scene_tag(io, by_tag)
        sid = ids.get(io.equipment, "")
        owner = owners.get(name)
        ok = owner is not None and (owner == sid or scene.composite_of(owner) == sid)
        what = f"{io.equipment}.{io.port} ({io.signal})"
        on = ports.get(name)
        if ok and on is not None and on[0] == sid and on[1] != io.port:
            # review fix 2c: present only on the row's own port (a composite's: its io name)
            rows.append(Row(io.row, what, name, "on another port",
                            f"on {sid}'s {on[1]}, not {io.port}"))  # fmt: skip
            continue
        rows.append(Row(io.row, what, name, "present" if ok else "missing"))

    for comp in plant.components:
        inst = scene.composite_of(comp.id)
        if comp.id in accounted or (inst and inst in accounted):
            continue
        rows.append(Row("-", "", inst or comp.id, "in the scene with no row"))
        shapes.append(shape(comp, "no row", inst or comp.id))
        if inst:
            accounted.add(inst)
    return Review(rows, shapes, drawing.id, scene_path.name, loaded.path.name)


def _joined(comps: dict[str, Any], link_id: str | None, to: str, ids: dict[str, str]) -> bool:
    """Whether the scene's link ``link_id`` names the connection row's ``to`` as its own ``to``."""
    link = comps.get(link_id or "")
    if link is None:
        return False
    tag, _, port = to.partition(".")
    want = ids.get(tag, "")
    got = str(getattr(link.config, "to", ""))
    return got == want or (bool(port) and got == f"{want}.{port}")


def _ports(scene_path: Path) -> dict[str, tuple[str, str]]:
    """Each scene tag's (component or composite instance id, port or io name), from the scene
    file's own ``io`` mappings."""
    import yaml

    try:
        raw = yaml.safe_load(scene_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    out: dict[str, tuple[str, str]] = {}
    for entry in raw.get("components") or []:
        if not isinstance(entry, dict) or not isinstance(entry.get("io"), dict):
            continue
        for port, value in entry["io"].items():
            tag = value.get("tag") if isinstance(value, dict) else value
            if isinstance(tag, str):
                out[tag] = (str(entry.get("id")), str(port))
    return out


def _source(e: Any) -> str:
    if e.layout is not None:
        return f"{e.layout.sheet} dimensions"
    if e.mount is not None:
        return f"{e.mount.conveyor}'s {e.mount.rail} rail" + (
            f" ({e.mount.sheet})" if e.mount.sheet else "")  # fmt: skip
    return "at, in scene metres (no sheet)"


def _missing_shape(e: Any, model: Any, pos: Any, heading: float, reg: Registration) -> dict:
    """A missing row drawn where its takeoff row puts it: its type's body, or a 0.3 m square."""
    from types import SimpleNamespace

    from openfactory.scaffold import row_config

    ids = {r.tag: tk.scene_id(r) for r in model.equipment}
    cfg = row_config(e, ids)
    cfg.position, cfg.rotation_deg = list(pos), heading
    kind = e.type if e.type in tk.COMPONENT_MODELS else "scenery"
    if kind == "scenery":
        cfg.size = [float(e.params.get(k, 0.3)) for k in ("length", "height", "width")]
    try:
        polys = fp.outline(cfg, kind)
    except (AttributeError, TypeError):
        polys = fp.outline(SimpleNamespace(position=list(pos), size=[0.3, 0.3, 0.3]), "scenery")
    if e.type == "tank" and getattr(cfg, "shape", "cylinder") != "box":
        cx, cy = reg.px(pos[0], pos[2])
        r = float(cfg.diameter) / 2 / tk.unit(reg.frame) * reg.px_per_unit
        return {"id": tk.scene_id(e), "label": f"MISSING {e.tag}", "kind": "missing",
                "circle": [cx, cy, round(r, 2)], "at": [cx, cy]}  # fmt: skip
    return {
        "id": tk.scene_id(e), "label": f"MISSING {e.tag}", "kind": "missing",
        "polys": [[list(reg.px(x, z)) for x, z in poly] for poly in polys],
        "at": list(reg.px(pos[0], pos[2])),
    }  # fmt: skip


def overlay_html(rev: Review, data_url: str) -> str:
    """The overlay page: the drawing as an image on a canvas, the top view drawn over it, and
    the table. It loads nothing: its policy allows only its own ``data:`` image."""
    payload = json.dumps({"shapes": rev.shapes, "colors": COLORS}, separators=(",", ":"))
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    title = html.escape(f"Review: {rev.scene_name} on {rev.sheet}")
    table = html.escape(rev.table())
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="{POLICY}">
<title>{title}</title>
<style>
body {{ margin: 0; background: #ffffff; color: #1a1a1a; font: 14px sans-serif; }}
#legend {{ padding: 8px 12px; }}
#legend span {{ margin-right: 16px; }}
canvas {{ display: block; }}
pre {{ padding: 8px 12px; white-space: pre-wrap; }}
</style>
</head>
<body>
<div id="legend">{title}.
<span style="color:{COLORS["present"]}">present</span>
<span style="color:{COLORS["laid out"]}">laid out (not from a drawing)</span>
<span style="color:{COLORS["no row"]}">in the scene with no row</span>
<span style="color:{COLORS["missing"]}">missing from the scene</span></div>
<canvas id="overlay"></canvas>
<pre id="table">{table}</pre>
<script>
"use strict";
const DATA = {payload};
const img = new Image();
img.onload = () => {{
  const canvas = document.getElementById("overlay");
  canvas.width = img.naturalWidth || 1400;
  canvas.height = img.naturalHeight || 1100;
  const g = canvas.getContext("2d");
  g.fillStyle = "#ffffff";
  g.fillRect(0, 0, canvas.width, canvas.height);
  g.drawImage(img, 0, 0, canvas.width, canvas.height);
  g.font = "bold 12px sans-serif";
  for (const s of DATA.shapes) {{
    const color = DATA.colors[s.kind];
    g.strokeStyle = color;
    g.fillStyle = color;
    g.lineWidth = s.kind === "missing" ? 3 : 2;
    g.setLineDash(s.kind === "missing" ? [8, 5] : s.kind === "laid out" ? [5, 4] : []);
    if (s.circle) {{
      g.beginPath();
      g.arc(s.circle[0], s.circle[1], s.circle[2], 0, 2 * Math.PI);
      g.stroke();
    }}
    for (const p of s.polys || []) {{
      g.beginPath();
      p.forEach(([x, y], i) => (i ? g.lineTo(x, y) : g.moveTo(x, y)));
      g.closePath();
      g.stroke();
    }}
    if (s.line) {{
      g.beginPath();
      s.line.forEach(([x, y], i) => (i ? g.lineTo(x, y) : g.moveTo(x, y)));
      g.stroke();
    }}
    if (s.kind === "missing") {{
      g.beginPath();
      g.arc(s.at[0], s.at[1], 18, 0, 2 * Math.PI);  // a ring round it, so a small item shows
      g.stroke();
    }}
    if (s.kind === "missing" || s.kind === "no row") {{
      g.setLineDash([]);
      g.fillText(s.label, s.at[0] + 8, s.at[1] - 8);
    }}
  }}
  window.__overlayReady = true;
}};
img.onerror = () => {{ window.__overlayReady = "the drawing did not load as an image"; }};
img.src = {json.dumps(data_url)};
</script>
</body>
</html>
"""


def default_dir(takeoff: Path) -> Path:
    """Beside the takeoff (ruled 2026-11-19)."""
    return takeoff.resolve().parent


def is_checkout(folder: Path) -> bool:
    """Whether ``folder`` is the root of an OpenFactory checkout: a git work tree holding this
    project's package (``src/openfactory``) or naming it in its ``pyproject.toml``."""
    if not (folder / ".git").exists():
        return False
    if (folder / "src" / "openfactory").is_dir():
        return True
    try:
        project = (folder / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return False
    return re.search(r'(?m)^name\s*=\s*"openfactory"\s*$', project) is not None


def in_repository(path: Path) -> bool:
    """Whether ``path`` is inside an OpenFactory checkout: this package's own, or any other the
    folder is in (review fix 2f, 2026-11-19). Only the folder's own ancestors are looked at, each
    for two names; nothing is listed or walked."""
    target = path.resolve()
    if (REPO / ".git").exists() and target.is_relative_to(REPO):
        return True
    return any(is_checkout(folder) for folder in (target, *target.parents))


def render_png(page_html: str, out: Path) -> None:
    """The overlay as a PNG, drawn by headless Chromium (Playwright, a development extra)."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1500, "height": 1200})
            page.set_content(page_html)
            page.wait_for_function("window.__overlayReady !== undefined", timeout=20_000)
            state = page.evaluate("window.__overlayReady")
            if state is not True:
                raise ValueError(str(state))
            page.locator("#overlay").screenshot(path=str(out))
        finally:
            browser.close()
