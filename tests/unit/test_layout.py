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


# --- review fixes, 2026-11-19 (second set) -----------------------------------------------------


def _cli(*args: str):  # noqa: ANN202
    from typer.testing import CliRunner

    from openfactory.cli import app

    return CliRunner().invoke(app, list(args))


def test_layout_scene_writes_only_with_o_or_in_place(laid, tmp_path):
    """Ruling 3 (2026-11-19, second set): with neither -o nor --in-place it changes nothing and
    says which to give; each flag writes; both, and --in-place on a takeoff, are refused."""
    path, _ = laid
    text = path.read_text(encoding="utf-8")
    at = list(load(path).scene.components[0].position)
    shifted = text.replace(f"position: {at}".replace("'", ""), "position: [9.0, 0.3, 9.0]", 1)
    assert shifted != text
    path.write_text(shifted, encoding="utf-8")
    done = _cli("layout", str(path))
    assert done.exit_code == 1
    assert "-o" in done.output and "--in-place" in done.output
    assert path.read_text(encoding="utf-8") == shifted  # nothing written
    out = tmp_path / "again.yaml"
    done = _cli("layout", str(path), "-o", str(out))
    assert done.exit_code == 0, done.output
    assert out.read_text(encoding="utf-8") == text
    assert path.read_text(encoding="utf-8") == shifted  # -o leaves the source alone
    done = _cli("layout", str(path), "--in-place")
    assert done.exit_code == 0, done.output
    assert path.read_text(encoding="utf-8") == text
    done = _cli("layout", str(path), "--in-place", "-o", str(tmp_path / "x.yaml"))
    assert done.exit_code == 1 and "not both" in done.output
    assert not (tmp_path / "x.yaml").exists()
    bare = stripped(tmp_path)
    before = bare.read_bytes()
    done = _cli("layout", str(bare), "--in-place")
    assert done.exit_code == 1 and "takeoff" in done.output
    assert bare.read_bytes() == before
    docs = (REPO / "docs" / "layout.md").read_text(encoding="utf-8")
    assert "--in-place" in docs


TURNED_BODY = """\
openfactory: "0.1"
scene: {name: turned, description: A long body turned 90 degrees.}
plc: {protocol: modbus_tcp}
composites:
  - type: long_item
    description: A long body
    parts:
      - {id: body, type: scenery, description: Body, position: [0.5, 0.25, 0],
         size: [2.0, 0.5, 0.4]}
    io: {}
components:
  - id: ITEM_01
    type: long_item
    description: A long body, turned
    position: [3.0, 0.0, 1.0]
    rotation_deg: 90
"""


def test_a_turned_composite_obstacle_is_its_parts_bodies(tmp_path):
    """Review fix 1b: a composite (or stand-in) turned 90 degrees and not marked is an obstacle
    whose body is the bounds of its parts' footprints, not those turned a second time."""
    from openfactory.core import footprints as fp
    from openfactory.layout import body, scene_items

    path = tmp_path / "turned.yaml"
    path.write_text(TURNED_BODY, encoding="utf-8")
    read = scene_items(TURNED_BODY, tmp_path)
    item = next(i for i in read.items if i.id == "ITEM_01")
    assert item.fixed
    parts = [c for c in load(path).components if c.id.startswith("ITEM_01")]
    want = fp.bounds(b for p in parts for b in fp.boxes(p.config, p.type))
    got = fp.bounds(body(item, item.position, item.heading))
    assert got == pytest.approx(want, abs=1e-6)


@pytest.mark.parametrize(
    "keep",
    [(), ("PE-201", "CYL-201"), ("SUP-101", "LV-101", "TK-101", "CV-201", "PE-201", "EMT-201")],
    ids=["stripped", "kept-mounts", "kept-six"],
)
def test_layout_on_a_freshly_scaffolded_scene_changes_no_byte(tmp_path, keep):
    """Ruling 1 (2026-11-19, second set), the general test: `layout SCENE -o` on the scene
    scaffold just wrote gives the same bytes, so a mounted device keeps its drawing's rail and
    distance and nothing is re-spread."""
    path, _ = write_scene(tmp_path, stripped(tmp_path, keep))
    out = tmp_path / "again.yaml"
    done = _cli("layout", str(path), "-o", str(out))
    assert done.exit_code == 0, done.output
    assert out.read_text(encoding="utf-8") == path.read_text(encoding="utf-8")


def test_a_mounted_device_follows_its_laid_out_belt_as_a_rigid_body(tmp_path):
    """Ruling 1: the sample with only PE-201's and CYL-201's mounts kept; the belt moved and laid
    out again, the eye stays 1.2 m and the pusher 2.0 m from its infeed, on the left rail."""
    path, _ = write_scene(tmp_path, stripped(tmp_path, keep=("PE-201", "CYL-201")))
    text = path.read_text(encoding="utf-8")
    scene = {c.id: c for c in load(path).scene.components}
    moved = text
    for cid in ("CV_201", "PE_201", "CYL_201"):  # the belt and its devices, 5 m along x
        at = list(scene[cid].position)
        moved = moved.replace(f"position: {at}".replace("'", ""),
                              f"position: {[round(at[0] + 5, 4), at[1], at[2]]}")  # fmt: skip
    assert moved != text
    again, ids, _ = relayout_scene(moved, path.parent)
    assert again == text and {"CV_201", "PE_201", "CYL_201"} <= set(ids)
    assert scene["PE_201"].position[0] == pytest.approx(scene["CV_201"].position[0] + 1.2)
    assert scene["CYL_201"].position[0] == pytest.approx(scene["CV_201"].position[0] + 2.0)
