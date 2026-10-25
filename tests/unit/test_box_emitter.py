"""box_emitter: enable, interval and jitter timing, weighted choice, 200-part cap, determinism."""

from __future__ import annotations

from collections import Counter

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
    r.step(49)  # 0.98 s after the first spawn
    assert len(r.world.parts) == 1
    r.step(1)  # 1.00 s
    assert len(r.world.parts) == 2
    r.step(100)
    assert len(r.world.parts) == 4


def test_disable_resets_timer(rig):
    r = rig(EMT)
    r.plc("EMT_01_EN_DO", True)
    r.step(10)
    r.plc("EMT_01_EN_DO", False)
    r.step(10)
    assert len(r.world.parts) == 1
    r.plc("EMT_01_EN_DO", True)
    r.step(1)  # re-enable spawns at once
    assert len(r.world.parts) == 2
    assert r["EMT_01"].state() == {"enabled": True, "spawned": 2, "skipped": 0}


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
            r.step()
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
        r.step(150)
        return [p.name for p in r.world.parts.values()]

    first = names(42)
    counts = Counter(first)
    assert counts["tall"] + counts["short"] == 150
    assert 45 <= counts["tall"] <= 105  # 50/50 weights
    assert names(42) == first


def test_respects_200_part_cap(rig):
    r = rig({**EMT, "interval_s": 0.02, "io": {}})
    r.step(MAX_PARTS + 25)
    assert len(r.world.parts) == MAX_PARTS
    state = r["EMT_01"].state()
    assert state["spawned"] == MAX_PARTS and state["skipped"] == 25


def test_geometry_lists_part_types(rig):
    geometry = rig(EMT)["EMT_01"].geometry()
    assert [p["name"] for p in geometry["parts"]] == ["tall", "short"]
    assert geometry["position"] == [0.2, 0.3, 0.0]
