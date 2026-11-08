"""drain (PLAN 3.2, ADR-0005): an open drain takes whatever arrives; a consumer draws its demand
pressure-dependently, on a schedule that can repeat."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from openfactory.core import flownet
from openfactory.core.component import REGISTRY
from openfactory.core.loader import build, build_iotable
from openfactory.core.schema import Scene
from openfactory.core.world import World

TK = {"id": "TK_01", "type": "tank", "description": "Head Tank", "position": [0, 3.0, 0],
      "height": 2.0, "initial_level": 1.5}  # fmt: skip
DR = {
    "id": "DR_01",
    "type": "drain",
    "description": "Process Water Consumer",
    "position": [2, 0, 0],
}


def scene(*components: dict) -> dict:
    return {"openfactory": "0.1", "scene": {"name": "drain-test", "description": "Drain test."},
            "plc": {"protocol": "none"}, "components": list(components)}  # fmt: skip


class Sim:
    """The tank and the drain built by hand and joined by a pipe (3.2's pipe comes later), stepped
    as LoadedScene steps them: the network, then the components."""

    def __init__(self, drain: dict, r: float = 2e5) -> None:
        parsed = Scene.model_validate(scene(TK, drain))
        self.io, world = build_iotable(parsed), World(seed=0)
        self.tank, self.drain = (
            REGISTRY.resolve(c.type)(c, self.io, world) for c in parsed.components
        )
        self.pipe = flownet.Resistance("PIPE_01", "TK_01.bottom", "DR_01", r=r)
        self.net = flownet.Network.build([self.tank.node, self.drain.node], [self.pipe])

    def step(self, dt: float = 0.02) -> None:
        self.net.step(dt)
        self.tank.step(dt)
        self.drain.step(dt)


def test_an_open_drain_takes_whatever_arrives_at_atmospheric_head():
    sim = Sim(DR)
    assert isinstance(sim.drain.node, flownet.FixedHead) and sim.drain.node.head == 0.0
    sim.step()
    sim.step()
    assert sim.pipe.flow == pytest.approx(((3.0 + sim.tank.node.level) / 2e5) ** 0.5, rel=1e-3)
    assert sim.drain.state() == {"flow_m3h": pytest.approx(sim.pipe.flow * 3600, rel=1e-3)}


def test_a_consumer_draws_its_demand_while_the_pressure_holds():
    sim = Sim({**DR, "demand_m3h": 1.2})
    assert isinstance(sim.drain.node, flownet.Junction)
    for _ in range(10):
        sim.step()
    assert sim.drain.state()["flow_m3h"] == pytest.approx(1.2, rel=1e-3)
    assert sim.drain.state()["demand_m3h"] == pytest.approx(1.2)


def test_the_schedule_steps_the_demand_and_can_repeat():
    sim = Sim({**DR, "schedule": [[0, 1.0], [1.0, 2.0], [2.0, 1.0]], "repeat": True})
    seen = {}
    for tick in range(1, 200):
        sim.step()
        seen[round(tick * 0.02, 2)] = sim.drain.state()["demand_m3h"]
    assert seen[0.5] == 1.0 and seen[1.5] == 2.0 and seen[2.5] == 1.0 and seen[3.5] == 2.0
    # the demand set at a tick is drawn from the next solve
    assert sim.drain.state()["flow_m3h"] == pytest.approx(seen[round(198 * 0.02, 2)], rel=1e-3)


def test_a_consumer_with_no_link_fails_to_build_and_says_why():
    with pytest.raises(ValueError, match="DR_01: no link connects to this junction"):
        build(Scene.model_validate(scene(TK, {**DR, "demand_m3h": 1.0})))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"schedule": [[0, 1.0], [0, 2.0]]}, "schedule times must rise"),
        ({"schedule": [[0, 1.0]], "repeat": True}, "repeat needs a schedule"),
        (
            {"demand_m3h": 1.0, "minimum_head_m": 3.0},
            "minimum_head_m must be below required_head_m",
        ),
        ({"demand_m3h": -1.0}, "greater than or equal to 0"),
    ],
)
def test_a_bad_drain_fails_at_load(overrides, message):
    with pytest.raises(ValidationError, match=message):
        Scene.model_validate(scene({**DR, **overrides}))
