"""transmitter (PLAN 3.2, ADR-0005 decision 7): every quantity it measures, its range and units,
the lag and seeded noise, and its faults, signal_fail at the NE 43 levels included."""

from __future__ import annotations

import statistics

import pytest
from pydantic import ValidationError

from openfactory.core.iotable import Writer
from openfactory.core.loader import build
from openfactory.core.schema import Scene
from openfactory.protocols.modbus import encode

SUP = {"id": "SUP_01", "type": "supply", "description": "Syrup Supply", "position": [0, 1, 0],
       "pressure_bar": 1.0, "ingredient": "syrup"}  # fmt: skip
TK = {"id": "TK_01", "type": "tank", "description": "Mix Tank", "position": [3, 0, 0],
      "height": 2.0, "initial_level": 1.0}  # fmt: skip
FV = {"id": "FV_01", "type": "valve_control", "description": "Syrup Valve", "position": [1, 1, 0],
      "from": "SUP_01", "to": "PIPE_01", "kv": 4.0, "io": {"position": "FV_01_POS_AO"}}  # fmt: skip
PIPE = {"id": "PIPE_01", "type": "pipe", "description": "Syrup Line", "position": [2, 1, 0],
        "from": "FV_01", "to": "J_01", "length": 2.0}  # fmt: skip
J = {"id": "J_01", "type": "junction", "description": "Inlet Tee", "position": [2.5, 1.0, 0]}
PIPE2 = {"id": "PIPE_02", "type": "pipe", "description": "Tank Inlet", "position": [2.8, 1, 0],
         "from": "J_01", "to": "TK_01.top", "length": 1.0}  # fmt: skip


def lt(**extra: object) -> dict:
    base = {"id": "LT_01", "type": "transmitter", "description": "Mix Tank Level Transmitter",
            "position": [3.6, 0.5, 0], "measures": "TK_01.level", "range": [0, 2.0],
            "io": {"pv": "TK_01_LT_AI"}}  # fmt: skip
    return {**base, **extra}


def scene(*components: dict, encoding: str = "float32") -> dict:
    return {"openfactory": "0.1", "scene": {"name": "tx-test", "description": "Transmitters."},
            "plc": {"protocol": "modbus_tcp", "analog_encoding": encoding},
            "components": list(components)}  # fmt: skip


def plant(*transmitters: dict, encoding: str = "float32"):  # type: ignore[no-untyped-def]
    s = build(
        Scene.model_validate(scene(SUP, TK, FV, PIPE, J, PIPE2, *transmitters, encoding=encoding))
    )
    s.io.set("FV_01_POS_AO", 100.0, Writer.PLC)
    return s


def run(s, seconds: float) -> None:  # type: ignore[no-untyped-def]
    for _ in range(round(seconds / 0.02)):
        s.step(0.02)


def test_each_quantity_reads_its_value_in_its_units():
    s = plant(
        lt(),
        lt(id="TT_01", measures="TK_01.temperature", range=[0, 100], io={"pv": "TK_01_TT_AI"}),
        lt(id="AT_01", measures="TK_01.fraction.syrup", range=[0, 100], io={"pv": "TK_01_AT_AI"}),
        lt(id="FT_01", measures="PIPE_01.flow", range=[0, 5], io={"pv": "PIPE_01_FT_AI"}),
        lt(id="PT_01", measures="J_01.pressure", range=[0, 1], io={"pv": "J_01_PT_AI"}),
    )
    run(s, 5.0)
    tank, pipe, joint = (
        s.component("TK_01").node,
        s.component("PIPE_01").link,
        s.component("J_01").node,
    )
    assert s.io.get("TK_01_LT_AI") == pytest.approx(tank.level)
    assert s.io.get("TK_01_TT_AI") == pytest.approx(tank.temperature)
    assert (
        s.io.get("TK_01_AT_AI") == pytest.approx(tank.fractions["syrup"] * 100)
        and tank.fractions["syrup"] > 0
    )
    assert s.io.get("PIPE_01_FT_AI") == pytest.approx(pipe.flow * 3600) and pipe.flow > 0
    assert (
        s.io.get("J_01_PT_AI") == pytest.approx(joint.pressure_bar) and 0 < joint.pressure_bar < 1
    )
    units = {
        t: s.io.tag(t).units
        for t in ("TK_01_LT_AI", "TK_01_TT_AI", "TK_01_AT_AI", "PIPE_01_FT_AI", "J_01_PT_AI")
    }
    assert units == {"TK_01_LT_AI": "m", "TK_01_TT_AI": "°C", "TK_01_AT_AI": "%",
                     "PIPE_01_FT_AI": "m³/h", "J_01_PT_AI": "bar"}  # fmt: skip


def test_the_range_scales_int16_and_units_can_be_named():
    s = plant(lt(range=[0, 2.5], units="metres"), encoding="int16_scaled")
    tag = s.io.tag("TK_01_LT_AI")
    assert (tag.range, tag.units) == ((0.0, 2.5), "metres")
    assert encode(tag, 1.25, "int16_scaled") == [16384]


def test_the_lag_filters_a_step():
    s = plant(lt(lag_s=2.0))
    s.step(0.02)
    tank = s.component("TK_01").node
    start = s.io.get("TK_01_LT_AI")
    tank.volume += tank.area * 0.5  # the level steps up 0.5 m
    run(s, 2.0)
    risen = s.io.get("TK_01_LT_AI") - start
    assert risen == pytest.approx(0.5 * (1 - 0.99**100), abs=0.02)  # ~63 % of the step


def test_noise_is_seeded_and_has_its_standard_deviation():
    def readings() -> list[float]:
        s = plant(lt(noise=0.01))  # 1 % of a 2 m span: 0.02 m
        out = []
        for _ in range(500):
            s.step(0.02)
            out.append(s.io.get("TK_01_LT_AI") - s.component("TK_01").node.level)
        return out

    first = readings()
    assert first == readings()
    assert statistics.pstdev(first) == pytest.approx(0.02, rel=0.15)


def test_faults_stuck_and_signal_fail_at_the_ne43_levels():
    s = plant(lt(range=[0, 2.0]))
    tx = s.component("LT_01")
    for kind, params, expected in (
        ("stuck_on", {}, 2.0),
        ("stuck_off", {}, 0.0),
        ("signal_fail", {}, -0.05),  # 3.6 mA: 0.4/16 of the span below 4 mA
        ("signal_fail", {"high": 1}, 2.125),  # 21 mA: 1/16 above 20 mA
    ):
        tx.fault_inject(kind, **params)
        s.step(0.02)
        assert s.io.get("TK_01_LT_AI") == pytest.approx(expected), kind
    int16 = plant(lt(), encoding="int16_scaled")
    int16.component("LT_01").fault_inject("signal_fail")
    int16.step(0.02)
    assert encode(int16.io.tag("TK_01_LT_AI"), int16.io.get("TK_01_LT_AI"), "int16_scaled") == [0]


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ({"measures": "TK_09.level"}, "TK_09 is no component; level is measured on a tank"),
        ({"measures": "TK_01.flow"}, "TK_01 is a tank; flow is measured on a pipe, pump"),
        ({"measures": "TK_01.volume"}, "String should match pattern"),
        ({"range": [2.0, 0.0]}, "range \\[lo, hi\\] needs lo < hi"),
    ],
)
def test_a_bad_transmitter_fails_at_load(extra, message):
    with pytest.raises(ValidationError, match=message):
        Scene.model_validate(scene(SUP, TK, FV, PIPE, J, PIPE2, lt(**extra)))


def test_an_open_drain_has_no_pressure_to_measure():
    dr = {"id": "DR_01", "type": "drain", "description": "Open Drain", "position": [5, 0, 0]}
    with pytest.raises(ValidationError, match="DR_01 is an open drain, always at atmospheric"):
        Scene.model_validate(scene(TK, dr, lt(measures="DR_01.pressure")))


def test_context_md_lists_signal_fail_only_where_a_transmitter_can_have_it(tmp_path):
    from openfactory.export.context_md import context_md

    with_tx = Scene.model_validate(scene(SUP, TK, FV, PIPE, J, PIPE2, lt()))
    without = Scene.model_validate(scene(SUP, TK, FV, PIPE, J, PIPE2))
    assert "- `signal_fail`:" in context_md(with_tx, tmp_path / "a.yaml")
    assert "- `signal_fail`:" not in context_md(without, tmp_path / "b.yaml")
