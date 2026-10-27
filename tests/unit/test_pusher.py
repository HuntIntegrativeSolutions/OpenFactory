"""pusher: full extend/retract cycle with limit switches, mid-stroke reversal, plate sizing."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from openfactory.components.pusher import push_path, resolve_plate_height
from openfactory.core.schema import Scene

SORTER = Path(__file__).resolve().parents[2] / "scenes" / "sorting-by-height.yaml"

CYL = {
    "id": "CYL_01",
    "type": "pusher",
    "description": "Tall Box Divert Pusher",
    "position": [2.5, 0.1, -0.3],
    "stroke": 0.5,
    "extend_time_s": 0.4,  # 20 ticks at 50 Hz
    "io": {"extend": "CYL_01_EXT_DO", "extended": "CYL_01_EXT_DI", "retracted": "CYL_01_RET_DI"},
}


@pytest.fixture
def cyl(rig):
    return rig(CYL)


def limits(r):
    return r.io.get("CYL_01_EXT_DI"), r.io.get("CYL_01_RET_DI")


def test_full_cycle_limit_switches(cyl):
    pusher = cyl["CYL_01"]
    cyl.step()
    assert limits(cyl) == (False, True)  # starts retracted

    cyl.plc("CYL_01_EXT_DO", True)
    cyl.step()
    assert limits(cyl) == (False, False)  # left the retracted switch
    cyl.step(18)  # 19 ticks = 0.38 s
    assert limits(cyl) == (False, False)  # mid-stroke: both false
    assert pusher.position == pytest.approx(0.475)
    cyl.step()  # 20 ticks = 0.40 s
    assert limits(cyl) == (True, False)
    assert pusher.position == 0.5
    cyl.step(10)  # holds extended
    assert limits(cyl) == (True, False)

    cyl.plc("CYL_01_EXT_DO", False)
    cyl.step()
    assert limits(cyl) == (False, False)
    cyl.step(19)
    assert limits(cyl) == (False, True)
    assert pusher.position == 0.0


def test_reverses_mid_stroke(cyl):
    pusher = cyl["CYL_01"]
    cyl.plc("CYL_01_EXT_DO", True)
    cyl.step(10)
    assert pusher.position == pytest.approx(0.25)
    cyl.plc("CYL_01_EXT_DO", False)
    cyl.step(5)
    assert pusher.position == pytest.approx(0.125)
    assert limits(cyl) == (False, False)
    cyl.step(5)
    assert limits(cyl) == (False, True)


def test_state_and_geometry(cyl):
    cyl.plc("CYL_01_EXT_DO", True)
    cyl.step(4)
    state = cyl["CYL_01"].state()
    assert state["extending"] is True
    assert state["position_m"] == pytest.approx(0.1)
    assert cyl["CYL_01"].geometry()["stroke"] == 0.5


def test_limit_ports_are_optional(rig):
    r = rig({**CYL, "io": {"extend": "CYL_01_EXT_DO"}})
    r.plc("CYL_01_EXT_DO", True)
    r.step(25)
    assert r["CYL_01"].position == 0.5


def _sorter_with(**pusher) -> Scene:
    data = yaml.safe_load(SORTER.read_text(encoding="utf-8"))
    cyl = next(c for c in data["components"] if c["id"] == "CYL_01")
    cyl.update(pusher)
    return Scene.model_validate(data)


def test_unset_plate_height_reaches_above_the_tallest_parts_centre_of_mass():
    scene = _sorter_with(position=[2.5, 0.1, -0.3], stroke=0.5)  # the old sorter values
    cfg = next(c for c in scene.components if c.id == "CYL_01")
    assert cfg.plate_height is None
    path = push_path(cfg, scene)
    assert (path.belt_id, path.belt_y) == ("CONV_01", 0.0)
    assert path.near_m == pytest.approx(0.05) and path.far_m == pytest.approx(0.55)
    # tallest box 0.4 m: top at 0.75 x 0.4 = 0.3 m, centred on y 0.1 -> 0.4 m tall
    assert resolve_plate_height(cfg, scene).plate_height == pytest.approx(0.4)
    explicit = cfg.model_copy(update={"plate_height": 0.15})
    assert resolve_plate_height(explicit, scene).plate_height == 0.15  # set values are kept


def test_default_plate_height_without_emitters_or_belts_is_0_15(rig):
    assert rig(CYL)["CYL_01"].config.plate_height == 0.15  # no belt, no emitter in the rig
