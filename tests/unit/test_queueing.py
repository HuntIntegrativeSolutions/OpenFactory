"""PLAN 2.0: headless part-to-part queueing along the flow axis.

Layout: belt A (3 m, 0.5 m/s) feeds belt B (2 m, 1.0 m/s) through a connection. B is stopped
while five boxes arrive, so they queue nose to tail, the first on B's infeed and the rest on A
behind it. When B restarts, it runs faster than A, so the parts leave with gaps, and a photo eye
on B sees each one. (At equal speeds the whole queue would accelerate together at μ·g and no gap
would open, in this model or in Rapier.)
"""

from __future__ import annotations

from openfactory.core.iotable import Writer
from openfactory.core.loader import LoadedScene, build
from openfactory.core.schema import Scene

DT = 0.02
BOX = [0.3, 0.2, 0.3]


def queue_scene(*, emitter_count: int = 5, extra=()) -> LoadedScene:
    data = {
        "openfactory": "0.1",
        "scene": {"name": "queue", "description": "Accumulation behind a stopped belt"},
        "components": [
            {
                "id": "CONV_A",
                "type": "belt_conveyor",
                "description": "Accumulation Belt",
                "position": [0, 0, 0],
                "length": 3.0,
                "width": 0.5,
                "speed_mps": 0.5,
                "io": {"run": "CONV_A_RUN_DO"},
            },
            {
                "id": "CONV_B",
                "type": "belt_conveyor",
                "description": "Metering Belt",
                "position": [3.0, 0, 0],
                "length": 2.0,
                "width": 0.5,
                "speed_mps": 1.0,
                "io": {"run": "CONV_B_RUN_DO"},
            },
            {
                "id": "PE_B",
                "type": "photo_eye",
                "description": "Metering Belt Gap Photo Eye",
                "position": [4.2, 0.1, 0],
                "beam_length": 0.6,
                "io": {"detect": "PE_B_DI"},
            },
            {
                "id": "EMT_A",
                "type": "box_emitter",
                "description": "Accumulation Belt Box Source",
                "position": [0.2, 0.11, 0],
                "interval_s": 1.0,
                "parts": [{"name": "box", "size": BOX, "weight": 1.0}],
                "io": {"enable": "EMT_A_EN_DO"},
            },
            *extra,
        ],
        "connections": [{"from": "CONV_A", "to": "CONV_B"}],
    }
    return build(Scene.model_validate(data))


def run(loaded: LoadedScene, seconds: float, record=None) -> None:
    for _ in range(round(seconds / DT)):
        loaded.step(DT)
        if record is not None:
            record(loaded)


def flow_order(loaded: LoadedScene):
    """Carried parts front to back with their front and back faces in A's frame (x along flow)."""
    out = []
    for p in loaded.world.parts.values():
        x = p.position[0]  # both belts are on the x axis, so world x is the A-frame coordinate
        out.append((x + p.size[0] / 2, x - p.size[0] / 2, p))
    return sorted(out, key=lambda t: -t[0])


def accumulate(loaded: LoadedScene) -> None:
    io = loaded.io
    io.set("CONV_A_RUN_DO", True, Writer.PLC)
    io.set("EMT_A_EN_DO", True, Writer.PLC)
    while loaded.component("EMT_A").spawned < 5:
        run(loaded, DT)
    io.set("EMT_A_EN_DO", False, Writer.PLC)
    run(loaded, 12.0)  # every box reaches the queue


def test_five_parts_queue_nose_to_tail_behind_a_stopped_belt():
    loaded = queue_scene()
    accumulate(loaded)
    order = flow_order(loaded)
    assert len(order) == 5 and all(p.carrier for _, _, p in order)
    gaps = [round(order[i][1] - order[i + 1][0], 9) for i in range(4)]
    carriers = [p.carrier for _, _, p in order]
    speeds = [p.v_flow for _, _, p in order]
    print(f"carriers {carriers}\nfronts {[round(f, 4) for f, _, _ in order]}\ngaps {gaps}")
    assert all(g >= 0 for g in gaps)  # no overlap anywhere, including across the A/B seam
    assert all(g <= 1e-6 for g in gaps)  # nose to tail
    assert carriers == ["CONV_B"] + ["CONV_A"] * 4  # the leader sits on B's infeed
    assert all(abs(v) < 1e-9 for v in speeds)  # held, slipping on the running belt


def test_restart_releases_the_queue_in_order_with_a_gap_the_eye_sees():
    loaded = queue_scene()
    accumulate(loaded)
    ids = [p.id for _, _, p in flow_order(loaded)]
    edges: list[tuple[int, bool]] = []
    removed_order: list[str] = []
    before = set(loaded.world.parts)

    def record(lo: LoadedScene) -> None:
        eye = lo.component("PE_B")
        if not edges or edges[-1][1] != eye.detect:
            edges.append((lo.tick, eye.detect))
        for pid in before - set(lo.world.parts):
            if pid not in removed_order:
                removed_order.append(pid)

    edges.append((loaded.tick, False))
    loaded.io.set("CONV_B_RUN_DO", True, Writer.PLC)
    run(loaded, 8.0, record)
    rising = [t for t, on in edges if on]
    print(f"PE_B edges (tick, detect): {edges[1:]}")
    print(f"released in order {removed_order} (queue order {ids})")
    assert len(rising) == 5  # one detection per part: the eye saw every gap
    assert removed_order == ids  # first in, first out


def test_parts_beside_each_other_do_not_block():
    loaded = queue_scene()
    a = loaded.world.spawn("box", tuple(BOX), (1.0, 0.1, -0.1))
    b = loaded.world.spawn("box", tuple(BOX), (1.2, 0.1, 0.0))  # overlaps a's footprint ahead
    beside = loaded.world.spawn("box", tuple(BOX), (1.0, 0.1, 0.5))  # off the belt's z range
    loaded.io.set("CONV_A_RUN_DO", True, Writer.PLC)
    run(loaded, 0.1)  # land
    lane = loaded.world.spawn("box", tuple(BOX), (2.0, 0.1, 0.0))
    for p in (a, b, lane):
        p.carrier, p.v_flow = "CONV_A", 0.0
    del beside
    run(loaded, 1.0)
    assert b.position[0] - a.position[0] >= 0.3 - 1e-9  # a queued behind b (they overlap in z)
    assert lane.position[0] - b.position[0] >= 0.3 - 1e-9


def test_result_does_not_depend_on_part_dict_order():
    def final(reverse: bool):
        loaded = queue_scene()
        accumulate(loaded)
        if reverse:
            loaded.world.parts = dict(reversed(list(loaded.world.parts.items())))
        loaded.io.set("CONV_B_RUN_DO", True, Writer.PLC)
        run(loaded, 1.5)
        return sorted(
            (p.id, tuple(round(v, 9) for v in p.position)) for p in loaded.world.parts.values()
        )

    assert final(False) == final(True)
