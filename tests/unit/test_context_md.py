"""PLAN 2.19: the generated assistant context, ``<scene>.CONTEXT.md``. `run` and `export` write
it beside the scene; it lists every tag with its addresses in both encodings, every component
with its parameters, ports and fault kinds, the checks, and the commands; it is deterministic."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openfactory.cli import app
from openfactory.core.component import REGISTRY
from openfactory.core.schema import load_scene
from openfactory.export.context_md import context_md, context_path
from openfactory.protocols.modbus import lockfile_path, read_lock

REPO = Path(__file__).resolve().parents[2]
SHIPPED = sorted(p for p in (REPO / "scenes").glob("*.yaml") if ".modbus." not in p.name)
runner = CliRunner()


@pytest.mark.parametrize("scene", SHIPPED, ids=lambda p: p.stem)
def test_committed_context_is_current(scene):
    """Each shipped scene's CONTEXT.md is committed and matches a fresh generation."""
    committed = context_path(scene)
    assert committed.is_file(), f"run: uv run openfactory export {scene.relative_to(REPO)}"
    assert committed.read_text(encoding="utf-8") == context_md(load_scene(scene), scene)


@pytest.mark.parametrize("folder", ["scenes", "kits"])
@pytest.mark.parametrize("scene", SHIPPED, ids=lambda p: p.stem)
def test_every_lockfile_tag_appears(scene, folder):
    path = (
        REPO / folder / scene.name
        if folder == "scenes"
        else REPO / "kits" / scene.stem / scene.name
    )
    text = context_path(path).read_text(encoding="utf-8")
    seen = 0
    for encoding in ("float32", "int16_scaled"):
        lock = read_lock(lockfile_path(path, encoding))
        if lock is None:
            continue
        for name in lock.addresses:
            seen += 1
            assert f"`{name}`" in text, f"{name} ({encoding}) missing from {context_path(path)}"
    assert seen > 0


def test_sorter_context_content():
    scene = REPO / "scenes" / "sorting-by-height.yaml"
    text = context_path(scene).read_text(encoding="utf-8")
    # both encodings' addresses on one tag row
    row = next(line for line in text.splitlines() if line.startswith("| `CONV_01_SPD_AO`"))
    assert row.endswith("| HR:0-1 | HR:0 |") and "% 0 to 100" in row
    assert "| `SIM_HEARTBEAT_DI` | DI | (simulator) |" in text
    # components with parameters, ports and the fault kinds they support
    parsed = load_scene(scene)
    for comp in parsed.components:
        assert f"### {comp.id} ({comp.type})" in text
        faults = REGISTRY.resolve(comp.type).FAULTS
        assert ("Faults: " + ", ".join(f"`{k}`" for k in faults)) in text
    assert "`stroke` 0.6, `extend_time_s` 0.4" in text
    # checks and commands, the script found where `bundle` finds it
    assert "- `max_cycle_s`: 10" in text
    script = "../examples/pyplc/sorting-by-height.py"
    for command in (
        "openfactory validate sorting-by-height.yaml",
        f"openfactory lint sorting-by-height.yaml --script {script}",
        f"openfactory test sorting-by-height.yaml --script {script}",
        "openfactory test sorting-by-height.yaml --plc",
        "openfactory run sorting-by-height.yaml",
        "openfactory export sorting-by-height.yaml -o sorting-by-height-io-int16.csv "
        "--analog-encoding int16_scaled",
        "openfactory bundle sorting-by-height.yaml",
    ):
        assert command in text, command
    kit = (REPO / "kits/sorting-by-height/sorting-by-height.CONTEXT.md").read_text(encoding="utf-8")
    assert "--script sorting-by-height.py" in kit and "../examples" not in kit


def test_composite_parts_and_computed_di_are_listed():
    scene = REPO / "tests" / "scenes" / "gated-conveyor.yaml"
    text = context_md(load_scene(scene), scene)
    assert "Part of `GC_01`, a `gated_conveyor` composite (PLAN 2.16)." in text
    assert "computed from the parts' ports" in text
    assert "expr(GC_01_GATE.raised and GC_01_EYE.detect)" in text


def test_run_and_export_write_it_and_test_does_not(tmp_path):
    for path in (REPO / "scenes").glob("sorting-by-height.*"):
        if not path.name.endswith(".CONTEXT.md"):
            shutil.copy(path, tmp_path / path.name)
    scene = tmp_path / "sorting-by-height.yaml"
    script = REPO / "examples/pyplc/sorting-by-height.py"
    target = context_path(scene)
    tested = runner.invoke(app, ["test", str(scene), "--script", str(script)])
    assert tested.exit_code == 0, tested.output
    assert not target.exists()
    exported = runner.invoke(app, ["export", str(scene), "-o", str(tmp_path / "io.csv")])
    assert exported.exit_code == 0, exported.output
    first = target.read_bytes()
    assert b"No stand-in PLC script was found" in first  # no examples/pyplc above tmp_path
    target.unlink()
    ran = runner.invoke(app, ["run", str(scene), "--as-fast-as-possible", "--duration", "0.1"])
    assert ran.exit_code == 0, ran.output
    assert target.read_bytes() == first  # same bytes on every write


def test_a_missing_lockfile_is_shown_as_computed(tmp_path):
    shutil.copy(REPO / "scenes" / "sorting-by-height.yaml", tmp_path)
    shutil.copy(REPO / "scenes" / "sorting-by-height.float32.modbus.yaml", tmp_path)
    scene = tmp_path / "sorting-by-height.yaml"
    text = context_md(load_scene(scene), scene)
    assert "- int16_scaled addresses: no lockfile yet; as `run` would write it" in text
    assert not lockfile_path(scene, "int16_scaled").exists()  # generating writes no lockfile


def test_a_bundle_input_writes_no_context(tmp_path, monkeypatch):
    from openfactory.bundle import collect, write_bundle

    for path in (REPO / "scenes").glob("sorting-by-height.*"):
        shutil.copy(path, tmp_path / path.name)
    target = write_bundle(collect(tmp_path / "sorting-by-height.yaml", no_script=True),
                          tmp_path / "out.ofz")  # fmt: skip
    (tmp_path / "sorting-by-height.CONTEXT.md").unlink()
    monkeypatch.chdir(tmp_path)
    ran = runner.invoke(app, ["run", str(target), "--as-fast-as-possible", "--duration", "0.1"])
    assert ran.exit_code == 0, ran.output
    assert not list(tmp_path.glob("*.CONTEXT.md"))
