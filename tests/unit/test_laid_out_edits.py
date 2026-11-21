"""PLAN 5.5, ruled 2026-11-20 (ruling 3): when the editor clears a component's `laid_out` mark,
the save also removes that component's LAID OUT comment, and no other byte of the file changes
beyond the mark and the position. On a copy of the wash-water sample with every position
removed, scaffolded and laid out in the test's folder."""

from __future__ import annotations

import difflib
from pathlib import Path

import pytest
import yaml

from openfactory import takeoff as tk
from openfactory.core.sceneedit import apply_ops, undo
from openfactory.scaffold import scaffold

REPO = Path(__file__).resolve().parents[2]
SAMPLE = REPO / "docs" / "samples" / "wash-skid" / "wash-skid.takeoff.yaml"


@pytest.fixture(scope="module")
def laid(tmp_path_factory) -> str:  # noqa: ANN001
    data = yaml.safe_load(SAMPLE.read_text(encoding="utf-8"))
    for e in data["equipment"]:
        for key in ("layout", "mount", "at"):
            e.pop(key, None)
    data.pop("checked", None)
    path = tmp_path_factory.mktemp("laid") / "bare.takeoff.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return scaffold(tk.load(path)).text


def changed(a: str, b: str) -> tuple[list[str], list[str]]:
    gone, new = [], []
    for line in difflib.ndiff(a.splitlines(), b.splitlines()):
        if line.startswith("- "):
            gone.append(line[2:])
        elif line.startswith("+ "):
            new.append(line[2:])
    return gone, new


@pytest.mark.parametrize(
    ("cid", "clear"),
    [
        ("TK_101", {"op": "unset"}),  # a moved component: what the editor sends
        ("LT_101", {"op": "set", "value": False}),  # the checkbox unticked
        ("AG_101", {"op": "unset"}),  # a stand-in: its PLACEHOLDER comment
        ("PIPE_101", {"op": "unset"}),  # a pipe: the comment is on the mark's own line
    ],
)
def test_clearing_the_mark_removes_its_comment_and_nothing_else(laid, cid, clear):
    ops = [
        {"op": "set", "path": ["components", cid, "position"], "value": [20.0, 0.3, 9.0]},
        {**clear, "path": ["components", cid, "laid_out"]},
    ]
    after, inverses = apply_ops(laid, ops)
    gone, new = changed(laid, after)
    print("\n".join(["-" + g for g in gone] + ["+" + n for n in new]))
    block = after.split(f"  - id: {cid}")[1].split("\n  - id: ")[0]
    assert "LAID OUT" not in block and "laid_out" not in block
    assert len(new) <= 2  # the position line, and the id line without its LAID OUT comment
    assert any("position: [20.0, 0.3, 9.0]" in n for n in new)
    for g in gone:  # each line that went is the position, the mark, or the commented id line
        assert "position:" in g or "laid_out: true" in g or "LAID OUT" in g, g
    for n in new:
        if "position:" not in n:  # the id line: the same text, less exactly the LAID OUT part
            old = next(g for g in gone if g.split("#")[0] == n.split("#")[0])
            assert old.startswith(n.rstrip(")")) and "LAID OUT" in old
    assert undo(after, inverses) == laid  # and back, byte for byte
