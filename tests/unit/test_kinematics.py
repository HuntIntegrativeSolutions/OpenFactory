"""PLAN 1.5: headless part motion on chained conveyors, transfers, falling, photo eye, remover."""

from __future__ import annotations

import math

import pytest

from openfactory.core.iotable import Writer
from openfactory.core.loader import build
from openfactory.core.schema import Scene

DT = 0.02
SPEED = 0.5
BOX = (0.3, 0.2, 0.3)
MU = 0.8  # scene default friction
ACCEL = MU * 9.81  # friction-limited part acceleration, m/s^2


def conveyor(cid, position, length=2.0, rotation=0.0):
    return {
        "id": cid,
        "type": "belt_conveyor",
        "description": f"Belt Conveyor {cid}",
        "position": list(position),
        "rotation_deg": rotation,
        "length": length,
        "width": 0.5,
        "speed_mps": SPEED,
        "io": {"run": f"{cid}_RUN_DO"},
    }


def scene(*components, connections=(), friction=MU):
    loaded = build(
        Scene.model_validate(
            {
                "openfactory": "0.1",
                "scene": {
                    "name": "kinematics-test",
                    "description": "Kinematics test",
                    "friction": friction,
                },
                "components": list(components),
                "connections": [{"from": a, "to": b} for a, b in connections],
            }
        )
    )
    for comp in loaded.components:  # start every conveyor
        if comp.type == "belt_conveyor":
            loaded.io.set(comp.bindings["run"], True, Writer.PLC)
    return loaded


def up_to_speed(loaded):
    for _ in range(20):  # the 0.3 s ramp is 15 ticks
        loaded.step(DT)
    assert all(
        c.surface_speed == pytest.approx(SPEED) for c in loaded.components if hasattr(c, "travel")
    )


def place(loaded, x, y=0.1, z=0.0, size=BOX):
    """A part at rest on the belt (bottom at y=0 for a 0.2 m box at y=0.1); lands next step."""
    return loaded.world.spawn("short", size, (x, y, z))


def place_moving(loaded, carrier, x, z=0.0):
    """A part already riding ``carrier`` at belt speed (no landing, no friction ramp)."""
    part = place(loaded, x, z=z)
    part.carrier, part.v_flow = carrier, SPEED
    return part


# --- Done when: two chained conveyors trip a photo eye at the expected time ± 1 tick ------------


def test_part_travels_two_chained_conveyors_and_trips_photo_eye_on_time():
    loaded = scene(
        conveyor("CONV_01", (0, 0, 0)),
        conveyor("CONV_02", (2, 0, 0)),
        {
            "id": "PE_02",
            "type": "photo_eye",
            "description": "Discharge Photo Eye",
            "position": [3.0, 0.1, 0.0],
            "beam_length": 0.6,
            "io": {"detect": "PE_02_DI"},
        },
        connections=[("CONV_01", "CONV_02")],
    )
    up_to_speed(loaded)
    part = place(loaded, x=0.3)
    start = loaded.tick

    # Expected, on paper: the part lands in its first tick at rest, then friction accelerates it
    # at mu*g = 7.85 m/s^2 to the belt's 0.5 m/s, which costs v^2 / (2 mu g) = 0.016 m against
    # moving at belt speed from the start. Its leading edge (center + 0.15) reaches the beam at
    # x = 3.0 when the center has moved d = 2.85 - 0.3 = 2.55 m:
    # moving ticks = ceil((d + v^2/(2 mu g)) / (v dt)) = ceil(256.6) = 257;
    # + 1 landing tick + 1 tick debounce = 259.
    lag = SPEED**2 / (2 * ACCEL)
    expected = 1 + math.ceil(round((3.0 - BOX[0] / 2 - 0.3 + lag) / (SPEED * DT), 9)) + 1
    assert expected == 259

    tripped_at = None
    carriers = []
    for _ in range(400):
        loaded.step(DT)
        carriers.append(part.carrier)
        if loaded.io.get("PE_02_DI"):
            tripped_at = loaded.tick - start
            break
    assert tripped_at is not None, "photo eye never tripped"
    print(f"PE_02 tripped at tick {tripped_at}; expected {expected} ± 1")
    assert abs(tripped_at - expected) <= 1, f"tripped at tick {tripped_at}, expected {expected}±1"
    assert "CONV_01" in carriers and carriers[-1] == "CONV_02"  # handed off on the way


def test_collinear_handoff_has_no_position_jump():
    loaded = scene(
        conveyor("CONV_01", (0, 0, 0)),
        conveyor("CONV_02", (2, 0, 0)),
        connections=[("CONV_01", "CONV_02")],
    )
    up_to_speed(loaded)
    part = place(loaded, x=1.7)
    for _ in range(6):  # lands, then 4 ticks of friction ramp to belt speed
        loaded.step(DT)
    assert part.v_flow == SPEED
    xs = []
    for _ in range(40):
        loaded.step(DT)
        xs.append(part.position[0])
    steps = [b - a for a, b in zip(xs, xs[1:], strict=False)]
    assert all(s == pytest.approx(SPEED * DT, abs=1e-9) for s in steps)
    assert part.carrier == "CONV_02" and part.position[1] == pytest.approx(0.1)


# --- transfers across gaps, steps, and turns ----------------------------------------------------


def test_connection_bridges_gap_and_height_step():
    loaded = scene(
        conveyor("CONV_01", (0, 0, 0)),
        conveyor("CONV_02", (2.5, -0.1, 0.1)),  # 0.5 m gap, 0.1 m lower, offset 0.1 m in z
        connections=[("CONV_01", "CONV_02")],
    )
    up_to_speed(loaded)
    part = place_moving(loaded, "CONV_01", x=1.995, z=0.05)
    loaded.step(DT)  # 2.005: past the discharge end -> onto CONV_02's infeed
    assert part.carrier == "CONV_02" and part.v_flow == SPEED  # speed carried over
    # overshoot carried over, height snapped to the lower belt, 0.05 m lateral offset kept relative
    # to CONV_02's centerline (z = 0.1)
    assert part.position == pytest.approx((2.5 + 0.005, -0.1 + 0.1, 0.1 + 0.05))


def test_connection_through_a_90_degree_turn():
    # CONV_02 turns the flow: rotation +90 deg points its local +x at world -z
    loaded = scene(
        conveyor("CONV_01", (0, 0, 0)),
        conveyor("CONV_02", (2.25, 0, -0.25), rotation=90),
        connections=[("CONV_01", "CONV_02")],
    )
    up_to_speed(loaded)
    part = place_moving(loaded, "CONV_01", x=1.995)
    loaded.step(DT)
    assert part.carrier == "CONV_02"
    before = part.position
    loaded.step(DT)
    assert part.position[0] == pytest.approx(before[0])
    assert part.position[2] == pytest.approx(before[2] - SPEED * DT)


# --- falling, landing, and lost parts -----------------------------------------------------------


def test_unconnected_discharge_part_falls_and_is_lost():
    loaded = scene(conveyor("CONV_01", (0, 0, 0)))
    up_to_speed(loaded)
    part = place_moving(loaded, "CONV_01", x=1.995)
    loaded.step(DT)
    assert part.carrier is None  # left the belt
    for _ in range(100):
        loaded.step(DT)
    assert part.id not in loaded.world.parts
    assert loaded.world.lost["short"] == 1


def test_dropped_part_lands_after_free_fall_time():
    loaded = scene(conveyor("CONV_01", (0, 0, 0)))
    part = loaded.world.spawn("tall", (0.3, 0.4, 0.3), (0.2, 0.3, 0.0))  # bottom 0.1 m above belt
    ticks = 0
    while part.carrier is None:
        loaded.step(DT)
        ticks += 1
        assert ticks < 50
    expected = math.sqrt(2 * 0.1 / 9.81) / DT  # about 7.1 ticks
    assert abs(ticks - expected) <= 2
    assert part.position[1] == pytest.approx(0.2)  # rests on the belt top


def test_part_pushed_beyond_belt_edge_falls():
    loaded = scene(conveyor("CONV_01", (0, 0, 0)))
    part = place(loaded, x=1.0)
    loaded.step(DT)
    assert part.carrier == "CONV_01"
    part.position = (1.0, 0.1, 0.3)  # e.g. moved sideways past the 0.25 m half-width
    loaded.step(DT)
    assert part.carrier is None


def test_part_not_over_any_belt_is_lost():
    loaded = scene(conveyor("CONV_01", (0, 0, 0)))
    loaded.world.spawn("short", BOX, (5.0, 0.5, 0.0))
    for _ in range(100):
        loaded.step(DT)
    assert loaded.world.lost["short"] == 1


def test_stopped_belt_holds_part():
    loaded = scene(conveyor("CONV_01", (0, 0, 0)))
    for comp in loaded.components:
        loaded.io.set(comp.bindings["run"], False, Writer.PLC)
    part = place(loaded, x=1.0)
    for _ in range(50):
        loaded.step(DT)
    assert part.position[0] == pytest.approx(1.0)
    assert part.carrier == "CONV_01"


# --- remover and emitter in the loop ------------------------------------------------------------


def test_remover_at_end_of_line_takes_conveyed_part():
    loaded = scene(
        conveyor("CONV_01", (0, 0, 0)),
        {
            "id": "REM_01",
            "type": "part_remover",
            "description": "Discharge Part Remover",
            "position": [1.9, 0.1, 0.0],
            "size": [0.2, 0.5, 0.6],
        },
    )
    up_to_speed(loaded)
    place(loaded, x=0.5)
    for _ in range(200):
        loaded.step(DT)
    assert loaded.world.parts == {}
    assert loaded.world.removed["short"] == 1 and loaded.world.lost == {}


def test_emitter_parts_drop_onto_belt_and_travel():
    loaded = scene(
        conveyor("CONV_01", (0, 0, 0), length=4.0),
        {
            "id": "EMT_01",
            "type": "box_emitter",
            "description": "Infeed Box Source",
            "position": [0.2, 0.3, 0.0],
            "interval_s": 1.0,
            "parts": [{"name": "short", "size": list(BOX), "weight": 1}],
        },
    )
    for _ in range(150):  # 3 s
        loaded.step(DT)
    xs = sorted(p.position[0] for p in loaded.world.parts.values())
    assert len(xs) == 3
    assert all(p.carrier == "CONV_01" for p in loaded.world.parts.values())
    assert xs[-1] > xs[0] + 0.9  # first part has travelled about a meter further than the last


# --- friction-limited acceleration (scene.friction) --------------------------------------------


@pytest.mark.parametrize(("mu", "expected_ticks"), [(0.8, 4), (0.3, 9), (0.1, 26)])
def test_part_reaches_belt_speed_in_friction_limited_time(mu, expected_ticks):
    """On paper: t = v / (mu g); at 0.5 m/s that is 64 ms (mu 0.8), 170 ms (0.3), 510 ms (0.1)."""
    loaded = scene(conveyor("CONV_01", (0, 0, 0)), friction=mu)
    up_to_speed(loaded)
    part = place(loaded, x=0.3)
    loaded.step(DT)  # lands at rest
    assert part.carrier == "CONV_01" and part.v_flow == 0.0
    ticks = 0
    speeds = []
    while part.v_flow < SPEED:
        loaded.step(DT)
        ticks += 1
        speeds.append(part.v_flow)
        assert ticks < 100
    assert expected_ticks == math.ceil(round(SPEED / (mu * 9.81) / DT, 9))
    assert ticks == expected_ticks
    # constant mu*g acceleration until the last (partial) step
    steps = [b - a for a, b in zip([0.0, *speeds], speeds, strict=False)]
    assert all(s == pytest.approx(mu * 9.81 * DT) for s in steps[:-1])
    print(f"mu={mu}: belt speed after {ticks} ticks ({ticks * DT:.2f} s)")


def test_part_slides_on_when_belt_stops_faster_than_friction_allows():
    # the belt coasts down at 0.5 m/s per 0.3 s = 1.67 m/s^2; mu 0.1 only allows 0.98 m/s^2
    for mu, slides in ((0.8, False), (0.1, True)):
        loaded = scene(conveyor("CONV_01", (0, 0, 0), length=4.0), friction=mu)
        up_to_speed(loaded)
        part = place_moving(loaded, "CONV_01", x=0.5)
        loaded.io.set("CONV_01_RUN_DO", False, Writer.PLC)
        belt = loaded.component("CONV_01")
        for _ in range(10):
            loaded.step(DT)
        # kinematics runs before the conveyor steps, so the part tracks last tick's belt speed
        last_tick_belt = belt.surface_speed + SPEED / 0.3 * DT
        assert (part.v_flow > last_tick_belt + 1e-9) is slides


def test_scene_friction_default_and_bounds():
    base = {"openfactory": "0.1", "components": [conveyor("CONV_01", (0, 0, 0))]}
    meta = {"name": "f", "description": "Friction"}
    assert Scene.model_validate({**base, "scene": meta}).scene.friction == 0.8
    with pytest.raises(ValueError, match="greater than 0"):
        Scene.model_validate({**base, "scene": {**meta, "friction": 0}})
