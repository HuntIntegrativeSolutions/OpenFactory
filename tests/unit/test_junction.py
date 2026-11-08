"""junction (PLAN 3.2, ADR-0005): a node whose head is solved; its pressure, gauge."""

from __future__ import annotations

import pytest

from openfactory.core import flownet
from openfactory.core.component import REGISTRY
from openfactory.core.loader import build, build_iotable
from openfactory.core.schema import Scene
from openfactory.core.world import World


def scene(*components: dict) -> dict:
    return {"openfactory": "0.1", "scene": {"name": "junction-test", "description": "Junction."},
            "plc": {"protocol": "none"}, "components": list(components)}  # fmt: skip


SUP = {
    "id": "SUP_01",
    "type": "supply",
    "description": "Supply",
    "position": [0, 0, 0],
    "pressure_bar": 2.0,
}
DR = {"id": "DR_01", "type": "drain", "description": "Open Drain", "position": [4, 0, 0]}
J = {"id": "J_01", "type": "junction", "description": "Header Tee", "position": [2, 1.0, 0]}


def test_its_head_is_solved_and_its_pressure_is_gauge_above_its_height():
    parsed = Scene.model_validate(scene(SUP, DR, J))
    io, world = build_iotable(parsed), World(seed=0)
    sup, dr, j = (REGISTRY.resolve(c.type)(c, io, world) for c in parsed.components)
    r1, r2 = 1e5, 3e5
    links = [flownet.Resistance("V_01", "SUP_01", "J_01", r=r1),
             flownet.Resistance("V_02", "J_01", "DR_01", r=r2)]  # fmt: skip
    net = flownet.Network.build([sup.node, dr.node, j.node], links)
    net.step(0.02)
    q = (sup.node.head / (r1 + r2)) ** 0.5
    assert j.node.head == pytest.approx(sup.node.head - r1 * q * q, rel=1e-6)
    assert j.state()["pressure_bar"] == pytest.approx((j.node.head - 1.0) / flownet.BAR_HEAD)
    links[0].r = float("inf")  # cut off from the supply: it settles at the open drain's head
    net.step(0.02)
    assert links[1].flow == pytest.approx(0.0, abs=1e-12) and j.node.head == pytest.approx(0.0)
    links[1].r = float("inf")  # cut off from every node of known head: no flow, no pressure
    net.step(0.02)
    assert links[1].flow == 0.0 and j.state()["pressure_bar"] == 0.0
    assert j.ports == {} and j.FAULTS == ()


def test_a_junction_no_link_reaches_fails_to_load():
    with pytest.raises(ValueError, match="J_01: no link connects to this junction"):
        build(Scene.model_validate(scene(SUP, J)))
