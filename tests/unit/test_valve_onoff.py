"""valve_onoff (PLAN 3.2, ADR-0005): travel, limit switches, flow coefficient, faults."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from openfactory.core.iotable import Writer
from openfactory.core.loader import build
from openfactory.core.schema import Scene

SUP = {
    "id": "SUP_01",
    "type": "supply",
    "description": "Supply",
    "position": [0, 0, 0],
    "pressure_bar": 1.0,
}
DR = {"id": "DR_01", "type": "drain", "description": "Open Drain", "position": [2, 0, 0]}
XV = {
    "id": "XV_01", "type": "valve_onoff", "description": "Water Inlet On/Off Valve",
    "position": [1, 0, 0], "from": "SUP_01", "to": "DR_01", "cv": 10, "travel_s": 2.0,
    "io": {"open": "XV_01_OPEN_DO", "opened": "XV_01_ZSO_DI", "closed": "XV_01_ZSC_DI"},
}  # fmt: skip


def scene(*components: dict) -> dict:
    return {"openfactory": "0.1", "scene": {"name": "xv-test", "description": "Valve test."},
            "plc": {"protocol": "none"}, "components": list(components)}  # fmt: skip


def sim(**overrides: object):  # type: ignore[no-untyped-def]
    return build(Scene.model_validate(scene(SUP, DR, {**XV, **overrides})))


def run(s, seconds: float) -> None:  # type: ignore[no-untyped-def]
    for _ in range(round(seconds / 0.02)):
        s.step(0.02)


def limits(s) -> tuple[bool, bool]:  # type: ignore[no-untyped-def]
    return s.io.get("XV_01_ZSO_DI"), s.io.get("XV_01_ZSC_DI")


def test_it_starts_closed_travels_open_and_passes_kv_times_root_dp():
    s = sim()
    xv = s.component("XV_01")
    run(s, 0.1)
    assert limits(s) == (False, True) and xv.state()["flow_m3h"] == 0.0
    s.io.set("XV_01_OPEN_DO", True, Writer.PLC)
    run(s, 1.0)
    assert xv.opening == pytest.approx(0.5) and limits(s) == (False, False)
    run(s, 1.2)
    assert limits(s) == (True, False)
    # 1 bar across a full-open valve: Kv m³/h, Kv = 0.865 Cv
    assert xv.state()["flow_m3h"] == pytest.approx(8.65, rel=1e-3)
    s.io.set("XV_01_OPEN_DO", False, Writer.PLC)
    run(s, 0.5)
    assert xv.opening == pytest.approx(0.75) and limits(s) == (False, False)
    s.io.set("XV_01_OPEN_DO", True, Writer.PLC)  # reverses at once mid-travel
    run(s, 0.1)
    assert xv.opening == pytest.approx(0.8)


def test_kv_can_be_given_instead_of_cv_but_not_both():
    s = sim(cv=None, kv=4.0)
    s.io.set("XV_01_OPEN_DO", True, Writer.PLC)
    run(s, 2.2)
    assert s.component("XV_01").state()["flow_m3h"] == pytest.approx(4.0, rel=1e-3)
    with pytest.raises(ValidationError, match="give cv or kv, not both"):
        Scene.model_validate(scene(SUP, DR, {**XV, "kv": 4.0}))


def test_faults_jammed_holds_slow_halves_and_stuck_holds_a_limit():
    s = sim()
    xv = s.component("XV_01")
    s.io.set("XV_01_OPEN_DO", True, Writer.PLC)
    run(s, 0.5)
    xv.fault_inject("jammed")
    run(s, 1.0)
    assert xv.opening == pytest.approx(0.25) and limits(s) == (False, False)
    xv.fault_inject("slow", factor=0.5)
    run(s, 1.0)
    assert xv.opening == pytest.approx(0.5)
    xv.fault_inject("stuck_on", port="closed")
    run(s, 0.02)
    assert limits(s) == (False, True)


def test_its_drawing_joins_its_ends_through_its_body():
    xv = sim().component("XV_01")
    assert xv.geometry()["points"] == [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [2.0, 0.0, 0.0]]
