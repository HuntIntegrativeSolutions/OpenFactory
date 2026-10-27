"""stopper: raise/lower timing and limits, a hard stop for the queue, pass-through, sizing, W206."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from openfactory.core.iotable import Writer
from openfactory.core.loader import build, load
from openfactory.core.schema import Scene
from openfactory.lint import lint_scene

REPO = Path(__file__).resolve().parents[2]
QUEUE = REPO / "tests" / "scenes" / "stopper-queue.yaml"
DT = 0.02
STP = {
    "id": "STP_01",
    "type": "stopper",
    "description": "Accumulation Belt Stopper",
    "position": [3.2, 0, 0],
    "raise_time_s": 0.2,  # 10 ticks
    "io": {"raise": "STP_01_RAISE_DO", "raised": "STP_01_UP_DI", "lowered": "STP_01_DN_DI"},
}
BELT = {
    "id": "CONV_A",
    "type": "belt_conveyor",
    "description": "Accumulation Belt Conveyor",
    "position": [0, 0, 0],
    "length": 4.0,
    "width": 0.6,
    "speed_mps": 0.5,
    "io": {"run": "CONV_A_RUN_DO"},
}


def scene(*components: dict) -> Scene:
    return Scene.model_validate(
        {
            "openfactory": "0.1",
            "scene": {"name": "stopper", "description": "stopper test"},
            "components": list(components),
        }
    )


def limits(r):
    return r.io.get("STP_01_UP_DI"), r.io.get("STP_01_DN_DI")


def test_raise_and_lower_take_raise_time_with_limits_only_at_the_ends(rig):
    r = rig(STP)
    r.step()
    assert limits(r) == (False, True) and r["STP_01"].blocking is False
    r.plc("STP_01_RAISE_DO", True)
    r.step(9)
    assert limits(r) == (False, False) and r["STP_01"].position == pytest.approx(0.9)
    assert r["STP_01"].blocking is True  # any height above fully lowered holds parts
    r.step()
    assert limits(r) == (True, False)
    r.plc("STP_01_RAISE_DO", False)
    r.step(5)
    assert limits(r) == (False, False) and r["STP_01"].state() == {
        "position": pytest.approx(0.5),
        "raising": False,
    }
    r.step(5)
    assert limits(r) == (False, True)


def test_unset_width_and_height_are_sized_from_the_belt_and_the_parts():
    emitter = {
        "id": "EMT_01",
        "type": "box_emitter",
        "description": "Box Source",
        "position": [0.3, 0.3, 0],
        "interval_s": 2.0,
        "parts": [{"name": "tall", "size": [0.3, 0.4, 0.3], "weight": 1.0}],
    }
    loaded = build(scene(BELT, STP, emitter))
    cfg = loaded.component("STP_01").config
    assert (cfg.width, cfg.height) == (0.6, 0.2)  # belt width; the tall box's centre of mass
    geometry = loaded.component("STP_01").geometry()
    assert (geometry["width"], geometry["height"], geometry["thickness"]) == (0.6, 0.2, 0.02)
    bare = build(scene({**STP, "position": [9, 0, 9]})).component("STP_01").config
    assert (bare.width, bare.height) == (0.5, 0.05)  # no belt, no emitter: the defaults


def run_queue(ticks: int, lower_at: int | None = None):
    loaded = load(QUEUE)
    for tag in ("CONV_A_RUN_DO", "CONV_B_RUN_DO", "STP_01_RAISE_DO"):
        loaded.io.set(tag, True, Writer.PLC)
    for k in range(1, ticks + 1):
        if k == lower_at:
            loaded.io.set("STP_01_RAISE_DO", False, Writer.PLC)
        loaded.step(DT)
    return loaded


def test_five_boxes_pile_nose_to_tail_against_the_raised_blade():
    loaded = run_queue(400)
    xs = sorted((p.position[0] for p in loaded.world.parts.values()), reverse=True)
    print("queue centres:", [round(x, 4) for x in xs])
    assert xs[0] == pytest.approx(3.2 - 0.15)  # front face on the blade's upstream face
    gaps = [round(a - b - 0.3, 6) for a, b in zip(xs, xs[1:], strict=False)]
    assert gaps == [0.0, 0.0, 0.0, 0.0]
    assert all(p.v_flow == 0.0 for p in loaded.world.parts.values())


def test_lowering_releases_the_queue_in_order():
    loaded = run_queue(650, lower_at=400)
    assert loaded.component("STP_01").position == 0.0
    removed = loaded.world.removed  # REM_B, past the end of CONV_B
    ahead = sorted(loaded.world.parts.values(), key=lambda p: -p.position[0])
    print(
        "removed", dict(removed), "still moving", [(p.name, round(p.position[0], 2)) for p in ahead]
    )
    assert sum(removed.values()) + len(ahead) == 5 and all(p.v_flow > 0 for p in ahead)
    order = [name for name in ("q1", "q2", "q3", "q4", "q5") if removed[name]]
    assert order == ["q1", "q2", "q3", "q4", "q5"][: len(order)]  # first in, first out
    assert [p.name for p in ahead] == ["q1", "q2", "q3", "q4", "q5"][len(order) :]


def test_a_part_already_over_the_rising_blade_passes_and_the_next_one_stops():
    loaded = build(scene(BELT, STP))
    loaded.io.set("CONV_A_RUN_DO", True, Writer.PLC)
    over = loaded.world.spawn("a", (0.3, 0.2, 0.3), (3.25, 0.1, 0.0))  # straddles x = 3.2
    behind = loaded.world.spawn("b", (0.3, 0.2, 0.3), (2.5, 0.1, 0.0))
    loaded.io.set("STP_01_RAISE_DO", True, Writer.PLC)
    for _ in range(150):
        loaded.step(DT)
    assert over.id not in loaded.world.parts or over.position[0] > 3.6  # carried on, off the end
    assert behind.position[0] == pytest.approx(3.05)


def test_a_low_blade_set_by_hand_warns_w206(tmp_path):
    data = yaml.safe_load(QUEUE.read_text(encoding="utf-8"))
    stp = next(c for c in data["components"] if c["id"] == "STP_01")
    stp["height"] = 0.06
    path = tmp_path / QUEUE.name
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    w206 = [f for f in lint_scene(path) if f.code == "W206"]
    print("\n".join(str(f) for f in w206))
    assert len(w206) == 5 and "raised blade top 0.06 m above CONV_A" in w206[0].message
    assert not [f for f in lint_scene(QUEUE) if f.code == "W206"]  # default: sized to 0.1 m
