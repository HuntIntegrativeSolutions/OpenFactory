"""PLAN 2.6 fault injection: every kind on the components that support it, validation, and
determinism. The rig steps components through Component.tick, as LoadedScene.step does."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import openfactory.components  # noqa: F401 - registers the built-in types
from openfactory.core.component import FAULT_KINDS, REGISTRY
from openfactory.core.iotable import Writer
from openfactory.core.loader import build, load
from openfactory.core.schema import Scene

REPO = Path(__file__).resolve().parents[2]
SORTER = REPO / "scenes" / "sorting-by-height.yaml"
DT = 0.02
PE = {
    "id": "PE_01",
    "type": "photo_eye",
    "description": "Photo Eye",
    "position": [1.0, 0.1, 0.0],
    "beam_length": 0.6,
    "io": {"detect": "PE_01_DI"},
}
CYL = {
    "id": "CYL_01",
    "type": "pusher",
    "description": "Pusher",
    "position": [0, 0.1, -0.3],
    "stroke": 0.5,
    "extend_time_s": 0.4,  # 20 ticks
    "io": {"extend": "CYL_01_EXT_DO", "extended": "CYL_01_EXT_DI", "retracted": "CYL_01_RET_DI"},
}
CONV = {
    "id": "CONV_01",
    "type": "belt_conveyor",
    "description": "Belt",
    "position": [0, 0, 0],
    "length": 3.0,
    "width": 0.5,
    "speed_mps": 0.5,
    "io": {"run": "CONV_01_RUN_DO", "running": "CONV_01_RUN_DI"},
}
DS = {
    "id": "DS_01",
    "type": "distance_sensor",
    "description": "Distance Sensor",
    "position": [0, 0.6, 0],
    "axis": "-y",
    "beam_length": 0.8,
    "io": {"distance": "DS_01_DIST_AI"},
}


def test_every_type_supports_only_known_kinds_and_rejects_the_rest(rig):
    for type_name in REGISTRY.types():
        assert set(REGISTRY.resolve(type_name).FAULTS) <= set(FAULT_KINDS), type_name
    r = rig(PE, CYL)
    with pytest.raises(ValueError, match="no 'jammed' fault .supports: stuck_on, stuck_off, "):
        r["PE_01"].fault_inject("jammed")
    with pytest.raises(ValueError, match="takes factor, not speed"):
        r["CYL_01"].fault_inject("slow", speed=0.5)
    with pytest.raises(ValueError, match="between 0 and 1"):
        r["CYL_01"].fault_inject("slow", factor=1.5)
    with pytest.raises(ValueError, match="needs a DI port .extended, retracted., not 'extend'"):
        r["CYL_01"].fault_inject("stuck_on", port="extend")
    remover = rig({"id": "REM_01", "type": "part_remover", "description": "Remover",
                   "position": [0, 0, 0], "size": [0.3, 1.0, 1.0]})  # fmt: skip
    with pytest.raises(ValueError, match="supports: none"):
        remover["REM_01"].fault_inject("stuck_off")


def test_one_fault_at_a_time_injecting_replaces_and_clear_removes(rig):
    r = rig(CYL)
    assert r["CYL_01"].fault_inject("jammed") is None
    replaced = r["CYL_01"].fault_inject("slow", factor=0.25)
    assert replaced.kind == "jammed" and r["CYL_01"].fault.params == {"factor": 0.25}
    assert r["CYL_01"].clear_fault().kind == "slow" and r["CYL_01"].fault is None


def test_stuck_on_and_off_override_the_device_output(rig):
    r = rig(PE)
    r["PE_01"].fault_inject("stuck_on")
    r.step(2)
    assert r.io.get("PE_01_DI") is True and r["PE_01"].blocked is False  # nothing there
    r.world.spawn("box", (0.3, 0.2, 0.3), (1.0, 0.1, 0.0))
    r["PE_01"].fault_inject("stuck_off")
    r.step(2)
    assert r.io.get("PE_01_DI") is False and r["PE_01"].blocked is True  # box in the beam


def test_stuck_on_one_port_and_an_nc_eye_reads_the_opposite_at_the_plc(rig):
    r = rig(CYL)
    r["CYL_01"].fault_inject("stuck_on", port="extended")
    r.step()
    assert (r.io.get("CYL_01_EXT_DI"), r.io.get("CYL_01_RET_DI")) == (True, True)  # both made
    data = yaml.safe_load(SORTER.read_text(encoding="utf-8"))
    next(c for c in data["components"] if c["id"] == "PE_01")["io"]["detect"] = {
        "tag": "PE_01_DI",
        "invert": True,
    }
    loaded = build(Scene.model_validate(data))  # LoadedScene.step runs the 2.17 transform
    loaded.component("PE_01").fault_inject("stuck_on")
    loaded.step(DT)
    assert loaded.io.get("PE_01_DI") is False  # the device's output is stuck on; NC inverts it


def test_jammed_belt_stops_at_once_with_honest_feedback(rig):
    r = rig(CONV)
    r.plc("CONV_01_RUN_DO", True)
    r.step(20)
    assert r["CONV_01"].surface_speed == pytest.approx(0.5)
    r["CONV_01"].fault_inject("jammed")
    r.step()
    assert r["CONV_01"].surface_speed == 0.0 and r.io.get("CONV_01_RUN_DI") is True
    r["CONV_01"].clear_fault()
    r.step(20)
    assert r["CONV_01"].surface_speed == pytest.approx(0.5)


def test_jammed_pusher_freezes_mid_stroke_and_its_limits_stay_honest(rig):
    r = rig(CYL)
    r.plc("CYL_01_EXT_DO", True)
    r.step(10)
    r["CYL_01"].fault_inject("jammed")
    r.step(30)
    assert r["CYL_01"].position == pytest.approx(0.25)
    assert (r.io.get("CYL_01_EXT_DI"), r.io.get("CYL_01_RET_DI")) == (False, False)


def test_slow_halves_the_speed_of_an_actuator_a_belt_and_an_emitter(rig):
    r = rig(CYL, CONV)
    r["CYL_01"].fault_inject("slow")  # factor 0.5: 40 ticks instead of 20
    r["CONV_01"].fault_inject("slow")
    r.plc("CYL_01_EXT_DO", True)
    r.plc("CONV_01_RUN_DO", True)
    r.step(39)
    assert r.io.get("CYL_01_EXT_DI") is False
    r.step()
    assert r.io.get("CYL_01_EXT_DI") is True
    assert r["CONV_01"].surface_speed == pytest.approx(0.25)
    emitter = {"id": "EMT_01", "type": "box_emitter", "description": "Source",
               "position": [0, 5, 0], "interval_s": 1.0,
               "parts": [{"name": "box", "size": [0.3, 0.2, 0.3], "weight": 1}]}  # fmt: skip
    e = rig(emitter)
    e["EMT_01"].fault_inject("slow")
    for _ in range(150):  # 3 s; parts moved out of the way each tick
        e.step()
        for p in e.world.parts.values():
            p.position = (p.position[0] + 1.0, *p.position[1:])
    assert e["EMT_01"].spawned == 2  # at 0 s and 2 s (interval stretched to 2 s)
    j = rig(emitter)
    j["EMT_01"].fault_inject("jammed")
    j.step(150)
    assert j["EMT_01"].spawned == 0


def test_misaligned_eye_sees_late_by_the_offset_and_distance_reads_more(rig):
    def first_detect(offset: float | None) -> int:
        loaded = load(SORTER)
        loaded.io.set("CONV_01_RUN_DO", True, Writer.PLC)
        loaded.io.set("EMT_01_EN_DO", True, Writer.PLC)
        if offset is not None:
            loaded.component("PE_01").fault_inject("misaligned", offset_m=offset)
        for k in range(1, 1000):
            loaded.step(DT)
            if loaded.io.get("PE_01_DI"):
                return k
        raise AssertionError("never detected")

    base, late = first_detect(None), first_detect(0.05)
    print(f"first tall box at PE_01: tick {base}; misaligned +0.05 m: tick {late}")
    assert late - base == 5  # 0.05 m at 0.5 m/s is 0.1 s
    r = rig(DS)
    r["DS_01"].fault_inject("misaligned", offset_m=-0.1)
    r.step()
    assert r.io.get("DS_01_DIST_AI") == pytest.approx(0.7)


def test_drift_grows_with_simulated_time(rig):
    r = rig(DS)
    r["DS_01"].fault_inject("drift", rate=0.02)
    r.step(50)  # 1 s
    assert r.io.get("DS_01_DIST_AI") == pytest.approx(0.8 + 0.02)
    r.step(100)
    assert r.io.get("DS_01_DIST_AI") == pytest.approx(0.8 + 0.06)


def test_noisy_dropouts_are_deterministic_long_enough_to_poll_and_leave_other_draws_alone():
    def run(noisy: bool) -> tuple[list[int], list[int], list[str]]:
        loaded = load(SORTER)
        loaded.io.set("CONV_01_RUN_DO", True, Writer.PLC)
        loaded.io.set("EMT_01_EN_DO", True, Writer.PLC)
        eye = loaded.component("PE_01")
        if noisy:
            eye.fault_inject("noisy", rate=0.5, duration_s=0.2)
        starts, lengths, run_length = [], [], 0
        for k in range(1, 3001):  # 60 s
            loaded.step(DT)
            dropping = eye.fault is not None and eye.fault.dropout_s > 0
            if dropping:
                if run_length == 0:
                    starts.append(k)
                run_length += 1
            elif run_length:
                lengths.append(run_length)
                run_length = 0
        names = [p.name for p in sorted(loaded.world.parts.values(), key=lambda p: p.id)]
        return starts, lengths, [*names, f"spawned {loaded.world.spawned}"]

    starts, lengths, parts = run(True)
    again, _, _ = run(True)
    _, _, clean = run(False)
    print(f"dropouts over 60 s (rate 0.5/s): {len(starts)}, starting at ticks {starts[:6]} ...; "
          f"lengths {sorted(set(lengths))} ticks")  # fmt: skip
    assert starts == again  # same seed, same dropouts
    assert 15 <= len(starts) <= 45  # about 0.5 per second
    assert set(lengths) == {10}  # 0.2 s each: long enough for 100 ms PLC polling
    assert parts == clean  # the emitter's part sequence is unchanged by the fault's draws


def test_a_dropout_reads_false_on_a_di_and_the_top_of_the_range_on_an_ai(rig):
    r = rig(PE, DS)
    r.world.spawn("box", (0.3, 0.2, 0.3), (1.0, 0.1, 0.0))
    r.world.spawn("box", (0.3, 0.2, 0.3), (0.0, 0.1, 0.0))  # 0.4 m below the distance sensor
    for comp in ("PE_01", "DS_01"):
        r[comp].fault_inject("noisy")
        r[comp].fault.dropout_s = 0.2  # in a dropout now
    r.step(2)
    assert r.io.get("PE_01_DI") is False and r["PE_01"].blocked is True
    assert r.io.get("DS_01_DIST_AI") == pytest.approx(0.8)  # no echo: reads beam_length
