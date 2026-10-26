"""box_emitter: enable, interval and jitter timing, weighted choice, 200-part cap, determinism,
and (PLAN 2.0) waiting for a clear spawn volume.

The component rig runs no kinematic model, so a spawned part would sit at the emitter forever and
block every later spawn. ``run`` moves parts 1 m away after each tick, as a running belt does.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from openfactory.core.iotable import Writer
from openfactory.core.loader import load
from openfactory.core.world import MAX_PARTS

EMT = {
    "id": "EMT_01",
    "type": "box_emitter",
    "description": "Infeed Box Source",
    "position": [0.2, 0.3, 0.0],
    "interval_s": 1.0,  # 50 ticks at 50 Hz
    "parts": [
        {"name": "tall", "size": [0.3, 0.4, 0.3], "weight": 0.5},
        {"name": "short", "size": [0.3, 0.2, 0.3], "weight": 0.5},
    ],
    "io": {"enable": "EMT_01_EN_DO"},
}


def run(r, n: int) -> None:
    """Step ``n`` ticks, moving every part 1 m along +x after each (a belt carrying it away)."""
    for _ in range(n):
        r.step()
        for part in r.world.parts.values():
            part.position = (part.position[0] + 1.0, *part.position[1:])


def test_idle_until_enabled(rig):
    r = rig(EMT)
    r.step(200)
    assert r.world.parts == {}


def test_spawns_at_enable_then_every_interval(rig):
    r = rig(EMT)
    r.plc("EMT_01_EN_DO", True)
    r.step(1)
    assert len(r.world.parts) == 1
    part = next(iter(r.world.parts.values()))
    assert part.position == (0.2, 0.3, 0.0) and part.source == "EMT_01"
    part.position = (1.2, 0.3, 0.0)  # carried away
    run(r, 49)  # 0.98 s after the first spawn
    assert len(r.world.parts) == 1
    run(r, 1)  # 1.00 s
    assert len(r.world.parts) == 2
    run(r, 100)
    assert len(r.world.parts) == 4


def test_disable_resets_timer(rig):
    r = rig(EMT)
    r.plc("EMT_01_EN_DO", True)
    run(r, 10)
    r.plc("EMT_01_EN_DO", False)
    run(r, 10)
    assert len(r.world.parts) == 1
    r.plc("EMT_01_EN_DO", True)
    run(r, 1)  # re-enable spawns at once
    assert len(r.world.parts) == 2
    assert r["EMT_01"].state() == {"enabled": True, "spawned": 2, "skipped": 0, "waiting_ticks": 0}


def test_unbound_enable_is_always_on(rig):
    r = rig({**EMT, "io": {}})
    r.step(1)
    assert len(r.world.parts) == 1


def test_jitter_stays_in_band_and_is_seeded(rig):
    def spawn_ticks(seed):
        r = rig({**EMT, "jitter_s": 0.3, "io": {}}, seed=seed)
        ticks = []
        for tick in range(1, 2001):
            before = r["EMT_01"].spawned
            run(r, 1)
            if r["EMT_01"].spawned > before:
                ticks.append(tick)
        return ticks

    ticks = spawn_ticks(42)
    gaps = [(b - a) * 0.02 for a, b in zip(ticks, ticks[1:], strict=False)]
    assert all(0.7 - 0.021 <= g <= 1.3 + 0.021 for g in gaps)
    assert len(set(gaps)) > 5  # jitter actually varies the interval
    assert spawn_ticks(42) == ticks  # same seed, same timing
    assert spawn_ticks(7) != ticks


def test_weighted_choice_and_determinism(rig):
    def names(seed):
        r = rig({**EMT, "interval_s": 0.02, "io": {}}, seed=seed)
        run(r, 150)
        return [p.name for p in r.world.parts.values()]

    first = names(42)
    counts = Counter(first)
    assert counts["tall"] + counts["short"] == 150
    assert 45 <= counts["tall"] <= 105  # 50/50 weights
    assert names(42) == first


def test_respects_200_part_cap(rig):
    r = rig({**EMT, "interval_s": 0.02, "io": {}})
    run(r, MAX_PARTS + 25)
    assert len(r.world.parts) == MAX_PARTS
    state = r["EMT_01"].state()
    assert state["spawned"] == MAX_PARTS and state["skipped"] == 25


def test_geometry_lists_part_types(rig):
    geometry = rig(EMT)["EMT_01"].geometry()
    assert [p["name"] for p in geometry["parts"]] == ["tall", "short"]
    assert geometry["position"] == [0.2, 0.3, 0.0]


# --- PLAN 2.0: wait for a clear spawn volume -----------------------------------------------------


def test_waits_while_the_spawn_volume_is_occupied_then_restarts_the_interval(rig):
    r = rig({**EMT, "io": {}})
    r.step(1)  # first part spawns and, with no belt, stays in the spawn volume
    r.step(149)  # two spawns fall due (ticks 51 and 101) but the volume is blocked
    emitter = r["EMT_01"]
    assert len(r.world.parts) == 1 and emitter.waiting_ticks == 100
    for part in r.world.parts.values():
        part.position = (5.0, *part.position[1:])  # carried away at tick 150
    r.step(1)
    assert len(r.world.parts) == 2  # the held part spawns at once, and only one: no burst
    run(r, 49)
    assert len(r.world.parts) == 2
    run(r, 1)  # the next interval counts from the late spawn
    assert len(r.world.parts) == 3


def test_held_part_keeps_the_random_sequence(rig):
    def names(block: bool):
        r = rig({**EMT, "io": {}}, seed=3)
        for tick in range(400):
            r.step()
            if not block or tick % 80 > 60:  # blocked for most of each 80-tick window
                for part in r.world.parts.values():
                    part.position = (part.position[0] + 1.0, *part.position[1:])
        return [p.name for p in r.world.parts.values()], r["EMT_01"].waiting_ticks

    (free, free_waits), (blocked, blocked_waits) = names(False), names(True)
    assert free_waits == 0 and blocked_waits > 0  # the blocked run really waited
    n = min(len(free), len(blocked))
    assert n >= 5 and blocked[:n] == free[:n]  # same part types in the same order


def test_chattering_enable_no_longer_stacks_boxes():
    """The 1.14 startup glitch: enable on, off, on within 0.3 s gave two boxes on top of each
    other. Now the second waits until the first has moved clear of the spawn volume."""
    loaded = load(Path(__file__).resolve().parents[2] / "scenes" / "sorting-by-height.yaml")
    io = loaded.io
    io.set("CONV_01_RUN_DO", True, Writer.PLC)
    for tick, enable in [(5, True), (11, False), (16, True)]:  # 0.10 / 0.22 / 0.32 s
        while loaded.tick < tick:
            loaded.step(0.02)
        io.set("EMT_01_EN_DO", enable, Writer.PLC)
    worst = 0.0
    for _ in range(500):
        loaded.step(0.02)
        parts = list(loaded.world.parts.values())
        for i, a in enumerate(parts):
            for b in parts[i + 1 :]:
                (alo, ahi), (blo, bhi) = a.aabb(), b.aabb()
                overlap = min(min(ahi[k], bhi[k]) - max(alo[k], blo[k]) for k in range(3))
                worst = max(worst, overlap)
    emitter = loaded.component("EMT_01")
    print(f"spawned {emitter.spawned}, waited {emitter.waiting_ticks} ticks, worst overlap {worst}")
    assert emitter.waiting_ticks > 0  # the re-enable spawn had to wait
    assert worst <= 1e-9  # no two parts ever overlap
