"""PLAN 5.7a, ADR-0006: the bulk component types (`bin`, `bulk_supply`, `bulk_discharge`,
`rotary_airlock`, `screw_conveyor`, `slide_gate`), the `materials:` block, their faults, their
lint (E105 to E109) and their checks (`never_empty`, `never_full`, `batch_within`), each on a
small scene."""

from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml

from openfactory.checks import evaluate
from openfactory.core.iotable import Writer
from openfactory.core.loader import build
from openfactory.core.schema import parse_scene
from openfactory.lint import lint_scene

DT = 0.02
HEAD = """openfactory: "0.1"
scene: {name: bulk, description: Bulk test scene.}
materials:
  - {name: flour, bulk_density: 500}
  - {name: sugar, bulk_density: 800}
components:
"""


def scene(body: str, checks: str = "", head: str = HEAD) -> str:
    return head + body + (f"checks:\n{checks}" if checks else "")


def loaded(text: str):  # noqa: ANN201
    return build(parse_scene(text, Path(".")))


def run(sc, seconds: float, set_: dict | None = None) -> None:  # noqa: ANN001
    for _ in range(round(seconds / DT)):
        for tag, value in (set_ or {}).items():
            sc.io.set(tag, value, Writer.PLC)
        sc.step(DT)


SILO = """  - id: SILO
    type: bin
    description: Test Silo
    position: [0, 3, 0]
    diameter: 2.0
    height: 2.0
    cone_height: 1.0
    outlet_diameter: 0.4
    material: flour
    initial_mass_kg: {mass}
    tare_kg: 100
    hi_level: 2.5
    lo_level: 0.5
    io: {{level: SILO_LT_AI, weight: SILO_WT_AI, hi_sw: SILO_LSH_DI, lo_sw: SILO_LSL_DI}}
"""
HOP = """  - id: HOP
    type: bin
    description: Test Hopper
    position: [3, 1, 0]
    shape: box
    length: 1.0
    width: 1.0
    height: 1.0
    material: flour
    io: {weight: HOP_WT_AI}
"""


def feeder(kind: str, src: str = "SILO", dst: str = "HOP", extra: str = "") -> str:
    io = {"rotary_airlock": "{run: RV_RUN_DO, speed: RV_SPD_AO, running: RV_RUN_DI, "
                            "plugged: RV_PLUG_DI}",
          "slide_gate": "{open: SG_OPEN_DO, opened: SG_ZSO_DI, closed: SG_ZSC_DI, "
                        "plugged: SG_PLUG_DI}",
          "screw_conveyor": "{run: SC_RUN_DO, running: SC_RUN_DI, motion: SC_ZSS_DI, "
                            "plugged: SC_PLUG_DI}"}[kind]  # fmt: skip
    cid = {"rotary_airlock": "RV", "slide_gate": "SG", "screw_conveyor": "SC"}[kind]
    return (f"  - id: {cid}\n    type: {kind}\n    description: Test {kind}\n"
            f"    position: [0, 2, 0]\n    from: {src}\n    to: {dst}\n"
            f"    capacity_kg_s: 5\n{extra}    io: {io}\n")  # fmt: skip


# -- bin ----------------------------------------------------------------------------------------


def test_a_bin_reads_its_level_weight_and_switches_from_its_mass_and_shape():
    sc = loaded(scene(SILO.format(mass=1000)))
    silo = sc.component("SILO")
    cone = math.pi * 1.0 / 3 * (1.0**2 + 1.0 * 0.2 + 0.2**2)  # frustum r 1.0 to 0.2, 1 m tall
    wall = math.pi * 1.0**2 * 2.0
    assert silo.node.capacity == pytest.approx((cone + wall) * 500)
    sc.step(DT)
    volume = 1000 / 500  # 2 m³: past the cone's 1.299 m³, into the wall
    level = 1.0 + (volume - cone) / (math.pi * 1.0**2)
    assert sc.io.get("SILO_LT_AI") == pytest.approx(level)
    assert sc.io.get("SILO_WT_AI") == pytest.approx(1100.0)  # contents and tare
    assert not sc.io.get("SILO_LSH_DI") and not sc.io.get("SILO_LSL_DI")
    # a level inside the cone, by bisection: 0.3 m³ is below the cone's top
    assert silo.shape.volume(silo.shape.level(0.3)) == pytest.approx(0.3, rel=1e-12)


def test_the_weight_and_level_ranges_follow_the_bin():
    sc = loaded(scene(SILO.format(mass=0)))
    tags = {t.name: t for t in sc.io.tags()}
    assert tags["SILO_LT_AI"].range == (0.0, 3.0)
    full = sc.component("SILO").node.capacity
    assert tags["SILO_WT_AI"].range == pytest.approx((0.0, full + 100))


def test_a_bridge_gives_nothing_until_cleared():
    text = scene(SILO.format(mass=1000) + HOP + feeder("slide_gate"))
    sc = loaded(text)
    sc.component("SILO").fault_inject("bridge")
    run(sc, 2.0, {"SG_OPEN_DO": True})
    assert sc.component("HOP").node.mass == 0.0
    sc.component("SILO").clear_fault()
    run(sc, 1.0, {"SG_OPEN_DO": True})
    assert sc.component("HOP").node.mass > 0


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (SILO.format(mass=99999), "is more than it holds full of flour"),
        (SILO.format(mass=0).replace("diameter: 2.0", "shape: box"), "needs length and width"),
        (
            SILO.format(mass=0).replace("outlet_diameter: 0.4", "outlet_diameter: 3"),
            "outlet_diameter is wider",
        ),  # fmt: skip
    ],
)
def test_a_bin_that_cannot_be_is_refused_at_load(body, message):
    with pytest.raises(ValueError, match=message):
        parse_scene(scene(body), Path("."))


def test_a_material_declared_twice_is_refused():
    head = HEAD.replace(
        "  - {name: sugar, bulk_density: 800}", "  - {name: flour, bulk_density: 9}"
    )
    with pytest.raises(ValueError, match="declared twice"):
        parse_scene(scene(SILO.format(mass=0), head=head), Path("."))


# -- supply and discharge -----------------------------------------------------------------------

SUPPLY = """  - id: LINE
    type: bulk_supply
    description: Test Line
    position: [0, 6, 0]
    material: flour
    capacity_kg_s: 2
    io: {enable: LINE_EN_DO}
"""
DISCHARGE = """  - id: OUT
    type: bulk_discharge
    description: Test Outlet
    position: [3, 0, 0]
    demand_kg_s: 1
"""


def test_a_supply_gives_its_capacity_while_enabled():
    sc = loaded(scene(SUPPLY + HOP + feeder("slide_gate", "LINE", "HOP")))
    run(sc, 2.0, {"SG_OPEN_DO": True, "LINE_EN_DO": False})
    assert sc.component("HOP").node.mass == 0.0
    run(sc, 3.0, {"SG_OPEN_DO": True, "LINE_EN_DO": True})  # open already: 2 kg/s, a tick late
    assert sc.component("HOP").node.mass == pytest.approx(2.0 * (3.0 - DT))
    assert sc.component("LINE").state()["total_kg"] == sc.component("HOP").node.mass


def test_a_discharge_takes_its_demand_and_blocks_the_rest():
    sc = loaded(scene(SILO.format(mass=1000) + DISCHARGE + feeder("slide_gate", "SILO", "OUT")))
    run(sc, 3.0, {"SG_OPEN_DO": True})  # the gate passes up to 5 kg/s, the outlet takes 1
    assert 2.8 < sc.component("OUT").state()["total_kg"] < 3.0  # 1 kg/s once 20 % open
    assert sc.io.get("SG_PLUG_DI")  # the rest is refused: blocked


# -- rotary airlock -----------------------------------------------------------------------------


def test_an_airlock_feeds_at_its_speed_and_reports_running():
    sc = loaded(scene(SILO.format(mass=1000) + HOP + feeder("rotary_airlock")))
    run(sc, 2.0, {"RV_RUN_DO": True, "RV_SPD_AO": 50.0})
    assert sc.io.get("RV_RUN_DI")
    # 2.5 kg/s from the second tick (the speed reaches the network a tick late)
    assert sc.component("HOP").node.mass == pytest.approx(2.5 * (2.0 - DT))


def test_an_airlock_trip_stops_it_and_drops_running():
    sc = loaded(scene(SILO.format(mass=1000) + HOP + feeder("rotary_airlock")))
    run(sc, 1.0, {"RV_RUN_DO": True, "RV_SPD_AO": 100.0})
    sc.component("RV").fault_inject("trip")
    run(sc, 0.04, {"RV_RUN_DO": True})
    held = sc.component("HOP").node.mass
    run(sc, 1.0, {"RV_RUN_DO": True})
    assert sc.component("HOP").node.mass == held
    assert not sc.io.get("RV_RUN_DI")


def test_an_airlock_into_a_full_hopper_is_blocked_and_says_so():
    small = HOP.replace("height: 1.0", "height: 0.01")  # 5 kg
    sc = loaded(scene(SILO.format(mass=1000) + small + feeder("rotary_airlock")))
    run(sc, 3.0, {"RV_RUN_DO": True, "RV_SPD_AO": 100.0})
    hop = sc.component("HOP").node
    assert hop.mass == pytest.approx(hop.capacity)
    assert sc.io.get("RV_PLUG_DI")


def test_a_plugged_chute_blocks_the_airlock():
    sc = loaded(scene(SILO.format(mass=1000) + HOP + feeder("rotary_airlock")))
    sc.component("RV").fault_inject("plugged")
    run(sc, 1.0, {"RV_RUN_DO": True, "RV_SPD_AO": 100.0})
    assert sc.component("HOP").node.mass == 0.0 and sc.io.get("RV_PLUG_DI")


# -- screw conveyor -----------------------------------------------------------------------------


def test_a_screw_delivers_after_its_travel_time_and_reports_motion():
    sc = loaded(scene(SILO.format(mass=1000) + HOP
                      + feeder("screw_conveyor", extra="    travel_s: 2\n")))  # fmt: skip
    first = None
    for n in range(300):
        sc.io.set("SC_RUN_DO", True, Writer.PLC)
        sc.step(DT)
        if first is None and sc.component("HOP").node.mass > 0:
            first = n
    assert sc.io.get("SC_RUN_DI") and sc.io.get("SC_ZSS_DI")
    # the run command reaches the network a tick late, then 2 s of travel
    assert first == 1 + round(2.0 / DT)


def test_a_jammed_screw_runs_but_does_not_turn():
    sc = loaded(scene(SILO.format(mass=1000) + HOP + feeder("screw_conveyor")))
    sc.component("SC").fault_inject("jammed")
    run(sc, 1.0, {"SC_RUN_DO": True})
    assert sc.io.get("SC_RUN_DI") and not sc.io.get("SC_ZSS_DI")
    assert sc.component("SC").link.load == 0.0


def test_a_tripped_screw_drops_running_and_motion():
    sc = loaded(scene(SILO.format(mass=1000) + HOP + feeder("screw_conveyor")))
    sc.component("SC").fault_inject("trip")
    run(sc, 0.5, {"SC_RUN_DO": True})
    assert not sc.io.get("SC_RUN_DI") and not sc.io.get("SC_ZSS_DI")


def test_a_screw_into_a_full_bin_holds_its_load_and_says_plugged():
    small = HOP.replace("height: 1.0", "height: 0.01")  # 5 kg
    sc = loaded(scene(SILO.format(mass=1000) + small + feeder("screw_conveyor")))
    run(sc, 10.0, {"SC_RUN_DO": True})
    screw = sc.component("SC").link
    assert sc.io.get("SC_PLUG_DI") and screw.blocked
    assert 0 < screw.load <= 5 * 4.0 + 1e-9  # capacity x the default 4 s travel


# -- slide gate ---------------------------------------------------------------------------------


def test_a_gate_opens_over_its_travel_time_with_honest_limits():
    sc = loaded(
        scene(SILO.format(mass=1000) + HOP + feeder("slide_gate", extra="    travel_s: 2\n"))
    )
    sc.step(DT)
    assert sc.io.get("SG_ZSC_DI") and not sc.io.get("SG_ZSO_DI")
    run(sc, 1.0, {"SG_OPEN_DO": True})
    assert not sc.io.get("SG_ZSC_DI") and not sc.io.get("SG_ZSO_DI")  # mid-travel
    assert sc.component("SG").opening == pytest.approx(0.5)
    run(sc, 1.0, {"SG_OPEN_DO": True})
    assert sc.io.get("SG_ZSO_DI")
    sc.component("SG").fault_inject("jammed")
    run(sc, 1.0, {"SG_OPEN_DO": False})
    assert sc.component("SG").opening == 1.0  # it stays where it jammed


# -- the network and the scene ------------------------------------------------------------------


def test_a_scene_with_no_bulk_type_has_no_solids_network():
    text = (Path(__file__).resolve().parents[2] / "scenes" / "tank-level-onoff.yaml").read_text()
    assert loaded(text).solidsnet is None


def test_a_liquid_link_cannot_name_a_bin():
    body = (
        SILO.format(mass=0)
        + """  - id: P_1
    type: pump
    description: Test Pump
    position: [1, 0, 0]
    from: SILO
    to: SILO
    io: {run: P_1_RUN_DO}
"""
    )
    with pytest.raises(ValueError, match="in the solids network"):
        parse_scene(scene(body), Path("."))


# -- lint ---------------------------------------------------------------------------------------


def lint_codes(tmp_path: Path, text: str) -> list[str]:
    path = tmp_path / "bulk.yaml"
    path.write_text(text, encoding="utf-8")
    return [f.code for f in lint_scene(path) if f.level == "error"]


TANK = """  - id: TK_1
    type: tank
    description: Test Tank
    position: [5, 0, 0]
"""


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (SILO.format(mass=0) + feeder("slide_gate", "SILO", "NOPE"), "E105"),
        (SILO.format(mass=0) + feeder("slide_gate", "SILO", "SILO.top"), "E105"),
        (
            SILO.format(mass=0).replace("    material: flour\n", "") + HOP + feeder("slide_gate"),
            "E106",
        ),  # fmt: skip
        (
            SILO.format(mass=0).replace("material: flour", "material: salt")
            + HOP
            + feeder("slide_gate"),
            "E107",
        ),  # fmt: skip
        (SILO.format(mass=0) + TANK + feeder("slide_gate", "SILO", "TK_1.top"), "E108"),
        (
            SILO.format(mass=0)
            + HOP.replace("material: flour", "material: sugar")
            + feeder("slide_gate"),
            "E109",
        ),  # fmt: skip
    ],
    ids=[
        "names-nothing",
        "names-a-port",
        "no-material",
        "undeclared",
        "liquid-end",
        "two-materials",
    ],
)
def test_lint_finds_each_bulk_error(tmp_path, body, code):
    assert lint_codes(tmp_path, scene(body)) == [code]


def test_a_good_bulk_scene_lints_clean_of_errors(tmp_path):
    assert lint_codes(tmp_path, scene(SILO.format(mass=100) + HOP + feeder("slide_gate"))) == []


# -- checks -------------------------------------------------------------------------------------


def grade(text: str, seconds: float, set_: dict) -> dict[str, bool]:
    sc = loaded(text)
    run(sc, seconds, set_)
    checks = parse_scene(text, Path(".")).checks
    return {r.name.split(":")[0]: r.passed for r in evaluate(checks, sc)}


CHECKS = """  never_empty: [SILO]
  never_full: [HOP]
  batch_within: {SILO: {target_kg: 20, tolerance_kg: 1}}
"""


def test_the_bulk_checks_fail_an_emptied_silo_a_filled_hopper_and_a_wrong_batch():
    text = scene(SILO.format(mass=20) + HOP.replace("height: 1.0", "height: 0.02")
                 + feeder("slide_gate"), CHECKS)  # fmt: skip
    got = grade(text, 6.0, {"SG_OPEN_DO": True})  # 20 kg through a 5 kg/s gate into 10 kg
    assert got == {"never_empty SILO": True, "never_full HOP": False,
                   "batch_within SILO": True}  # fmt: skip
    text = scene(SILO.format(mass=5) + HOP + feeder("slide_gate"), CHECKS)
    got = grade(text, 4.0, {"SG_OPEN_DO": True})
    assert got == {"never_empty SILO": False, "never_full HOP": True,
                   "batch_within SILO": False}  # fmt: skip


def test_no_batch_fails_batch_within():
    text = scene(SILO.format(mass=20) + HOP + feeder("slide_gate"), CHECKS)
    assert grade(text, 1.0, {"SG_OPEN_DO": False})["batch_within SILO"] is False


def test_the_shipped_scene_balances_its_mass_over_300_s():
    import runpy

    repo = Path(__file__).resolve().parents[2]
    step = runpy.run_path(str(repo / "examples" / "pyplc" / "silo-batch-weigh.py"))["step"]
    sc = build(
        parse_scene((repo / "scenes" / "silo-batch-weigh.yaml").read_text(), repo / "scenes")
    )
    net = sc.solidsnet
    start, worst = net.held(), 0.0

    class Io:
        def __getitem__(self, k: str) -> object:
            return sc.io.get(k)

        def __setitem__(self, k: str, v: object) -> None:
            sc.io.set(k, v, Writer.PLC)

    io = Io()
    for n in range(round(300 / DT)):
        step(io, n * DT)
        sc.step(DT)
        worst = max(worst, abs(net.held() - (start + net.supplied() - net.discharged())))
    through = start + net.supplied() + net.discharged()
    print(f"silo-batch-weigh, 300 s: worst balance {worst:.3e} kg of {through:.0f} kg")
    assert worst / through < 1e-12
    assert yaml.safe_load("[]") == []
