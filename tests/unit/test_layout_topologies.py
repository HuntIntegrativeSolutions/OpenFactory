"""PLAN 5.5 review fixes (2026-11-19, second set), 1e: topology beyond the sample. Four small
takeoffs in tests/takeoffs/ (fixtures, not shipped scenes), each with no positions, lay out with
no error and no overlap finding, then build and run a tick."""

from __future__ import annotations

from pathlib import Path

import pytest

from openfactory import takeoff as tk
from openfactory.core.loader import load
from openfactory.lint import lint_scene
from openfactory.scaffold import scaffold

FIXTURES = Path(__file__).resolve().parents[1] / "takeoffs"
NAMES = ["junction-two-tanks", "recirculation-loop", "belts-turntable-lift", "heater-and-drive"]
OVERLAPS = {"W204", "W205", "W212", "W213", "W214", "W217"}


@pytest.mark.parametrize("name", NAMES)
def test_each_topology_lays_out_clear_builds_and_runs(tmp_path, name):
    loaded = tk.load(FIXTURES / f"{name}.takeoff.yaml")
    assert not [f for f in tk.check(loaded) if f.level == "error"]
    result = scaffold(loaded)
    assert not result.overlaps, result.overlaps
    assert result.exit_code() == 3  # laid-out rows are for a person to confirm, not 1
    assert all(o.outcome in ("laid out", "placed") for o in result.outcomes)
    scene = tmp_path / f"{name}.yaml"
    scene.write_text(result.text, encoding="utf-8")
    findings = lint_scene(scene)
    assert not [f for f in findings if f.level == "error"], findings
    assert not [f for f in findings if f.code in OVERLAPS], [str(f) for f in findings]
    load(scene).step(0.02)


def test_the_junction_keeps_each_branch_together_in_flow_order(tmp_path):
    """Each tank is followed by its own pump along the line, so a pump's straight run from its
    tank's bottom crosses no other body; supplies first, drains last."""
    result = scaffold(tk.load(FIXTURES / "junction-two-tanks.takeoff.yaml"))
    scene = tmp_path / "j.yaml"
    scene.write_text(result.text, encoding="utf-8")
    x = {c.id: c.config.position[0] for c in load(scene).components}
    order = sorted(("SUP_1", "J_1", "TK_1", "P_1", "TK_2", "P_2", "DR_1", "DR_2"), key=x.get)
    assert order == ["SUP_1", "J_1", "TK_1", "P_1", "TK_2", "P_2", "DR_1", "DR_2"]
