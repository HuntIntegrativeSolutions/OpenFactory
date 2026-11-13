"""PLAN 4.1a: the editor's operations on scene text. On every shipped scene: set, move, add and
delete each give a scene that loads with the change, keep every comment outside the edited text,
and their inverses give the original bytes back."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from openfactory.core.sceneedit import apply_ops, emit, emit_item, undo
from openfactory.core.schema import parse_scene

REPO = Path(__file__).resolve().parents[2]
SCENES = sorted(
    p
    for p in [*(REPO / "scenes").glob("*.yaml"), *(REPO / "scenes" / "examples").glob("*.yaml")]
    if not p.name.endswith(".modbus.yaml")
)
BUTTON = {
    "id": "EDIT_PB_01",
    "type": "pushbutton",
    "description": "Editor Test Pushbutton",
    "position": [0.0, 1.2, -2.0],
    "io": {"pressed": "EDIT_PB_01_DI"},
}


def comments(text: str) -> list[str]:
    """Every comment, from its #, in order (a # inside quotes does not occur in the scenes)."""
    return [line[line.index("#") :] for line in text.splitlines() if "#" in line]


def components(text: str, scene: Path) -> dict[str, dict]:
    parsed = yaml.safe_load(text)
    parse_scene(text, scene.parent)  # it loads
    return {c["id"]: c for c in parsed["components"]}


def deletable(text: str, scene: Path) -> tuple[str, bool]:
    """A component to delete, and whether the scene still loads without it. Where every
    component is referenced (a closed flow loop, conveyors joined by connections), the last
    one is deleted anyway: the text edit is the same, and the load error is what the editor's
    preview reports (deleting the references with it is the editor's job, PLAN 4.1c)."""
    ids = [c["id"] for c in yaml.safe_load(text)["components"]]
    for comp_id in reversed(ids):
        new, _ = apply_ops(text, [{"op": "delete", "id": comp_id}])
        try:
            parse_scene(new, scene.parent)
        except Exception:  # noqa: BLE001, S112 - referenced: try the next
            continue
        return comp_id, True
    return ids[-1], False


@pytest.mark.parametrize("scene", SCENES, ids=lambda p: p.stem)
def test_set_move_add_delete_and_their_inverses_on_every_shipped_scene(scene):
    text = scene.read_text(encoding="utf-8")
    before = components(text, scene)
    first = next(iter(before))
    victim, loads = deletable(text, scene)
    x, y, z = before[first]["position"]
    steps = [
        ("set", [{"op": "set", "path": ["components", first, "description"], "value": "Edited"}]),
        ("move", [{"op": "move", "id": first, "position": [x + 0.25, y, z], "rotation_deg": 15}]),
        ("add", [{"op": "add", "component": BUTTON}]),
        ("delete", [{"op": "delete", "id": victim}]),
    ]
    for name, ops in steps:
        new, inverses = apply_ops(text, ops)
        if name == "delete" and not loads:
            with pytest.raises(ValueError, match=victim):
                parse_scene(new, scene.parent)  # a dangling reference, named
            after = {c["id"]: c for c in yaml.safe_load(new)["components"]}
        else:
            after = components(new, scene)
        if name == "set":
            assert after[first]["description"] == "Edited"
        elif name == "move":
            assert after[first]["position"] == [x + 0.25, y, z]
            assert after[first]["rotation_deg"] == 15
        elif name == "add":
            assert after["EDIT_PB_01"] == BUTTON and list(after)[-1] == "EDIT_PB_01"
        else:
            assert victim not in after and len(after) == len(before) - 1
        if name == "delete":  # its own leading comments go with it, every other one stays
            removed = comments(_removed(text, new))
            kept = comments(text)
            for c in removed:
                kept.remove(c)
            assert comments(new) == kept, (scene.name, name)
        else:
            assert comments(new) == comments(text), (scene.name, name)
        others = {k: v for k, v in before.items() if k not in (first, victim)}
        assert all(after[k] == v for k, v in others.items())  # nothing else changed
        assert undo(new, inverses) == text, (scene.name, name)  # byte for byte
    # all four at once, undone in one go
    every = [op for _, ops in steps for op in ops]
    new, inverses = apply_ops(text, every)
    if loads:
        components(new, scene)
    assert undo(new, inverses) == text


def test_most_shipped_scenes_have_a_component_that_deletes_cleanly():
    clean = [s.stem for s in SCENES if deletable(s.read_text(encoding="utf-8"), s)[1]]
    assert len(clean) >= len(SCENES) - 2, clean


def _removed(old: str, new: str) -> str:
    """The text a single deletion removed (the old text minus the common prefix and suffix)."""
    i = 0
    while i < len(new) and old[i] == new[i]:
        i += 1
    j = 0
    while j < len(new) - i and old[-1 - j] == new[-1 - j]:
        j += 1
    return old[i : len(old) - j]


def test_a_delete_takes_the_comment_lines_directly_above_and_leaves_a_blank_line_separated_one():
    text = (
        'openfactory: "0.1"\ncomponents:\n  - id: A  # inline\n    type: t\n\n'
        "  # section comment\n\n  # about B\n  # more about B\n  - id: B\n    type: t\n"
        "  # about C\n  - id: C\n    type: t\n"
    )
    new, inv = apply_ops(text, [{"op": "delete", "id": "B"}])
    assert "about B" not in new and "section comment" in new and "about C" in new
    assert "- id: C" in new and undo(new, inv) == text


def test_set_adds_a_missing_key_and_unset_removes_it_and_flow_values_are_edited_in_place():
    text = 'openfactory: "0.1"\ncomponents:\n  - id: A  # keep\n    io: {detect: PE_01_DI}\n'
    new, inv = apply_ops(
        text,
        [
            {"op": "set", "path": ["components", "A", "speed_mps"], "value": 0.6},
            {"op": "set", "path": ["components", "A", "io", "detect"], "value": "PE_02_DI"},
            {"op": "set", "path": ["components", "A", "io", "count"], "value": "PE_02_CT"},
        ],
    )
    assert new == (
        'openfactory: "0.1"\ncomponents:\n  - id: A  # keep\n'
        "    io: {detect: PE_02_DI, count: PE_02_CT}\n    speed_mps: 0.6\n"
    )
    assert undo(new, inv) == text
    gone, inv2 = apply_ops(new, [{"op": "unset", "path": ["components", "A", "speed_mps"]}])
    assert "speed_mps" not in gone and undo(gone, inv2) == new


def test_errors_name_the_operation():
    text = 'openfactory: "0.1"\ncomponents:\n  - id: A\n    io:\n      detect: X_DI\n'
    with pytest.raises(ValueError, match=r"op 1 \(delete\): no item with id 'NOPE'"):
        apply_ops(text, [{"op": "delete", "id": "NOPE"}])
    with pytest.raises(ValueError, match=r"op 1 \(set\).*block collection"):
        apply_ops(text, [{"op": "set", "path": ["components", "A", "io"], "value": {}}])
    with pytest.raises(ValueError, match=r"op 2 \(frobnicate\)"):
        apply_ops(
            text,
            [{"op": "set", "path": ["components", "A", "x"], "value": 1}, {"op": "frobnicate"}],
        )


def test_the_emitter_writes_the_shipped_scenes_style():
    assert emit(0.5) == "0.5" and emit(True) == "true" and emit([1.0, 0, -2.5]) == "[1.0, 0, -2.5]"
    assert emit("Transfer Belt Conveyor") == "Transfer Belt Conveyor"
    assert emit("yes") == '"yes"' and emit("a: b") == '"a: b"' and emit("1.0") == '"1.0"'
    assert emit({"tag": "X_DI", "address": 3}) == "{tag: X_DI, address: 3}"
    assert emit_item(BUTTON) == [
        "- id: EDIT_PB_01",
        "  type: pushbutton",
        "  description: Editor Test Pushbutton",
        "  position: [0.0, 1.2, -2.0]",
        "  io:",
        "    pressed: EDIT_PB_01_DI",
    ]
    for value in (0.5, True, [1.0, 0, -2.5], "yes", "a: b", {"tag": "X_DI", "address": 3}):
        assert yaml.safe_load(f"k: {emit(value)}")["k"] == value
    assert not re.search(r"\s$", emit("x"))


# --- connect (PLAN 4.1d): what a snap in the editor adds ------------------------------------------


def _two_belts(connections: str = "") -> str:
    belts = "".join(
        f"  - id: {cid}\n    type: belt_conveyor\n    description: Belt {cid}\n"
        f"    position: [{x}, 0, 0]\n    length: 2\n    width: 0.5\n    speed_mps: 0.5\n"
        f"    io: {{run: {cid}_RUN_DO}}\n"
        for cid, x in (("CONV_01", 0), ("CONV_02", 2), ("CONV_03", 4))
    )
    return (
        'openfactory: "0.1"\nscene: {name: two-belts, description: x, author: x}\n'
        "plc: {protocol: none}\n# the belts\ncomponents:\n" + belts + connections
    )


@pytest.mark.parametrize(
    "connections",
    [
        "",  # none yet: a new block at the end
        "connections:\n  - {from: CONV_02, to: CONV_03}\n",  # a block list
        "connections: [{from: CONV_02, to: CONV_03}]\n",  # a flow list
        "connections: []\n",  # an empty flow list
    ],
)
def test_connect_adds_the_connection_and_its_inverse_restores_the_bytes(connections):
    text = _two_belts(connections)
    new, inverses = apply_ops(text, [{"op": "connect", "from": "CONV_01", "to": "CONV_02"}])
    pairs = {(c.from_, c.to) for c in parse_scene(new).connections}
    assert ("CONV_01", "CONV_02") in pairs
    assert len(pairs) == (2 if "CONV_03" in connections else 1)
    assert "# the belts" in new  # comments kept
    assert undo(new, inverses) == text
    again, _ = apply_ops(new, [{"op": "connect", "from": "CONV_01", "to": "CONV_02"}])
    assert again == new  # already joined: nothing


def test_connect_refuses_a_bad_id_or_a_conveyor_feeding_itself():
    with pytest.raises(ValueError, match=r"op 1 \(connect\).*not a component id"):
        apply_ops(_two_belts(), [{"op": "connect", "from": "conv 1", "to": "CONV_02"}])
    with pytest.raises(ValueError, match="cannot feed itself"):
        apply_ops(_two_belts(), [{"op": "connect", "from": "CONV_01", "to": "CONV_01"}])
