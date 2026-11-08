"""PLAN 3.1: the flow network solver (``core/flownet.py``, ADR-0005), held to the tests of the
proposal's section 8: closed forms for single links, hand solutions for small networks, the
volume balance every tick, status logic, determinism, and convergence within the cap."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from openfactory.core.flownet import (
    BAR_HEAD,
    DENSITY,
    MAX_ITERATIONS,
    SPECIFIC_HEAT,
    FixedHead,
    FlowNetError,
    Junction,
    Network,
    Pump,
    Resistance,
    Tank,
    kv_from_cv,
    pipe_resistance,
    pump_curve,
    valve_resistance,
)
from openfactory.core.loader import load

REPO = Path(__file__).resolve().parents[2]
DT = 0.02


def tank(
    tid: str,
    *,
    elevation: float = 0.0,
    area: float = 1.0,
    height: float = 3.0,
    level: float = 1.0,
    **nozzles: float,
) -> Tank:
    return Tank(
        tid, elevation, area, height, area * level, {"bottom": 0.0, "top": height, **nozzles}
    )


def balance(net: Network) -> float:
    """Tank volume + overflow - (delivered - received - demand drawn): constant if mass balances."""
    stored = sum(t.volume + t.overflow for t in net.tanks)
    through = sum(n.delivered - n.received for n in net.fixed)
    drawn = sum(j.received for j in net.junctions)
    return stored - through + drawn


# --- coefficients ------------------------------------------------------------------------------


def test_coefficients_from_data_sheet_values():
    assert pytest.approx(10.21, abs=0.01) == BAR_HEAD
    assert kv_from_cv(10) == pytest.approx(8.65)
    # a Kv 10 valve passes 10 m³/h at a 1 bar drop
    r = valve_resistance(10.0, 1.0)
    assert math.sqrt(BAR_HEAD / r) * 3600 == pytest.approx(10.0)
    assert valve_resistance(10.0, 0.0) == math.inf
    # Darcy-Weisbach: 10 m of 50 mm pipe, f 0.02, at 2 m/s loses f·L/D·v²/2g
    area = math.pi * 0.05**2 / 4
    q = 2.0 * area
    assert pipe_resistance(10, 0.05, 0.02, 0) * q * q == pytest.approx(
        0.02 * 10 / 0.05 * 4 / (2 * 9.81)
    )


def test_the_single_point_pump_curve_is_epanets():
    q, h = 6 / 3600, 8.0
    shutoff, b, n = pump_curve([(q, h)])
    assert shutoff == pytest.approx(1.33 * h)
    assert shutoff - b * q**n == pytest.approx(h)
    assert shutoff - b * (2 * q) ** n == pytest.approx(0.0, abs=1e-9)
    with pytest.raises(FlowNetError, match="three points"):
        pump_curve([(0, 5), (1, 6), (2, 0)])


# --- single links against closed forms ----------------------------------------------------------


def test_a_tank_draining_through_a_valve_follows_torricellis_law():
    """Free drain through a fixed restriction: q = sqrt(h / r), so sqrt(h) falls linearly,
    sqrt(h(t)) = sqrt(h0) - t / (2·A·sqrt(r)) (Torricelli with the valve as the orifice)."""
    tk = tank("TK_01", area=0.5, level=2.0)
    r = valve_resistance(4.0, 1.0)
    net = Network.build(
        [tk, FixedHead("DR_01", 0.0)], [Resistance("HV_01", "TK_01.bottom", "DR_01", r=r)]
    )
    empty_at = 2 * 0.5 * math.sqrt(r) * math.sqrt(2.0)
    t = 0.0
    while t < 0.95 * empty_at:
        net.step(DT)
        t += DT
        expected = (math.sqrt(2.0) - t / (2 * 0.5 * math.sqrt(r))) ** 2
        assert tk.level == pytest.approx(expected, rel=0.005, abs=1e-4)
    for _ in range(int(0.2 * empty_at / DT)):
        net.step(DT)
    # empties, never below: the last millimetres drain on the linear low-flow law, so the level
    # decays toward zero and the nozzle counts as dry within LEVEL_EPS
    assert 0.0 <= tk.level <= 1e-9
    assert net.links[0].flow == 0.0 and net.stats.cap_hits == 0


def test_a_pump_against_rising_head_follows_its_curve_and_stops_at_shutoff():
    """A pump lifts from a wide sump into the bottom of a tank: each tick its gain equals the
    static lift plus the pipe loss; the flow falls as the tank fills and stops at shutoff head."""
    sump = tank("TK_01", area=50.0, level=1.0)
    upper = tank("TK_02", elevation=2.0, area=0.02, height=12.0, level=0.5)
    shutoff, b, n = pump_curve([(6 / 3600, 8.0)])
    pump = Pump(
        "P_01", "TK_01.bottom", "PIPE_01", shutoff=shutoff, coefficient=b, exponent=n, running=True
    )
    r = pipe_resistance(8, 0.04, 0.02, 2.0)
    pipe = Resistance("PIPE_01", "P_01", "TK_02.bottom", r=r)
    net = Network.build([sump, upper], [pump, pipe])
    flows = []
    for _ in range(int(600 / DT)):
        lift = upper.head - sump.head  # the solve sees the levels at the start of the tick
        net.step(DT)
        flows.append(pump.flow)
        if pump.flow > 0:
            assert pump.gain(pump.flow) == pytest.approx(lift + pipe.loss(pump.flow), abs=2e-3)
    assert flows[0] > 5 / 3600 and all(
        a >= b - 1e-12 for a, b in zip(flows, flows[1:], strict=False)
    )
    assert pump.flow == 0.0  # at shutoff: the pump cannot push and does not run backwards
    assert upper.head - sump.head == pytest.approx(shutoff, abs=0.01)


# --- networks against hand solutions -------------------------------------------------------------


def test_two_valves_in_series_add_their_resistances():
    """Supply at 2 bar, two valves joined end to end (an implicit junction between them), an
    open drain: q = sqrt(Δh / (r1 + r2)), and the junction sits at the first valve's drop."""
    r1, r2 = valve_resistance(10, 1.0), valve_resistance(5, 0.6)
    sup, dr = FixedHead("SUP_01", 0.0, pressure_bar=2.0), FixedHead("DR_01", 0.0)
    v1 = Resistance("FV_01", "SUP_01", "FV_02", position=(1, 0.5, 0), r=r1)
    v2 = Resistance("FV_02", "FV_01", "DR_01", position=(2, 0.5, 0), r=r2)
    net = Network.build([sup, dr], [v1, v2])
    net.step(DT)
    q = math.sqrt(sup.head / (r1 + r2))
    assert v1.flow == pytest.approx(q, rel=1e-3) and v2.flow == pytest.approx(v1.flow, rel=1e-9)
    joint = net.junctions[0]
    assert joint.id == "FV_01>FV_02" and joint.elevation == 0.5
    assert joint.head == pytest.approx(sup.head - r1 * q * q, rel=1e-3)
    # one valve alone passes more than the pair: the pressure-free model would get this wrong
    assert q < math.sqrt(sup.head / r1) and q < math.sqrt(sup.head / r2)


def test_two_pipes_in_parallel_split_inversely_to_the_root_of_their_resistances():
    r1, r2 = 4e5, 1e5
    sup, dr = FixedHead("SUP_01", 0.0, pressure_bar=1.0), FixedHead("DR_01", 0.0)
    j1, j2 = Junction("J_01", 0.0), Junction("J_02", 0.0)
    links = [
        Resistance("PIPE_01", "SUP_01", "J_01", r=1e3),
        Resistance("PIPE_02", "J_01", "J_02", r=r1),
        Resistance("PIPE_03", "J_01", "J_02", r=r2),
        Resistance("PIPE_04", "J_02", "DR_01", r=1e3),
    ]
    net = Network.build([sup, dr, j1, j2], links)
    net.step(DT)
    assert links[1].flow / links[2].flow == pytest.approx(math.sqrt(r2 / r1), rel=1e-3)
    assert links[0].flow == pytest.approx(links[1].flow + links[2].flow, rel=1e-9)


# --- status logic ---------------------------------------------------------------------------------


def test_a_pump_that_runs_its_tank_dry_stops_delivering_until_it_refills():
    sump = tank("TK_01", area=0.1, level=0.3)
    upper = tank("TK_02", elevation=1.0, area=1.0, height=5.0, level=0.1)
    shutoff, b, n = pump_curve([(6 / 3600, 8.0)])
    pump = Pump(
        "P_01",
        "TK_01.bottom",
        "TK_02.top",
        shutoff=shutoff,
        coefficient=b,
        exponent=n,
        running=True,
    )
    net = Network.build([sump, upper], [pump])
    start = balance(net)
    for _ in range(int(60 / DT)):
        net.step(DT)
        assert sump.volume >= 0.0
        assert balance(net) == pytest.approx(start, abs=1e-9)
    assert sump.level == 0.0 and pump.flow == 0.0 and pump.held_closed
    assert net.stats.splits >= 1  # the tick was split where the sump ran dry
    sump.volume = 0.05  # refilled by hand: the pump picks up again
    net.step(DT)
    assert pump.flow > 0 and not pump.held_closed


def test_a_check_valve_stops_reverse_flow_and_a_plain_pipe_does_not():
    for check, expect_flow in ((True, False), (False, True)):
        low, high = tank("TK_01", level=0.5), tank("TK_02", level=2.0)
        pipe = Resistance("PIPE_01", "TK_01.bottom", "TK_02.bottom", r=1e5, check_valve=check)
        net = Network.build([low, high], [pipe])
        net.step(DT)
        assert (pipe.flow < 0) is expect_flow  # TK_02 is higher: flow would run backwards


def test_an_outlet_nozzle_above_the_surface_passes_nothing_and_an_inlet_above_it_falls_freely():
    tk = tank("TK_01", level=0.5, side=1.0)  # side nozzle 1 m up, level 0.5 m
    out = Resistance("PIPE_01", "TK_01.side", "DR_01", r=1e4)
    net = Network.build([tk, FixedHead("DR_01", -2.0)], [out])
    net.step(DT)
    assert out.flow == 0.0
    feed = tank("TK_02", level=0.5)
    inlet = Resistance("PIPE_02", "SUP_01", "TK_02.top", r=1e5)
    net = Network.build([feed, FixedHead("SUP_01", 3.5)], [inlet])
    net.step(DT)
    first = inlet.flow
    feed.volume = 2.0  # level 2 m, still below the 3 m top nozzle: same head, same flow
    net.step(DT)
    assert inlet.flow == pytest.approx(first, rel=1e-6) and first == pytest.approx(
        math.sqrt(0.5 / 1e5), rel=1e-2
    )


def test_a_full_tank_overflows_and_the_overflow_is_counted():
    tk = tank("TK_01", area=0.1, height=1.0, level=0.95)
    sup = FixedHead("SUP_01", 0.0, pressure_bar=1.0)
    net = Network.build(
        [tk, sup], [Resistance("FV_01", "SUP_01", "TK_01.top", r=valve_resistance(10, 1.0))]
    )
    start = balance(net)
    for _ in range(int(10 / DT)):
        net.step(DT)
        assert tk.level <= 1.0
        assert balance(net) == pytest.approx(start, abs=1e-9)
    assert tk.level == 1.0 and tk.overflow > 0.0


def test_a_demand_drain_draws_its_demand_and_slows_to_nothing_as_its_supply_runs_out():
    tk = tank("TK_01", elevation=5.0, area=0.2, level=1.0)
    j = Junction("DR_01", 0.0, demand=2 / 3600, required_head=2.0, minimum_head=0.0)
    net = Network.build([tk, j], [Resistance("PIPE_01", "TK_01.bottom", "DR_01", r=1e5)])
    net.step(DT)
    drawn = net._demand_links["DR_01"].flow
    assert drawn == pytest.approx(2 / 3600, rel=1e-3)  # 6 m of head, above the 2 m required
    start = balance(net)
    for _ in range(int(800 / DT)):
        net.step(DT)
        assert balance(net) == pytest.approx(start, abs=1e-9)
        assert j.head >= j.elevation - 1e-6  # never pulled below its outlet: no negative heads
    assert tk.level == 0.0 and net._demand_links["DR_01"].flow == pytest.approx(0.0, abs=1e-9)
    assert j.received == pytest.approx(0.2, rel=1e-6)  # all of the tank, no more


# --- the volume balance every tick, determinism, convergence ------------------------------------


def plant() -> Network:
    """Supply -> control valve -> TK_01 -> gravity pipe -> TK_02 -> pump -> two branches (a demand
    drain and back to TK_01 through a check valve), a hand valve to an open drain."""
    t1 = tank("TK_01", elevation=2.0, area=0.5, height=2.0, level=0.6)
    t2 = tank("TK_02", elevation=0.0, area=0.8, height=2.0, level=1.2)
    sup = FixedHead("SUP_01", 0.0, pressure_bar=2.0, temperature=15.0, fractions={"water": 1.0})
    drain = FixedHead("DR_01", -1.0)
    use = Junction("DR_02", 0.5, demand=1.5 / 3600)
    split = Junction("J_01", 0.5)
    shutoff, b, n = pump_curve([(4 / 3600, 6.0)])
    links = [
        Resistance("FV_01", "SUP_01", "TK_01.top", r=valve_resistance(4, 0.5)),
        Resistance("PIPE_01", "TK_01.bottom", "TK_02.top", r=pipe_resistance(5, 0.03, 0.02, 2.0)),
        Pump(
            "P_01", "TK_02.bottom", "J_01", shutoff=shutoff, coefficient=b, exponent=n, running=True
        ),
        Resistance("PIPE_02", "J_01", "DR_02", r=pipe_resistance(3, 0.025, 0.02, 1.0)),
        Resistance(
            "PIPE_03", "J_01", "TK_01.top", r=pipe_resistance(6, 0.02, 0.02, 3.0), check_valve=True
        ),
        Resistance("HV_01", "TK_02.bottom", "DR_01", r=valve_resistance(1, 0.3)),
    ]
    return Network.build([t1, t2, sup, drain, use, split], links)


def run(net: Network, seconds: float, *, record: bool = False) -> list[tuple[float, ...]]:
    history = []
    valve, pump = net.links[0], net.links[2]
    for tick in range(int(seconds / DT)):
        if tick % 1500 == 750:  # operate it: shut and reopen the valve, stop and start the pump
            valve.r = math.inf if math.isfinite(valve.r) else valve_resistance(4, 0.5)
            pump.running = not pump.running
        net.step(DT)
        if record:
            history.append(tuple(lk.flow for lk in net.links) + tuple(t.volume for t in net.tanks))
    return history


def test_the_volume_balance_closes_every_tick_for_300_s():
    net = plant()
    start = balance(net)
    for tick in range(int(300 / DT)):
        if tick % 1500 == 750:
            net.links[0].r = math.inf if math.isfinite(net.links[0].r) else valve_resistance(4, 0.5)
            net.links[2].running = not net.links[2].running
        net.step(DT)
        assert balance(net) == pytest.approx(start, abs=1e-9), f"tick {tick}"
        for j in net.junctions:  # continuity at every junction
            if not math.isfinite(j.head):
                continue
            net_in = sum(lk.flow for lk in net.links if lk.end[0] is j) - sum(
                lk.flow for lk in net.links if lk.start[0] is j
            )
            drawn = net._demand_links[j.id].flow if j.id in net._demand_links else 0.0
            assert net_in - drawn == pytest.approx(0.0, abs=1e-12)
    assert net.stats.cap_hits == 0 and net.stats.max_iterations <= MAX_ITERATIONS
    assert net.stats.split_cap_hits == 0


def test_two_runs_are_identical_float_for_float():
    assert run(plant(), 60, record=True) == run(plant(), 60, record=True)


def test_every_converged_link_meets_its_head_equation_within_1_mm():
    net = plant()
    for _ in range(int(30 / DT)):
        net.step(DT)
    for lk in net.links:
        if lk.flow != 0.0:
            drop = net._end_head(lk.start) - net._end_head(lk.end)
            assert abs(lk.loss(lk.flow) - drop) < 1e-3


def test_mixing_and_heat_are_exact_balances():
    tk = tank("TK_01", area=1.0, height=4.0, level=1.0)
    tk.temperature, tk.fractions = 20.0, {"water": 1.0}
    hot = FixedHead("SUP_01", 0.0, pressure_bar=1.0, temperature=80.0, fractions={"syrup": 1.0})
    feed = Resistance("FV_01", "SUP_01", "TK_01.top", r=valve_resistance(3.6, 1.0))
    net = Network.build([tk, hot], [feed])
    net.step(DT)
    added = feed.flow * DT
    assert tk.temperature == pytest.approx((1.0 * 20 + added * 80) / (1.0 + added), rel=1e-12)
    assert tk.fractions["syrup"] == pytest.approx(added / (1.0 + added), rel=1e-12)
    assert sum(tk.fractions.values()) == pytest.approx(1.0)
    feed.r = math.inf
    before = tk.temperature
    tk.heat_w = 6000.0
    net.step(DT)
    assert tk.temperature - before == pytest.approx(
        6000 * DT / (DENSITY * SPECIFIC_HEAT * tk.volume)
    )


# --- building ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("links", "message"),
    [
        ([Resistance("V_01", "TK_09.bottom", "DR_01")], "'TK_09.bottom' names no tank"),
        ([Resistance("V_01", "TK_01", "DR_01")], "is a tank; name a nozzle"),
        ([Resistance("V_01", "TK_01.side", "DR_01")], "TK_01 has no nozzle 'side'"),
        ([Resistance("V_01", "DR_01.x", "TK_01.top")], "DR_01 is not a tank"),
        (
            [Resistance("V_01", "TK_01.bottom", "V_02"), Resistance("V_02", "TK_01.top", "DR_01")],
            "V_01.to names V_02, so V_02.from must name V_01",
        ),
        ([Resistance("V_01", "TK_01.bottom", "J_01")], "J_02: no link connects"),
    ],
)
def test_a_bad_network_fails_to_build_and_says_why(links, message):
    nodes = [tank("TK_01"), FixedHead("DR_01", 0.0)]
    if "J_02" in message:
        nodes.append(Junction("J_02", 0.0))
        links = [Resistance("V_01", "TK_01.bottom", "DR_01")]
    with pytest.raises(FlowNetError, match=message.replace("(", r"\(")):
        Network.build(nodes, links)


def test_scenes_without_process_components_have_no_network_and_are_unchanged():
    loaded = load(REPO / "scenes" / "sorting-by-height.yaml")
    assert loaded.flownet is None


def shipped_with_networks() -> list[Path]:
    paths = sorted(p for p in (REPO / "scenes").glob("*.yaml") if ".modbus." not in p.name)
    paths += sorted(
        p for p in (REPO / "tests" / "scenes").glob("*.yaml") if ".modbus." not in p.name
    )
    return [p for p in paths if load(p).flownet is not None]


def test_every_scene_with_a_network_converges_within_the_cap_for_60_s():
    """Convergence on every shipped and test scene that has a flow network (none before 3.2's
    components; the 3.3 scenes are added here as they land)."""
    for path in shipped_with_networks():
        loaded = load(path)
        for _ in range(int(60 / DT)):
            loaded.step(DT)
        stats = loaded.flownet.stats  # type: ignore[union-attr]
        assert stats.cap_hits == 0 and stats.split_cap_hits == 0, path.name


def test_a_pump_ramping_up_behind_a_pipe_into_a_top_inlet_starts_delivering():
    """Found building tank-level-onoff (PLAN 3.3): a pump too slow to lift on its first ticks
    closed, which left its pipe to a top nozzle a dead end that closed too; a closure carried
    over cut the junction between them off from every known head, and the pump never reopened.
    Every solve now starts from every available link open."""
    sump = tank("TK_01", area=0.8, level=0.8)
    head = tank("TK_02", elevation=1.6, area=0.07, height=1.0, level=0.5)
    shutoff, b, n = pump_curve([(12 / 3600, 4.0)])
    pump = Pump("P_01", "TK_01.bottom", "PIPE_01", shutoff=shutoff, coefficient=b, exponent=n,
                running=True, speed=0.02)  # fmt: skip
    pipe = Resistance("PIPE_01", "P_01", "TK_02.top", r=pipe_resistance(4, 0.04, 0.02, 1.5))
    net = Network.build([sump, head], [pump, pipe])
    for k in range(100):
        pump.speed = min(1.0, 0.02 * (k + 1))  # ramping over 1 s
        net.step(DT)
    assert pump.flow > 5 / 3600 and pipe.flow == pytest.approx(pump.flow)


def test_a_pump_at_its_shutoff_head_closes_instead_of_trickling():
    """EPANET's pump rule: an open pump whose lift reaches its speed-adjusted shutoff head closes,
    so a lift Euler took a hair past shutoff gives no flow, not solver noise."""
    sump = tank("TK_01", area=50.0, level=1.0)
    shutoff, b, n = pump_curve([(6 / 3600, 8.0)])
    upper = tank("TK_02", elevation=2.0, area=0.02, height=12.0, level=shutoff - 1.0 + 1e-6)
    pump = Pump("P_01", "TK_01.bottom", "TK_02.bottom", shutoff=shutoff, coefficient=b,
                exponent=n, running=True)  # fmt: skip
    net = Network.build([sump, upper], [pump])
    net.step(DT)
    assert pump.flow == 0.0 and pump.held_closed
