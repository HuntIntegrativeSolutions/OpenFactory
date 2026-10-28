"""diverter_arm: swing timing and limits, sizing, the fence, diverting and passing, W206."""

from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from openfactory.core.iotable import Writer
from openfactory.core.loader import load
from openfactory.core.schema import Scene
from openfactory.lint import lint_scene

REPO = Path(__file__).resolve().parents[2]
SCENE = REPO / "tests" / "scenes" / "demo-diverter.yaml"
DT = 0.02
ARM = {
    "id": "DIV_01",
    "type": "diverter_arm",
    "description": "Main Line Divert Arm",
    "position": [2.6, 0, -0.25],
    "length": 0.78,
    "swing_deg": 45,
    "extend_time_s": 0.5,  # 25 ticks
    "io": {"extend": "DIV_01_EXT_DO", "extended": "DIV_01_EXT_DI", "retracted": "DIV_01_RET_DI"},
}


def limits(r):
    return r.io.get("DIV_01_EXT_DI"), r.io.get("DIV_01_RET_DI")


@pytest.mark.parametrize("swing", [45, -30])
def test_swing_timing_and_limit_switches(rig, swing):
    r = rig({**ARM, "swing_deg": swing})
    r.step()
    assert limits(r) == (False, True)
    r.plc("DIV_01_EXT_DO", True)
    r.step(12)
    assert limits(r) == (False, False)
    assert r["DIV_01"].angle == pytest.approx(swing * 12 / 25)
    r.step(13)
    assert limits(r) == (True, False) and r["DIV_01"].state()["angle_deg"] == swing
    r.plc("DIV_01_EXT_DO", False)
    r.step(25)
    assert limits(r) == (False, True)


def test_height_defaults_and_a_zero_swing_is_rejected(rig):
    assert rig(ARM)["DIV_01"].config.height == 0.15  # no emitter in the rig
    assert load(SCENE).component("DIV_01").config.height == pytest.approx(0.15)  # 0.75 x 0.2 m
    assert rig({**ARM, "height": 0.3})["DIV_01"].config.height == 0.3
    with pytest.raises(ValidationError, match="swing_deg must not be 0"):
        Scene.model_validate(
            {
                "openfactory": "0.1",
                "scene": {"name": "x", "description": "x"},
                "components": [{**ARM, "swing_deg": 0}],
            }
        )


def run(ticks: int, retract_at: int | None = None):
    loaded = load(SCENE)
    for tag in ("CONV_01_RUN_DO", "CONV_SIDE_RUN_DO", "DIV_01_EXT_DO"):
        loaded.io.set(tag, True, Writer.PLC)
    track = []
    for k in range(1, ticks + 1):
        if k == retract_at:
            loaded.io.set("DIV_01_EXT_DO", False, Writer.PLC)
        loaded.step(DT)
        track.append({p.name: (p.position, p.carrier) for p in loaded.world.parts.values()})
    return loaded, track


def test_an_extended_arm_slides_a_box_along_it_onto_the_side_belt():
    _, track = run(200)
    # while fenced, the box moves along the arm at 0.5 m/s x cos 45
    (x0, _, z0), _ = track[129]["a"]
    (x1, _, z1), _ = track[139]["a"]
    speed = math.hypot(x1 - x0, z1 - z0) / (10 * DT)
    print(f"box a along the arm: {speed:.4f} m/s (0.5 cos 45 = {0.5 * math.cos(math.pi / 4):.4f})")
    assert speed == pytest.approx(0.5 * math.cos(math.pi / 4), abs=1e-6)
    assert z1 - z0 == pytest.approx(x1 - x0)  # along the 45 degree arm
    (x, _, z), carrier = track[-1]["a"]
    print(f"box a after 4 s: x {x:.3f}, z {z:.3f} on {carrier}")
    # slid until its corner passed the arm tip at z 0.30: fully on the side belt (z 0.25 to 0.75)
    assert carrier == "CONV_SIDE" and z == pytest.approx(0.45, abs=0.01)


def test_the_core_places_a_box_while_it_touches_the_arm():
    """Ruling 2026-10-27 (PLAN §3.3): parts in contact with an arm follow the core in the viewer."""
    loaded = load(SCENE)
    for tag in ("CONV_01_RUN_DO", "CONV_SIDE_RUN_DO", "DIV_01_EXT_DO"):
        loaded.io.set(tag, True, Writer.PLC)
    guided = []
    for k in range(1, 251):
        loaded.step(DT)
        a = next(p for p in loaded.world.parts.values() if p.name == "a")
        if loaded.kinematics.follows_core(a):
            guided.append(k)
    print(f"box a guided on {len(guided)} ticks, {guided[0]} to {guided[-1]}")
    assert 90 < guided[0] < 130 and guided[-1] < 200  # from first contact; free on CONV_SIDE
    assert a.carrier == "CONV_SIDE" and not loaded.kinematics.follows_core(a)


def test_divert_then_pass_reaches_each_remover():
    loaded, _ = run(450, retract_at=175)
    print("removed:", dict(loaded.world.removed), "parts left:", list(loaded.world.parts))
    remover = {c.id: c for c in loaded.components if c.type == "part_remover"}
    assert dict(remover["REM_SIDE"].removed) == {"a": 1}
    b = next(p for p in loaded.world.parts.values() if p.name == "b")
    assert b.carrier == "CONV_01" and b.position[2] == pytest.approx(0.0)  # passed straight on


def test_a_low_arm_set_by_hand_warns_w206(tmp_path):
    data = yaml.safe_load(SCENE.read_text(encoding="utf-8"))
    next(c for c in data["components"] if c["id"] == "DIV_01")["height"] = 0.05
    path = tmp_path / SCENE.name
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    w206 = [f for f in lint_scene(path) if f.code == "W206"]
    print("\n".join(str(f) for f in w206))
    assert len(w206) == 2 and "arm top 0.05 m above CONV_01" in w206[0].message
