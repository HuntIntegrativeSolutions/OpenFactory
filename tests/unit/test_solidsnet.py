"""PLAN 5.7a, ADR-0006: the solids network (`core/solidsnet.py`), tested before its components.
Mass balance over 300 s with every link starting, stopping and blocking; determinism; the dead
time of a carrier is its travel time; a full bin blocks its feeder; the blocked-feeder rule; no
wall clock."""

from __future__ import annotations

import ast
import json
import math
from pathlib import Path

import pytest

from openfactory.core.solidsnet import Bin, Carrier, Discharge, Feeder, Network, Supply

DT = 0.02  # 50 Hz
SRC = Path(__file__).resolve().parents[2] / "src" / "openfactory" / "core" / "solidsnet.py"


def plant() -> Network:
    """The shipped scene's shape: a supply fills a silo through a gate; an airlock under the silo
    feeds a screw into a weigh hopper; a gate under the hopper empties it to a discharge."""
    return Network.build(
        [
            Supply("TRUCK", capacity=40.0),
            Bin("SILO", capacity=3000.0, mass=1500.0),
            Bin("HOPPER", capacity=300.0, mass=0.0),
            Discharge("MIXER", demand=25.0),
        ],
        [
            Feeder("FILL", "TRUCK", "SILO", capacity=30.0),
            Feeder("RV", "SILO", "SCREW", capacity=12.0),
            Carrier("SCREW", "RV", "HOPPER", capacity=10.0, travel_s=4.0),
            Feeder("GATE", "HOPPER", "MIXER", capacity=40.0),
        ],
    )


def drive(net: Network, t: float) -> None:
    """Every link starting, stopping and running part speed; the hopper filling past full so the
    screw blocks and then the airlock under it; a plugged outlet and a bridge, each a while."""
    lk = net.links
    lk["FILL"].speed = 1.0 if (t % 50) < 20 else 0.0
    lk["RV"].speed = 0.0 if 100 <= t < 110 else 1.0
    lk["SCREW"].speed = 0.0 if (t % 37) > 30 else (0.35 if t < 60 else 1.0)
    lk["GATE"].speed = 1.0 if (t % 90) > 70 else 0.0  # the hopper fills full first
    lk["SCREW"].plugged = 200 <= t < 205
    net.nodes["SILO"].bridged = 220 <= t < 230  # type: ignore[attr-defined]
    net.nodes["TRUCK"].enabled = not 240 <= t < 245  # type: ignore[attr-defined]


def run(seconds: float = 300.0) -> tuple[Network, list[dict]]:
    net = plant()
    start = net.held()
    trace, worst = [], 0.0
    blocked = {k: False for k in net.links}
    for n in range(round(seconds / DT)):
        drive(net, n * DT)
        net.step(DT)
        balance = net.held() - (start + net.supplied() - net.discharged())
        worst = max(worst, abs(balance))
        for k, lk in net.links.items():
            blocked[k] |= lk.blocked
        trace.append({k: (v.mass if isinstance(v, Bin) else v.total) for k, v in net.nodes.items()})
    net.worst_balance = worst  # type: ignore[attr-defined]
    net.ever_blocked = blocked  # type: ignore[attr-defined]
    return net, trace


def test_mass_balances_every_tick_over_300_s_with_every_link_starting_stopping_and_blocking():
    net, _ = run()
    through = 1500.0 + net.supplied() + net.discharged()  # every kg held or passed
    relative = net.worst_balance / through  # type: ignore[attr-defined]
    print(
        f"worst mass balance over 300 s: {net.worst_balance:.3e} kg of {through:.0f} kg "
        f"({relative:.1e})"
    )  # type: ignore[attr-defined]
    # float precision: rounding of order 1e-16 a step, never a lost gram
    assert relative < 1e-12
    # it was tested hard: every link moved material, and the screw and airlock were blocked
    assert all(lk.total > 0 for lk in net.links.values())
    assert net.ever_blocked["SCREW"] and net.ever_blocked["RV"]  # type: ignore[attr-defined]
    hopper = net.nodes["HOPPER"]
    assert isinstance(hopper, Bin) and hopper.mass <= hopper.capacity
    screw = net.links["SCREW"]
    assert isinstance(screw, Carrier)
    assert screw.load <= screw.capacity * screw.travel_s + 1e-9  # never overfilled


def test_two_runs_give_the_same_bytes():
    a = json.dumps(run(60)[1]).encode()
    b = json.dumps(run(60)[1]).encode()
    assert a == b


@pytest.mark.parametrize("travel_s", [0.5, 4.0, 7.3])
def test_the_dead_time_of_a_carrier_is_its_travel_time(travel_s):
    net = Network.build(
        [Bin("A", capacity=100.0, mass=50.0), Bin("B", capacity=100.0)],
        [Carrier("SC", "A", "B", capacity=1.0, travel_s=travel_s)],
    )
    net.links["SC"].speed = 1.0
    first = None
    for n in range(1, 2000):
        net.step(DT)
        if first is None and net.nodes["B"].mass > 0:  # type: ignore[attr-defined]
            first = n
            break
    # the first tick's intake arrives N ticks later, N = round(travel_s / dt)
    assert first is not None
    assert (first - 1) * DT == pytest.approx(round(travel_s / DT) * DT, abs=1e-12)
    assert abs((first - 1) * DT - travel_s) <= DT / 2 + 1e-12


def test_half_speed_doubles_the_dead_time():
    net = Network.build(
        [Bin("A", capacity=100.0, mass=50.0), Bin("B", capacity=100.0)],
        [Carrier("SC", "A", "B", capacity=1.0, travel_s=2.0)],
    )
    net.links["SC"].speed = 0.5
    n = 0
    while net.nodes["B"].mass == 0:  # type: ignore[attr-defined]
        net.step(DT)
        n += 1
    assert (n - 1) * DT == pytest.approx(4.0, abs=2 * DT)


def test_a_full_bin_blocks_its_feeder():
    net = Network.build(
        [Bin("SILO", capacity=1000.0, mass=1000.0), Bin("HOP", capacity=10.0, mass=9.9)],
        [Feeder("RV", "SILO", "HOP", capacity=5.0)],
    )
    net.links["RV"].speed = 1.0
    net.step(DT)  # 0.1 kg of room, 0.1 kg asked for: it fills, not blocked
    net.step(DT)
    rv, hop = net.links["RV"], net.nodes["HOP"]
    assert hop.mass == pytest.approx(10.0)  # type: ignore[attr-defined]
    assert rv.moved == 0.0 and rv.blocked
    assert net.nodes["SILO"].mass == pytest.approx(999.9)  # type: ignore[attr-defined]


def test_a_blocked_screw_holds_its_load_and_never_overfills():
    """The blocked-feeder rule (ADR-0006 decision 3): a screw whose outlet bin is full stops
    conveying and keeps what is on it; the airlock above it fills its inlet slot and then blocks;
    when the bin has room again the screw delivers what it held."""
    net = Network.build(
        [Bin("SILO", capacity=1e4, mass=1e4), Bin("HOP", capacity=20.0)],
        [
            Feeder("RV", "SILO", "SC", capacity=8.0),
            Carrier("SC", "RV", "HOP", capacity=5.0, travel_s=2.0),
        ],  # fmt: skip
    )
    rv, sc, hop = net.links["RV"], net.links["SC"], net.nodes["HOP"]
    rv.speed = sc.speed = 1.0
    for _ in range(500):  # 10 s: the hopper fills (20 kg at 5 kg/s) and the screw backs up
        net.step(DT)
    assert hop.mass == pytest.approx(20.0)  # type: ignore[attr-defined]
    assert sc.blocked and rv.blocked and rv.moved == 0.0
    assert sc.load <= 5.0 * 2.0 + 1e-9  # capacity x travel time
    held = sc.load
    sc.speed = 0.0  # stopped under a running airlock: never overfilled
    for _ in range(200):
        net.step(DT)
    assert sc.load == pytest.approx(held)
    hop.mass = 0.0  # type: ignore[attr-defined]
    sc.speed = 1.0
    for _ in range(round(2.0 / DT) + 1):
        net.step(DT)
    assert hop.mass >= held - 1e-9  # type: ignore[attr-defined]


def test_a_stopped_link_takes_nothing_from_a_bin_and_a_bridge_gives_nothing():
    net = Network.build(
        [Bin("A", capacity=100.0, mass=50.0), Bin("B", capacity=100.0)],
        [Feeder("G", "A", "B", capacity=10.0)],
    )
    net.step(DT)
    assert net.nodes["B"].mass == 0.0  # type: ignore[attr-defined]
    net.links["G"].speed = 1.0
    net.nodes["A"].bridged = True  # type: ignore[attr-defined]
    net.step(DT)
    assert net.nodes["B"].mass == 0.0  # type: ignore[attr-defined]
    net.nodes["A"].bridged = False  # type: ignore[attr-defined]
    net.step(DT)
    assert net.nodes["B"].mass == pytest.approx(10.0 * DT)  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("links", "message"),
    [
        ([Feeder("G", "NOPE", "B", capacity=1.0)], "names no bulk component"),
        ([Feeder("G", "A", "NOPE", capacity=1.0)], "names no bulk component"),
        (
            [Feeder("G", "A", "B", capacity=1.0), Feeder("H", "G", "B", capacity=1.0)],
            "is a feeder",
        ),  # fmt: skip
        ([Feeder("G", "A", "S", capacity=1.0)], "is a supply"),
    ],
)
def test_an_end_that_cannot_join_is_refused(links, message):
    with pytest.raises(ValueError, match=message):
        Network.build([Bin("A", capacity=1.0), Bin("B", capacity=1.0), Supply("S")], links)


def test_a_loop_is_stepped_in_a_fixed_order():
    nodes = [Bin("A", capacity=10.0, mass=5.0), Bin("B", capacity=10.0, mass=5.0)]
    links = [Feeder("AB", "A", "B", capacity=1.0), Feeder("BA", "B", "A", capacity=1.0)]
    net = Network.build(nodes, links)
    assert [lk.id for lk in net.order] == ["BA", "AB"]  # AB closes no loop first: upstream


def test_no_wall_clock():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    mods = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import | ast.ImportFrom)
            for a in n.names} | {n.module for n in ast.walk(tree)
                                 if isinstance(n, ast.ImportFrom) and n.module}  # fmt: skip
    assert not {"time", "datetime", "random"} & (names | mods)
    assert math.isfinite(DT)
