"""PLAN 5.14b: the plant in words, ``openfactory look`` (``src/openfactory/observe.py``)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from openfactory.cli import app as cli
from openfactory.core.component import REGISTRY, Component
from openfactory.core.kinematics import KinematicModel
from openfactory.core.loader import load
from openfactory.observe import Plant, carrier_frame, look, look_recording, part_line
from openfactory.pyplc import ScriptPlc
from openfactory.record import FRAME_EVERY, Recorder, read_recording
from openfactory.server.app import create_app
from openfactory.server.runtime import Runtime

REPO = Path(__file__).resolve().parents[2]
KIT_REC = REPO / "kits" / "sorting-by-height" / "sorting-by-height.ofrec"
GOLDEN = REPO / "tests" / "golden" / "look-sorting-by-height-6.0s.md"
SCENES = sorted(
    p
    for p in [*(REPO / "scenes").rglob("*.yaml"), *(REPO / "tests" / "scenes").glob("*.yaml")]
    if ".modbus." not in p.name and "_library" not in p.parts
)
#: Types no scene in the repository uses: a state of the shape their state() returns.
SYNTHETIC = {
    "junction": ({"pressure_bar": 1.25}, {}),
    "vfd_motor": ({"running": True, "speed": 50.0, "fault": False}, {}),
}


def scripted(scene: Path, script: Path) -> Runtime:
    loaded = load(scene)
    return Runtime(loaded, realtime=False, controller=ScriptPlc(script, loaded.io))


def test_every_registered_type_has_its_own_describe() -> None:
    """Done-when: a registered type without a describe fails this test."""
    missing = [
        name
        for name in sorted(REGISTRY.types())
        if REGISTRY.resolve(name).describe.__func__ is Component.describe.__func__  # type: ignore[attr-defined]
    ]
    assert not missing, f"no describe(state, geometry): {missing}"


def test_every_type_describes_a_real_state_in_one_line() -> None:
    logging.disable(logging.WARNING)
    try:
        samples: dict[str, tuple[dict, dict]] = dict(SYNTHETIC)
        for path in SCENES:
            for comp in load(path).components:
                samples.setdefault(comp.type, (comp.state(), comp.geometry()))
    finally:
        logging.disable(logging.NOTSET)
    assert REGISTRY.types() <= set(samples)
    for name in sorted(REGISTRY.types()):
        state, geometry = samples[name]
        text = REGISTRY.resolve(name).describe(state, geometry)
        assert text and "\n" not in text and len(text) < 120, (name, text)


def test_the_sorter_at_6_s_from_its_kit_recording_is_the_golden_file() -> None:
    """Done-when: the committed golden file. Renew it only when the report's form is meant to
    change: ``uv run openfactory look kits/sorting-by-height/sorting-by-height.ofrec --at 6.0
    > tests/golden/look-sorting-by-height-6.0s.md``."""
    text = look_recording(read_recording(KIT_REC), 6.0)
    assert text == GOLDEN.read_text(encoding="utf-8")
    assert len(text.splitlines()) < 150


def test_a_live_run_and_its_own_recording_give_the_same_text(tmp_path: Path) -> None:
    """Done-when: the same report from a live scripted run and from its recording, at 6.0 s,
    and at 4.2 s with a narrower window and for one component."""
    scene = REPO / "scenes" / "sorting-by-height.yaml"
    runtime = scripted(scene, REPO / "examples" / "pyplc" / "sorting-by-height.py")
    header = {
        "openfactory": "test",
        "scene_yaml": scene.read_text(encoding="utf-8"),
        "scene": runtime.scene_document(meshes=False, snaps=False),
        "dt": runtime.scheduler.dt,
        "encoding": "float32",
        "frame_every": FRAME_EVERY,
    }
    path = tmp_path / "run.ofrec"
    runtime.recorder = Recorder(path, header, runtime.snapshot)
    live = {}
    for _ in range(210):
        runtime._step(runtime.scheduler.dt)
    live["4.2 narrow"] = runtime.look(since_s=1.0, component="CONV_01")
    for _ in range(90):
        runtime._step(runtime.scheduler.dt)
    live["6.0"] = runtime.look()
    http = TestClient(create_app(runtime, web_dist=None), base_url="http://127.0.0.1")
    served = http.get("/api/look")
    assert served.headers["content-type"].startswith("text/markdown")
    assert served.text == live["6.0"]
    runtime.recorder.close({})
    rec = read_recording(path)
    assert look_recording(rec, 6.0) == live["6.0"]
    assert look_recording(rec, 4.2, since_s=1.0, component="CONV_01") == live["4.2 narrow"]
    assert live["6.0"] == GOLDEN.read_text(encoding="utf-8")  # the kit's run, the same run


def test_component_narrows_the_report() -> None:
    rec = read_recording(KIT_REC)
    text = look_recording(rec, 30.0, component="CYL_01")
    assert "## CYL_01" in text and "## Components" not in text and "## Checks" not in text
    assert "Connected to: CONV_01." in text  # stands on CONV_01's rail (W214's footprint)
    assert "CYL_01_EXT_DO" in text and "PE_01_DI" not in text
    belt = look_recording(rec, 6.0, component="CONV_01")
    assert "## Parts on CONV_01" in belt and "`P000002` tall, on CONV_01" in belt
    with pytest.raises(KeyError, match="NOPE"):
        look_recording(rec, 6.0, component="NOPE")


def test_get_api_look_refuses_what_it_cannot_answer() -> None:
    runtime = scripted(
        REPO / "scenes" / "sorting-by-height.yaml",
        REPO / "examples" / "pyplc" / "sorting-by-height.py",
    )
    http = TestClient(create_app(runtime, web_dist=None), base_url="http://127.0.0.1")
    assert http.get("/api/look", params={"component": "NOPE"}).status_code == 404
    assert http.get("/api/look", params={"since": 0}).status_code == 400
    assert http.post("/api/look", json={}).status_code == 405


def test_look_command_on_a_recording() -> None:
    runner = CliRunner()
    done = runner.invoke(cli, ["look", str(KIT_REC), "--at", "6"])
    assert done.exit_code == 0, done.output
    assert done.output == GOLDEN.read_text(encoding="utf-8")
    assert runner.invoke(cli, ["look", "--at", "6"]).exit_code == 2
    unknown = runner.invoke(cli, ["look", str(KIT_REC), "--component", "NOPE"])
    assert unknown.exit_code == 1


@pytest.mark.parametrize(
    ("scene", "script", "deck"),
    [
        ("demo-turntable.yaml", "demo-turntable.py", "turntable"),
        ("demo-lift.yaml", "demo-lift.py", "lift"),
    ],
)
def test_distance_along_a_moving_deck_uses_the_kinematics_frame(
    scene: str, script: str, deck: str
) -> None:
    """A turntable's frame turns with its deck and a lift's rises: the report's frame, from the
    document and the state alone, is the one the kinematics carries parts in, mid-move."""
    runtime = scripted(REPO / "tests" / "scenes" / scene, REPO / "tests" / "scenes" / script)
    comp = next(c for c in runtime.loaded.components if c.type == deck)
    moving = "turning" if deck == "turntable" else "moving"
    for _ in range(3000):
        runtime._step(runtime.scheduler.dt)
        if comp.state()[moving]:
            break
    assert comp.state()[moving], f"the {deck} never moved"
    document = {c["id"]: c for c in runtime.scene_document()["components"]}
    origin, rot = carrier_frame(document[comp.id], comp.state())
    k_origin, k_rot = KinematicModel._frame(comp)
    assert rot == pytest.approx(k_rot)
    assert origin == pytest.approx(tuple(k_origin))


def test_a_part_carried_by_nothing() -> None:
    held = {"id": "P1", "name": "box", "position": [1, 2, 3], "velocity": [0, 0, 0],
            "carrier": None, "follow": True}  # fmt: skip
    falling = {**held, "follow": False, "velocity": [0, -1.5, 0]}
    resting = {**held, "follow": False}
    assert part_line(held, {}, {}) == "`P1` box, held by a gripper at (1.00, 2.00, 3.00)"
    assert "falling at (1.00, 2.00, 3.00), 1.50 m/s" in part_line(falling, {}, {})
    assert "carried by nothing: at rest at" in part_line(resting, {}, {})


def test_report_without_checks_or_parts() -> None:
    document = {
        "dt": 0.02,
        "scene": {"name": "s", "description": "A test."},
        "components": [],
        "tags": [],
        "connections": [],
    }
    snapshot = {"t": 0.0, "tick": 0, "components": {}, "parts": [], "io": {}}
    text = look(document, snapshot, [], Plant())
    assert "The scene declares no checks." in text and text.count("None.") == 3
