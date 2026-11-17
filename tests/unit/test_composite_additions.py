"""PLAN 5.2a: composite additions. Command names in io expressions, ``command: AO``, ``kind: AI``
numeric io, the conditional expression, computed io reading computed io, cycles refused."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from openfactory.core.iotable import Writer
from openfactory.core.loader import build
from openfactory.core.schema import Scene

REPO = Path(__file__).resolve().parents[2]
SCENE = REPO / "tests" / "scenes" / "composite-additions.yaml"
DT = 0.02


def doc() -> dict:
    return yaml.safe_load(SCENE.read_text(encoding="utf-8"))


def loaded(data: dict | None = None):
    return build(Scene.model_validate(data or doc(), context={"scene_dir": SCENE.parent}))


def ticks_until_running(data: dict) -> int:
    """Write start between ticks, as a PLC does; count the ticks until the PLC can read running
    true (it reads at its next scan, after the tick that set it)."""
    scene = loaded(data)
    for _ in range(5):
        scene.step(DT)
    assert scene.io.get("MX_01_RUN_DI") is False
    scene.io.set("MX_01_RUN_DO", True, Writer.PLC)
    for n in range(1, 1000):
        scene.step(DT)
        if scene.io.get("MX_01_RUN_DI"):
            return n
    raise AssertionError("never running")


def test_running_follows_start_one_tick_later_with_no_part_running_output() -> None:
    """Done-when: running one tick after start, from no part's output (the only part is a
    lamp); ``on_delay_s: 2.0`` makes it 100 ticks more at 50 Hz."""
    scene = loaded()
    assert [c.type for c in scene.components] == ["pilot_light"]
    assert ticks_until_running(doc()) == 1
    delayed = doc()
    delayed["components"][0]["io"]["running"] = {"tag": "MX_01_RUN_DI", "on_delay_s": 2.0}
    assert ticks_until_running(delayed) == 101


def test_speed_feedback_follows_speed_while_running_and_reads_0_when_stopped() -> None:
    scene = loaded()
    scene.io.set("MX_01_SPD_AO", 40.0, Writer.PLC)
    scene.step(DT)
    assert scene.io.get("MX_01_SPD_AI") == 0.0  # stopped
    scene.io.set("MX_01_RUN_DO", True, Writer.PLC)
    scene.step(DT)
    assert scene.io.get("MX_01_RUN_DI") is True
    assert scene.io.get("MX_01_SPD_AI") == 40.0  # this tick: running is evaluated first
    scene.io.set("MX_01_SPD_AO", 75.5, Writer.PLC)
    scene.step(DT)
    assert scene.io.get("MX_01_SPD_AI") == 75.5
    scene.io.set("MX_01_RUN_DO", False, Writer.PLC)
    scene.step(DT)
    assert scene.io.get("MX_01_SPD_AI") == 0.0


def test_speed_feedback_reads_the_delayed_running_the_plc_sees() -> None:
    data = doc()
    data["components"][0]["io"]["running"] = {"tag": "MX_01_RUN_DI", "on_delay_s": 0.1}
    scene = loaded(data)
    scene.io.set("MX_01_SPD_AO", 50.0, Writer.PLC)
    scene.io.set("MX_01_RUN_DO", True, Writer.PLC)
    seen = []
    for _ in range(8):
        scene.step(DT)
        seen.append((scene.io.get("MX_01_RUN_DI"), scene.io.get("MX_01_SPD_AI")))
    assert seen[:5] == [(False, 0.0)] * 5 and seen[5:] == [(True, 50.0)] * 3


def test_the_tags_export_like_any_other() -> None:
    scene = Scene.model_validate(doc(), context={"scene_dir": SCENE.parent})
    kinds = {b.name: (b.kind, b.component_id, b.port.range) for b in scene.tag_bindings()}
    assert kinds == {
        "MX_01_RUN_DO": ("DO", "MX_01_LAMP", None),
        "MX_01_SPD_AO": ("AO", "MX_01_LAMP", (0.0, 100.0)),
        "MX_01_RUN_DI": ("DI", "MX_01_LAMP", None),
        "MX_01_SPD_AI": ("AI", "MX_01_LAMP", (0.0, 100.0)),
    }


def test_a_cycle_is_a_load_error_naming_it() -> None:
    data = doc()
    io = data["composites"][0]["io"]
    io["a"] = {"from": "b and start"}
    io["b"] = {"from": "a or start"}
    data["components"][0]["io"].update({"a": "MX_01_A_DI", "b": "MX_01_B_DI"})
    with pytest.raises(ValidationError, match="io expressions read each other: a <- b <- a"):
        Scene.model_validate(data, context={"scene_dir": SCENE.parent})
    io.pop("b")
    io["a"] = {"from": "a and start"}
    with pytest.raises(ValidationError, match="a <- a"):
        Scene.model_validate(data, context={"scene_dir": SCENE.parent})


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"running": {"from": "nope"}}, "'nope' is not a part.port reference, a command"),
        ({"speed_fb": {"from": "running", "kind": "AI", "range": [0, 1]}}, "needs a number"),
        ({"running": {"from": "speed"}}, "gives a number; a DI needs true/false"),
        ({"running": {"from": "1 if speed else 0", "kind": "AI", "range": [0, 1]}}, "takes true"),
        ({"speed_fb": {"from": "speed if running else 0", "kind": "AI"}}, "needs `range"),
        ({"speed": {"command": "AO"}}, "needs `range"),
        ({"start": {"command": "DO", "range": [0, 1]}}, "`range` and `units` are for"),
        ({"x": {"to": "lamp.on", "kind": "AI", "range": [0, 1]}}, "`kind` goes with"),
    ],
)
def test_load_errors(change: dict, message: str) -> None:
    data = doc()
    data["composites"][0]["io"].update(change)
    with pytest.raises(ValidationError, match=message.replace("(", r"\(").replace("`", "`")):
        Scene.model_validate(data, context={"scene_dir": SCENE.parent})


def test_an_ao_command_in_logic_reads_a_number() -> None:
    data = doc()
    data["composites"][0]["logic"] = {"lamp.on": "start and speed > 10"}
    scene = loaded(data)
    lamp = scene.components[0]
    scene.io.set("MX_01_RUN_DO", True, Writer.PLC)
    scene.io.set("MX_01_SPD_AO", 5.0, Writer.PLC)
    scene.step(DT)
    scene.step(DT)
    assert lamp.driven["on"] is False
    scene.io.set("MX_01_SPD_AO", 50.0, Writer.PLC)
    scene.step(DT)
    scene.step(DT)
    assert lamp.driven["on"] is True
