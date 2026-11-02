"""PLAN 2.5b: composite logic: blocks, command io, composite connections, library composites by
name, and the first library entry, the four-zone accumulation conveyor."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from openfactory.bundle import collect, write_bundle
from openfactory.cli import app
from openfactory.core.iotable import TagWriteError, Writer
from openfactory.core.loader import build, load
from openfactory.core.schema import load_scene, parse_scene
from openfactory.pyplc import ScriptPlc

REPO = Path(__file__).resolve().parents[2]
DEMO = REPO / "scenes" / "examples" / "accumulation-demo.yaml"
DEMO_SCRIPT = REPO / "scenes" / "examples" / "accumulation-demo.py"
LIBRARY = REPO / "scenes" / "_library" / "accumulation_conveyor.yaml"
DT = 0.02
runner = CliRunner()


def run_demo(seconds: float):
    """The demo under its script; yields (loaded, t) after every tick."""
    loaded = load(DEMO)
    plc = ScriptPlc(DEMO_SCRIPT, loaded.io)
    for _ in range(round(seconds / DT)):
        plc.scan(loaded.time)
        loaded.step(DT)
        yield loaded


def zone_parts(loaded):
    parts = [p for p in loaded.world.parts.values() if (p.carrier or "").startswith("ACC_Z")]
    return sorted(parts, key=lambda p: p.position[0])


# --- Done-when: the accumulation conveyor ---------------------------------------------------


def test_four_boxes_accumulate_one_per_zone_without_touching():
    *_, loaded = run_demo(13.0)  # all four zones full at 11.4 s, release at 13.42 s
    assert loaded.io.get("ACC_REL_DO") is False
    parts = zone_parts(loaded)
    assert [p.carrier for p in parts] == ["ACC_Z1", "ACC_Z2", "ACC_Z3", "ACC_Z4"]  # no empty zone
    assert all(abs(p.v_flow) < 1e-12 for p in parts)  # all at rest
    assert all(loaded.io.get(f"ACC_Z{n}_DI") for n in range(1, 5))
    xs = [p.position[0] for p in parts]
    gaps = [b - a - 0.3 for a, b in zip(xs, xs[1:], strict=False)]
    assert all(0.6 < g < 0.8 for g in gaps), gaps  # no box touches another: no line pressure
    speeds = [loaded.component(f"ACC_Z{n}").surface_speed for n in range(1, 5)]
    assert speeds == [0.0, 0.0, 0.0, 0.0]


def test_release_lets_them_out_one_zone_at_a_time():
    cleared: dict[int, float] = {}
    started: dict[int, float] = {}
    released = None
    was = dict.fromkeys(range(1, 5), True)
    for loaded in run_demo(25.0):
        if released is None and loaded.io.get("ACC_REL_DO"):
            released = loaded.time
        if released is None:
            continue
        for n in range(1, 5):
            eye = loaded.io.get(f"ACC_Z{n}_DI")
            if was[n] and not eye:
                cleared.setdefault(n, loaded.time)
            was[n] = eye
            if loaded.component(f"ACC_Z{n}").surface_speed > 0:
                started.setdefault(n, loaded.time)
    assert released is not None
    assert started[4] == pytest.approx(released, abs=DT)  # the last zone goes on release
    for n in (3, 2, 1):  # each zone starts the tick after the zone ahead empties, not before
        assert cleared[n + 1] < started[n] <= cleared[n + 1] + 2 * DT + 1e-9
    assert started[3] - started[4] > 0.5 and started[2] - started[3] > 0.5
    assert loaded.component("REM_01").state()["removed"] == 4


def test_the_demo_lints_clean_and_runs_from_its_bundle(tmp_path, monkeypatch):
    linted = runner.invoke(app, ["lint", str(DEMO), "--script", str(DEMO_SCRIPT)])
    assert linted.exit_code == 0 and "0 error(s), 0 warning(s)" in linted.output
    contents = collect(DEMO)
    roles = {f.path: f.role for f in contents.files}
    assert roles["_library/accumulation_conveyor.yaml"] == "composite"
    target = write_bundle(contents, tmp_path / "acc.ofz")
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["test", str(target), "--bundled-script"])
    print(result.output)
    assert result.exit_code == 0 and "PASS: 2 of 2 checks passed" in result.output


# --- logic: semantics ---------------------------------------------------------------------------

GATE = """
openfactory: "0.1"
scene: {name: t, description: t}
composites:
  - type: gated_belt
    params: {length: 2.0}
    parts:
      - {id: belt, type: belt_conveyor, position: [0, 0, 0], length: "{length}", width: 0.5,
         speed_mps: 0.5}
      - {id: eye, type: photo_eye, position: [1.5, 0.05, 0], beam_length: 0.6}
    io:
      go: {command: DO}
      eye: {from: eye.detect}
    logic:
      belt.run: "go and not eye.detect"
components:
  - id: GB
    type: gated_belt
    description: Gated Belt
    position: [0, 0, 0]
    io: {go: GB_GO_DO, eye: GB_EYE_DI}
"""


def test_a_logic_driven_port_has_no_tag_and_follows_its_expression_one_tick_late():
    scene = parse_scene(GATE)
    names = [b.name for b in scene.tag_bindings()]
    assert names == ["GB_GO_DO", "GB_EYE_DI"]  # the command (after the first part); no belt.run
    assert scene.components[0].io.run is None
    loaded = build(scene)
    belt = loaded.component("GB_BELT")
    assert belt.driven == {"run": False}  # primed at load
    loaded.io.set("GB_GO_DO", True, Writer.PLC)
    loaded.step(DT)
    assert belt.driven["run"] is True and belt.surface_speed > 0
    eye = loaded.component("GB_EYE")
    eye.fault_inject("stuck_on")  # the eye's own output, read by the logic
    loaded.step(DT)  # the eye writes true in this tick; the logic saw false
    assert belt.driven["run"] is True
    loaded.step(DT)
    assert belt.driven["run"] is False
    with pytest.raises(TagWriteError):
        loaded.io.set("GB_EYE_DI", True, Writer.PLC)
    assert [t.name for t in loaded.io.tags()] == ["GB_GO_DO", "GB_EYE_DI", "SIM_HEARTBEAT_DI"]


def test_command_tags_are_described_by_their_instance_and_written_by_the_plc():
    loaded = build(parse_scene(GATE))
    assert loaded.io.tag("GB_GO_DO").description == "Gated Belt — go command"
    with pytest.raises(TagWriteError):
        loaded.io.set("GB_GO_DO", True, Writer.SIM)


@pytest.mark.parametrize(
    "change, error",
    [
        (("      eye: {from: eye.detect}",
          "      eye: {from: eye.detect}\n      run: {to: belt.run}"),
         "io.run also drives belt.run; a part command driven by logic cannot also be exposed"),
        (('"go and not eye.detect"', '"stop and not eye.detect"'),
         "'stop' is not a command io of gated_belt (commands: go)"),
        (("belt.run:", "eye.detect:"), "eye.detect is a DI port; logic drives DO ports"),
        (("belt.run:", "nope.run:"), "no part 'nope'"),
        (('"go and not eye.detect"', '"go and not belt.run"'),
         "'belt.run' is not a DI or AI port"),
        (('"go and not eye.detect"', '"go + 1"'), "arithmetic takes numbers"),
        (("    io: {go: GB_GO_DO, eye: GB_EYE_DI}", "    io: {eye: GB_EYE_DI}"),
         "io.go is required"),
        (("      go: {command: DO}", "      go: {command: DO, to: belt.run}"),
         "exactly one of `to`, `from` or `command`"),
    ],
)  # fmt: skip
def test_logic_load_errors(change, error):
    old, new = change
    assert old in GATE
    with pytest.raises(ValidationError, match=error.replace("(", r"\(").replace(")", r"\)")):
        parse_scene(GATE.replace(old, new))


def test_composite_connections_expand_and_are_checked():
    text = LIBRARY.read_text(encoding="utf-8")
    assert "connections:" in text
    scene = load_scene(DEMO)
    pairs = {(c.from_, c.to) for c in scene.connections}
    assert {("ACC_Z1", "ACC_Z2"), ("ACC_Z2", "ACC_Z3"), ("ACC_Z3", "ACC_Z4")} <= pairs
    bad = GATE.replace("    logic:", "    connections:\n      - {from: belt, to: eye}\n    logic:")
    with pytest.raises(ValidationError, match="connection end eye is a photo_eye"):
        parse_scene(bad)


# --- library composites -----------------------------------------------------------------------

USES = """
openfactory: "0.1"
scene: {name: t, description: t}
composites:
  - {library: accumulation_conveyor}
components:
  - id: ACC
    type: accumulation_conveyor
    description: Accumulation Conveyor
    position: [0, 0, 0]
    zone_length: 0.8
    io: {release: ACC_REL_DO, zone1_eye: A1_DI, zone2_eye: A2_DI, zone3_eye: A3_DI,
         zone4_eye: A4_DI}
"""


def test_a_library_composite_is_found_beside_the_scene_or_above_it(tmp_path):
    (tmp_path / "_library").mkdir()
    shutil.copy(LIBRARY, tmp_path / "_library")
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    scene = parse_scene(USES, deep)
    assert scene.library_files() == [(tmp_path / "_library" / LIBRARY.name).resolve()]
    assert scene.components[3].length == pytest.approx(0.8)  # z4, at zone_length 0.8
    assert scene.components[3].position[0] == pytest.approx(2.4)


def test_library_errors(tmp_path):
    with pytest.raises(ValidationError, match="not loaded from a file"):
        parse_scene(USES)
    with pytest.raises(ValidationError, match="no _library/accumulation_conveyor.yaml in"):
        parse_scene(USES, tmp_path)
    (tmp_path / "_library").mkdir()
    lib = tmp_path / "_library" / "accumulation_conveyor.yaml"
    lib.write_text(LIBRARY.read_text(encoding="utf-8").replace(
        "type: accumulation_conveyor", "type: other_thing"), encoding="utf-8")  # fmt: skip
    with pytest.raises(ValidationError, match="defines composite type 'other_thing'"):
        parse_scene(USES, tmp_path)
    lib.write_text('openfactory: "0.1"\ncomposite: {type: x, parts: []}\nextra: 1\n',
                   encoding="utf-8")  # fmt: skip
    with pytest.raises(ValidationError, match="holds `openfactory` and `composite` only"):
        parse_scene(USES, tmp_path)


def test_a_bundle_missing_its_library_is_refused_not_resolved_outside(tmp_path, monkeypatch):
    """A bundle names a library it does not carry; a decoy _library/ sits above where it would
    be unpacked. Opening it must fail, not load the decoy (PLAN 2.18 and 2.5b)."""
    import hashlib
    import json
    import zipfile

    from openfactory import bundle as bundle_mod
    from openfactory.bundle import BundleError, open_bundle

    decoy = tmp_path / "_library"
    decoy.mkdir()
    shutil.copy(LIBRARY, decoy)
    unpack = tmp_path / "unpack"
    unpack.mkdir()
    monkeypatch.setattr(bundle_mod.tempfile, "mkdtemp", lambda **kw: str(unpack))
    scene = USES.encode("utf-8")
    meta = {"format": "openfactory-scene-bundle", "bundle_version": 1, "openfactory": "0.1",
            "scene": "s.yaml", "script": None,
            "files": [{"path": "s.yaml", "role": "scene", "bytes": len(scene),
                       "sha256": hashlib.sha256(scene).hexdigest()}]}  # fmt: skip
    target = tmp_path / "x.ofz"
    with zipfile.ZipFile(target, "w") as zf:
        zf.writestr("manifest.json", json.dumps(meta))
        zf.writestr("s.yaml", scene)
    with (
        pytest.raises(BundleError, match="has no _library/accumulation_conveyor.yaml"),
        open_bundle(target),
    ):
        pass
    assert not any(unpack.iterdir())  # refused before anything was written
