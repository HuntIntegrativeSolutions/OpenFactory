"""PLAN 5.5 Layout from topology (ruled 2026-11-19). The sample's takeoff with its positions
removed (a copy, made here; the shipped sample keeps its drawn positions) lays out with no
overlap finding. Layout is Python, deterministic byte for byte, never moves what a drawing
placed, marks every position it gives, puts equipment in flow order and routes pipes at right
angles through no body."""

from __future__ import annotations

import math
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from openfactory import takeoff as tk
from openfactory.core.loader import load
from openfactory.layout import LINE_Y_M, TANK_Y_M, relayout_scene
from openfactory.lint import lint_scene
from openfactory.scaffold import scaffold

REPO = Path(__file__).resolve().parents[2]
SAMPLE = REPO / "docs" / "samples" / "wash-skid" / "wash-skid.takeoff.yaml"
OVERLAPS = {"W204", "W205", "W212", "W213", "W214", "W217"}


def stripped(tmp_path: Path, keep: tuple[str, ...] = ()) -> Path:
    """A copy of the sample's takeoff with every layout, mount and at removed (but ``keep``'s)
    and its checked mark dropped (its rows changed)."""
    data = yaml.safe_load(SAMPLE.read_text(encoding="utf-8"))
    for e in data["equipment"]:
        if e["tag"] not in keep:
            for key in ("layout", "mount", "at"):
                e.pop(key, None)
    data.pop("checked", None)
    path = tmp_path / "bare.takeoff.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def write_scene(tmp_path: Path, takeoff: Path, name: str = "laid.yaml") -> tuple[Path, object]:
    result = scaffold(tk.load(takeoff))
    out = tmp_path / name
    out.write_text(result.text, encoding="utf-8")
    return out, result


@pytest.fixture
def laid(tmp_path):  # noqa: ANN201
    return write_scene(tmp_path, stripped(tmp_path))


def test_the_sample_with_no_positions_lays_out_with_no_overlap_finding(laid):
    """Done-when (the screenshot, top view and default view, is looked at and named in PLAN)."""
    path, result = laid
    findings = lint_scene(path)
    print("\n".join(str(f) for f in findings if f.code not in ("W201", "W202")))
    assert not [f for f in findings if f.level == "error"]
    assert not [f for f in findings if f.code in OVERLAPS]
    tally = {k: len(v) for k, v in result.tally().items()}
    assert tally["laid out"] == 12 and tally["stand-in"] == 3  # AG-101 is laid out as a stand-in
    loaded = load(path)  # it builds, runs a tick
    loaded.step(0.02)


def test_it_is_deterministic_byte_for_byte_in_python(tmp_path):
    """Ruling 1: two processes with different hash seeds write the same bytes; no browser."""
    bare = stripped(tmp_path)
    outs = []
    for seed in ("1", "4242"):
        out = tmp_path / f"seed-{seed}.yaml"
        done = subprocess.run(
            [sys.executable, "-m", "openfactory", "layout", str(bare), "-o", str(out)],
            capture_output=True, text=True, env={**os.environ, "PYTHONHASHSEED": seed},
        )  # fmt: skip
        assert done.returncode == 3, done.stderr  # rows to resolve, as scaffold says
        outs.append(out.read_bytes())
    assert outs[0] == outs[1]
    assert "playwright" not in (REPO / "src" / "openfactory" / "layout.py").read_text("utf-8")


def test_it_never_moves_what_a_drawing_placed(tmp_path):
    """Ruling 2: rows with a layout, mount or at stay where the drawing put them; only the rest
    are laid out, past the drawn ones."""
    keep = ("SUP-101", "LV-101", "TK-101", "CV-201", "PE-201", "EMT-201")
    path, result = write_scene(tmp_path, stripped(tmp_path, keep))
    model = tk.load(SAMPLE).model
    scene = {c.id: c for c in load(path).scene.components}
    drawn_x = []
    for e in model.equipment:
        sid = tk.scene_id(e)
        if e.tag in keep:
            pos, heading = tk.position_of(e, model)
            assert scene[sid].position == pytest.approx(list(pos), abs=1e-4), e.tag
            assert not scene[sid].laid_out, e.tag
            drawn_x.append(pos[0])
        else:
            comp = scene.get(sid) or scene[f"{sid}_BODY"]
            assert comp.laid_out, e.tag
    assert not [f for f in lint_scene(path) if f.code in OVERLAPS]
    assert scene["P_101"].position[0] > max(drawn_x)  # laid out past the drawn items
    # and a scene: only what is marked moves, every other byte stays
    text = path.read_text(encoding="utf-8")
    again, ids, _ = relayout_scene(text, path.parent)
    assert again == text  # laying out a laid-out scene again changes nothing
    shifted = text.replace(
        f"position: {list(scene['P_101'].position)}".replace("'", ""), "position: [9.0, 0.3, 9.0]"
    )
    assert shifted != text
    back, ids, _ = relayout_scene(shifted, path.parent)
    assert back == text and "P_101" in ids and "TK_101" not in ids


def test_every_position_it_gives_is_marked_in_the_scene_and_the_report(laid):
    """Ruling 3."""
    path, result = laid
    text = path.read_text(encoding="utf-8")
    scene = load(path).scene
    for c in scene.components:
        assert c.laid_out, c.id
    for block in text.split("\n  - id: ")[1:]:
        head = block.split("\n", 1)[0]
        assert "LAID OUT by openfactory layout (PLAN 5.5), not from a drawing" in block, head
        assert "laid_out: true" in block, head
    report = result.report()
    print(report)
    assert "13 laid out by openfactory layout, not from a drawing (EQ-1, EQ-2" in report
    assert "pipes, routed by openfactory layout (2): PIPE_101 (CN-2) routed" in report
    drawn = scaffold(tk.load(SAMPLE))
    drawn_scene = yaml.safe_load(drawn.text)
    marked = [c["id"] for c in drawn_scene["components"] if c.get("laid_out")]
    assert marked == ["PIPE_101", "PIPE_102"]  # the shipped sample: only the pipes it made
    assert "laid out" not in "".join(drawn.report().split("positions:")[1].split("\n")[0])


def test_equipment_in_flow_order_and_pipes_at_right_angles_through_no_body(laid):
    """Ruling 4: supplies first, drains last, along the connections; every run of a pipe, valve
    or pump is along one axis and passes through no body but its ends' (W217 says none)."""
    path, _ = laid
    loaded = load(path)
    comps = {c.id: c for c in loaded.components}
    xs = [comps[i].config.position[0] for i in ("SUP_101", "LV_101", "TK_101", "P_101", "DR_101")]
    assert xs == sorted(xs) and len(set(xs)) == 5
    assert comps["TK_101"].config.position[1] == TANK_Y_M
    assert comps["P_101"].config.position[1] == LINE_Y_M
    top = TANK_Y_M + 1.8
    assert comps["SUP_101"].config.position[1] == pytest.approx(top + 0.3)  # fills the top
    assert comps["DR_101"].config.position[1] == LINE_Y_M  # beside the pump that feeds it
    for c in loaded.components:
        pts = getattr(c, "points", None)
        if not pts:
            continue
        for a, b in zip(pts, pts[1:], strict=False):
            moved = [k for k in range(3) if abs(a[k] - b[k]) > 1e-6]
            assert len(moved) <= 1, (c.id, a, b)
    assert comps["PIPE_101"].config.path  # it bends: over and down into the tank's top
    assert not [f for f in lint_scene(path) if f.code == "W217"]


def test_a_device_mounted_on_a_laid_out_belt_follows_it_and_is_marked(tmp_path):
    path, _ = write_scene(tmp_path, stripped(tmp_path, keep=("PE-201", "CYL-201")))
    scene = {c.id: c for c in load(path).scene.components}
    belt, eye = scene["CV_201"], scene["PE_201"]
    assert belt.laid_out and eye.laid_out
    assert eye.position[0] == pytest.approx(belt.position[0] + 1.2, abs=1e-4)  # along 1200 mm
    assert "which layout placed" in path.read_text(encoding="utf-8")


def test_a_routed_pipes_length_is_its_drawn_length(laid):
    """Ruling 6: layout changes a pipe's length, and so its friction (the docs say so)."""
    path, _ = laid
    pipe = next(c for c in load(path).components if c.id == "PIPE_101")
    drawn = sum(math.dist(a, b) for a, b in zip(pipe.points, pipe.points[1:], strict=False))
    assert pipe.length == pytest.approx(drawn) and pipe.length > 1.0
    docs = (REPO / "docs" / "layout.md").read_text(encoding="utf-8")
    assert "drawn length" in docs and "friction" in docs
