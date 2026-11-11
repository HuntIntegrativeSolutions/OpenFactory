"""PLAN 4.7: scene format migration. The format is 0.1 and has never broken, so every shipped
scene is current and `migrate` leaves it byte for byte; the corpus folders match the migrations
(none yet); and a synthetic migration, defined only here, proves a rewrite keeps every comment,
the key order and the layout, and round-trips with its inverse."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from openfactory.cli import app
from openfactory.core import migrate
from openfactory.core.schema import SCHEMA_VERSION

REPO = Path(__file__).resolve().parents[2]
SCENES = REPO / "scenes"
CORPUS = REPO / "tests" / "migrate"
SHIPPED = sorted(
    p
    for p in [
        *SCENES.glob("*.yaml"),
        *SCENES.glob("examples/*.yaml"),
        *SCENES.glob("_library/*.yaml"),
    ]
    if not p.name.endswith(".modbus.yaml")
)


def test_every_shipped_scene_is_current_and_migrate_leaves_it_byte_for_byte():
    assert len(SHIPPED) >= 12
    for path in SHIPPED:
        text = path.read_text(encoding="utf-8")
        result = migrate.migrate_text(text)
        assert (result.source, result.changed, result.text) == (SCHEMA_VERSION, False, text), path


def test_the_corpus_has_a_folder_for_exactly_the_versions_migrations_start_from():
    folders = {p.name for p in CORPUS.iterdir() if p.is_dir()}
    assert folders == set(migrate.MIGRATIONS)
    for version in sorted(migrate.MIGRATIONS):  # none yet: the first breaking change adds one
        for shipped in SHIPPED:
            old = CORPUS / version / shipped.relative_to(SCENES)
            assert old.is_file(), f"corpus missing {old}"
            result = migrate.migrate_text(old.read_text(encoding="utf-8"))
            assert result.text == shipped.read_text(encoding="utf-8"), old


def test_migrate_check_passes_on_every_shipped_scene():
    run = CliRunner().invoke(
        app, ["migrate", "--check", str(SCENES), str(SCENES / "examples"), str(SCENES / "_library")]
    )
    assert run.exit_code == 0, run.output
    assert run.output.count(f"format {SCHEMA_VERSION}, current") == len(SHIPPED)


def test_errors_name_the_problem():
    with pytest.raises(ValueError, match="no `openfactory:` version line"):
        migrate.migrate_text("scene: {name: x}\n")
    with pytest.raises(ValueError, match="newer than this OpenFactory's 0.1"):
        migrate.migrate_text('openfactory: "9.0"\n')
    with pytest.raises(ValueError, match="no migration from format 0.0"):
        migrate.migrate_text('openfactory: "0.0"\n')


# --- a synthetic migration, in this test only: 0.1 -> 0.2 renames scene.description to
# scene.summary and the components' `position` to `at`, and writes a component's speed as 0.75


def _forward(doc: migrate.Document) -> None:
    doc.rename_key(("scene", "description"), "summary")
    for i in range(_count(doc)):
        doc.rename_key(("components", i, "position"), "at")
    doc.set_scalar(("components", 0, "speed_mps"), "0.75")


def _backward(doc: migrate.Document) -> None:
    doc.rename_key(("scene", "summary"), "description")
    for i in range(_count(doc)):
        doc.rename_key(("components", i, "at"), "position")
    doc.set_scalar(("components", 0, "speed_mps"), ORIGINAL_SPEED)


def _count(doc: migrate.Document) -> int:
    _, node = doc.find(("components",))
    return len(node.value)


FORWARD = {"0.1": migrate.Migration("0.1", "0.2", "synthetic: renames and a speed", _forward)}
BACKWARD = {"0.2": migrate.Migration("0.2", "0.1", "synthetic: the inverse", _backward)}
SCENE = SCENES / "conveyor-start-stop.yaml"
ORIGINAL_SPEED = migrate.Document(SCENE.read_text(encoding="utf-8")).value(
    ("components", 0, "speed_mps")
)


def _keys(node: yaml.Node, path: tuple = ()) -> list[tuple[tuple, str]]:
    """Every mapping key with the path of its mapping, in document order, depth first."""
    out: list[tuple[tuple, str]] = []
    if isinstance(node, yaml.MappingNode):
        for k, v in node.value:
            out.append((path, k.value))
            out += _keys(v, (*path, k.value))
    elif isinstance(node, yaml.SequenceNode):
        for i, v in enumerate(node.value):
            out += _keys(v, (*path, i))
    return out


def _renamed(path: tuple, key: str) -> str:
    if path == ("scene",) and key == "description":
        return "summary"
    if len(path) == 2 and path[0] == "components" and key == "position":
        return "at"
    return key


def test_a_synthetic_rewrite_keeps_comments_key_order_and_layout_and_round_trips():
    text = SCENE.read_text(encoding="utf-8")
    assert "description:" in text and "speed_mps:" in text and "#" in text
    out = migrate.migrate_text(text, migrations=FORWARD, current="0.2")
    assert out.changed and out.source == "0.1" and out.steps[0].startswith("0.1 -> 0.2")
    new = out.text
    # every comment survives, character for character, in order
    comments = [line[line.index("#") :] for line in text.splitlines() if "#" in line]
    assert [line[line.index("#") :] for line in new.splitlines() if "#" in line] == comments
    # the same lines; only the version, the renamed keys and the speed changed
    old_lines, new_lines = text.splitlines(), new.splitlines()
    assert len(old_lines) == len(new_lines)
    changed = [(a, b) for a, b in zip(old_lines, new_lines, strict=True) if a != b]
    expected = 1 + 1 + _count(migrate.Document(text)) + 1  # version, summary, each at, speed
    assert len(changed) == expected
    edits = r"\b(description|summary|position|at):|openfactory: \"0\.[12]\"|speed_mps: [0-9.]+"
    for a, b in changed:
        assert re.sub(edits, "", a) == re.sub(edits, "", b), (a, b)
    # key order everywhere is the original's, under the new names
    before = [_renamed(p, k) for p, k in _keys(yaml.compose(text, Loader=yaml.SafeLoader))]
    after = [k for _, k in _keys(yaml.compose(new, Loader=yaml.SafeLoader))]
    assert after == before
    loaded = yaml.safe_load(new)
    assert loaded["openfactory"] == "0.2" and loaded["components"][0]["speed_mps"] == 0.75
    assert "summary" in loaded["scene"] and "at" in loaded["components"][0]
    # and back: the inverse gives the original bytes
    back = migrate.migrate_text(new, migrations=BACKWARD, current="0.1")
    assert back.text == text


def test_a_chain_runs_each_migration_in_turn():
    calls = []
    chain = {
        "0.0": migrate.Migration("0.0", "0.05", "a", lambda d: calls.append("a")),
        "0.05": migrate.Migration("0.05", "0.1", "b", lambda d: calls.append("b")),
    }
    out = migrate.migrate_text('# kept\nopenfactory: "0.0"  # kept too\n', migrations=chain)
    assert calls == ["a", "b"] and out.text == '# kept\nopenfactory: "0.1"  # kept too\n'
