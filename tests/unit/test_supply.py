"""supply (PLAN 3.2, ADR-0005): a fixed-pressure source node that delivers what is drawn."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from openfactory.core.flownet import BAR_HEAD, Resistance, valve_resistance
from openfactory.core.loader import build
from openfactory.core.schema import Scene

SUP = {"id": "SUP_01", "type": "supply", "description": "City Water Supply",
       "position": [0, 1.0, 0], "pressure_bar": 2.0, "temperature_c": 12.0,
       "ingredient": "water"}  # fmt: skip
TK = {
    "id": "TK_01",
    "type": "tank",
    "description": "Water Tank",
    "position": [1, 0, 0],
    "initial_level": 0.5,
}


def scene(*components: dict) -> dict:
    return {"openfactory": "0.1", "scene": {"name": "supply-test", "description": "Supply test."},
            "plc": {"protocol": "none"}, "components": list(components)}  # fmt: skip


def test_its_head_is_its_elevation_plus_its_pressure_and_it_delivers_what_is_drawn():
    sim = build(Scene.model_validate(scene(SUP, TK)))
    sup, tank = sim.component("SUP_01"), sim.component("TK_01")
    assert sup.node.head == pytest.approx(1.0 + 2.0 * BAR_HEAD)
    # a valve from the supply into the tank's top, added to the built network by hand
    feed = Resistance("FV_01", "SUP_01", "TK_01.top", r=valve_resistance(10, 1.0))
    feed.start, feed.end = (sup.node, None), (tank.node, "top")
    sim.flownet.links.append(feed)  # type: ignore[union-attr]
    for _ in range(50):
        sim.step(0.02)
    q = (sup.node.head - (tank.node.elevation + 2.0)) ** 0.5 / valve_resistance(10, 1.0) ** 0.5
    assert sup.state()["flow_m3h"] == pytest.approx(q * 3600, rel=1e-3)
    assert sup.node.delivered == pytest.approx(tank.node.volume - tank.node.area * 0.5, rel=1e-9)
    assert tank.node.temperature < 20.0 and tank.node.fractions == pytest.approx({"water": 1.0})


def test_it_has_no_ports_or_faults_and_validates_its_values():
    sim = build(Scene.model_validate(scene(SUP)))
    sup = sim.component("SUP_01")
    assert sup.ports == {} and sup.FAULTS == ()
    assert sup.geometry()["pressure_bar"] == 2.0
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        Scene.model_validate(scene({**SUP, "pressure_bar": -1}))
    with pytest.raises(ValidationError, match="String should match pattern"):
        Scene.model_validate(scene({**SUP, "ingredient": "Syrup"}))
