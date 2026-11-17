"""PLAN 5.2c: the ``scenery`` primitive, and the ``driven_item`` and ``variable_item`` library
composites built on it."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from openfactory.components.scenery import Scenery
from openfactory.core.component import REGISTRY
from openfactory.core.iotable import Writer
from openfactory.core.loader import build
from openfactory.core.schema import COMPONENT_MODELS, Scene, instance_ports
from openfactory.pyplc import ScriptPlc
from openfactory.server.runtime import Runtime

REPO = Path(__file__).resolve().parents[2]
DEMO = REPO / "scenes" / "examples" / "variable-item-demo.yaml"
SORTER = REPO / "scenes" / "sorting-by-height.yaml"
DT = 0.02


def demo_doc() -> dict:
    return yaml.safe_load(DEMO.read_text(encoding="utf-8"))


def demo(doc: dict | None = None) -> Scene:
    return Scene.model_validate(doc or demo_doc(), context={"scene_dir": DEMO.parent})


def body(**fields) -> dict:
    return {"id": "SCN_01", "type": "scenery", "description": "Platform", "position": [0, 0.5, 0],
            **fields}  # fmt: skip


def test_scenery_has_no_io_no_behaviour_and_describes_its_size() -> None:
    assert REGISTRY.resolve("scenery") is Scenery and "scenery" in COMPONENT_MODELS
    model = COMPONENT_MODELS["scenery"].model_validate(body(size=[2.0, 0.4, 1.5]))
    assert instance_ports(model) == {} and Scenery.FAULTS == ()
    assert model.color == "#8b939c"
    doc = {"openfactory": "0.1", "scene": {"name": "s", "description": "A body."},
           "components": [body(size=[2.0, 0.4, 1.5], color="#336699")]}  # fmt: skip
    loaded = build(Scene.model_validate(doc))
    comp = loaded.components[0]
    before = dict(comp.raw)
    for _ in range(10):
        loaded.step(DT)
    assert comp.state() == {} and comp.raw == before
    assert comp.geometry()["size"] == [2.0, 0.4, 1.5] and comp.geometry()["color"] == "#336699"
    assert Scenery.describe({}, comp.geometry()) == "a 2.00 x 0.40 x 1.50 m body, no I/O"
    assert [t.name for t in loaded.io.tags()] == ["SIM_HEARTBEAT_DI"]
    with pytest.raises(ValueError):
        comp.check_fault("stuck_on")


@pytest.mark.parametrize(
    "fields",
    [{"color": "grey"}, {"color": "#12345"}, {"size": [1, 0, 1]}, {"io": {"run": "X_01_RUN_DO"}}],
)
def test_scenery_refuses(fields: dict) -> None:
    with pytest.raises(ValidationError):
        COMPONENT_MODELS["scenery"].model_validate(body(**fields))


def _parts_over(doc: dict, ticks: int) -> list:
    loaded = build(Scene.model_validate(doc, context={"scene_dir": SORTER.parent}))
    runtime = Runtime(
        loaded,
        realtime=False,
        controller=ScriptPlc(REPO / "examples" / "pyplc" / "sorting-by-height.py", loaded.io),
    )
    seen = []
    for n in range(ticks):
        runtime._step(DT)
        if n % 25 == 0:
            seen.append([(p["id"], p["position"], p["carrier"]) for p in runtime.snapshot["parts"]])
    return seen


def test_parts_pass_through_scenery() -> None:
    """A scenery box straddling the sorter's infeed belt, boxes inside it: the parts move
    exactly as without it."""
    plain = yaml.safe_load(SORTER.read_text(encoding="utf-8"))
    walled = yaml.safe_load(SORTER.read_text(encoding="utf-8"))
    walled["components"].append(body(position=[1.5, 0.3, 0], size=[1.0, 0.6, 2.0]))
    assert _parts_over(walled, 1500) == _parts_over(plain, 1500)


def test_the_item_mesh_is_ours_rebuilt_byte_for_byte_and_small() -> None:
    spec = importlib.util.spec_from_file_location("mim", REPO / "tools" / "make_item_mesh.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    data = module.OUT.read_bytes()
    assert module.build() == data and len(data) < 1_000_000
    sidecar = module.OUT.with_name(module.OUT.name + ".license.txt").read_text(encoding="utf-8")
    assert sidecar == module.LICENSE and "no outside asset" in sidecar
    assert module.main(["--check"]) == 0


def test_the_variable_item_demo_holds_a_box_and_a_mesh_with_its_ports() -> None:
    """5.2's Done-when: a variable_item drawn as a box and with a mesh, with start, running,
    speed and speed feedback ports and a 2 s start delay; its tags export like any other."""
    scene = demo()
    comps = {c.id: c for c in scene.components}
    assert comps["MX_01_BODY"].type == "scenery" and comps["MX_01_BODY"].mesh is None
    assert comps["MX_02_BODY"].mesh.file == "models/mixer-drive.glb"
    assert tuple(comps["MX_02_BODY"].size) == (1.0, 0.8, 0.6)
    tags = {b.name: (b.kind, b.transform.describe()) for b in scene.tag_bindings()}
    assert tags["MX_02_RUN_DO"] == ("DO", "")
    assert tags["MX_02_SPD_AO"] == ("AO", "")
    assert tags["MX_02_RUN_DI"][0] == "DI" and tags["MX_02_RUN_DI"][1].endswith("on-delay 2 s")
    assert tags["MX_02_SPD_AI"] == ("AI", "expr(speed if running else 0); lag 4 s")


def test_the_start_delay_is_2_s_and_the_selector_rules() -> None:
    loaded = build(demo())
    io = loaded.io
    io.set("MX_02_SPD_AO", 80.0, Writer.PLC)
    io.set("MX_02_RUN_DO", True, Writer.PLC)
    ticks = 0
    while not io.get("MX_02_RUN_DI"):
        loaded.step(DT)
        ticks += 1
    assert ticks == 101  # the PLC reads it 1 tick after start without a delay (5.2a), 100 more
    assert io.get("MX_02_AUTO_DI") is True and io.get("MX_02_HAND_DI") is False
    hoa = next(c for c in loaded.components if c.id == "MX_01_HOA")
    hoa.set_position("a")  # hand: runs without the PLC
    for _ in range(2):
        loaded.step(DT)
    assert io.get("MX_01_RUN_DI") is True and io.get("MX_01_HAND_DI") is True
    hoa.set_position("center")  # off: stops whatever the PLC says
    io.set("MX_01_RUN_DO", True, Writer.PLC)
    for _ in range(2):
        loaded.step(DT)
    assert io.get("MX_01_RUN_DI") is False


def test_driven_item_loads_and_mesh_needs_one_scenery_body() -> None:
    doc = demo_doc()
    doc["composites"] = [{"library": "driven_item"}, {"library": "accumulation_conveyor"}]
    doc["components"] = [
        {"id": "AG_01", "type": "driven_item", "description": "Agitator", "position": [0, 0, 0],
         "mesh": {"file": "models/mixer-drive.glb"},
         "io": {"start": "AG_01_RUN_DO", "running": "AG_01_RUN_DI", "hoa_hand": "AG_01_HAND_DI",
                "hoa_auto": "AG_01_AUTO_DI"}},
    ]  # fmt: skip
    doc.pop("checks")
    scene = demo(doc)
    assert {b.name for b in scene.tag_bindings()} == {
        "AG_01_RUN_DO", "AG_01_RUN_DI", "AG_01_HAND_DI", "AG_01_AUTO_DI"
    }  # fmt: skip
    assert next(c for c in scene.components if c.id == "AG_01_BODY").mesh is not None
    doc["components"].append(
        {"id": "ACC", "type": "accumulation_conveyor", "description": "Buffer",
         "position": [3, 0, 0], "mesh": {"file": "models/mixer-drive.glb"},
         "io": {"release": "ACC_REL_DO", "zone1_eye": "ACC_Z1_DI", "zone2_eye": "ACC_Z2_DI",
                "zone3_eye": "ACC_Z3_DI", "zone4_eye": "ACC_Z4_DI"}}
    )  # fmt: skip
    with pytest.raises(ValidationError, match="has 0 scenery parts; it needs exactly one"):
        demo(doc)
