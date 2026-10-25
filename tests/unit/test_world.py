"""World: part registry with the 200-part cap and deterministic per-component randomness."""

from __future__ import annotations

from openfactory.core.world import MAX_PARTS, World


def test_rng_is_deterministic_per_seed_and_component():
    a = [World(seed=42).rng("EMT_01").random() for _ in range(2)]
    assert a[0] == a[1]
    assert World(seed=42).rng("EMT_01").random() != World(seed=42).rng("EMT_02").random()
    assert World(seed=42).rng("EMT_01").random() != World(seed=7).rng("EMT_01").random()


def test_spawn_ids_cap_and_remove_counts():
    world = World()
    first = world.spawn("tall", (0.3, 0.4, 0.3), (0.0, 0.2, 0.0), source="EMT_01")
    assert first is not None and first.id == "P000001" and first.source == "EMT_01"
    for _ in range(MAX_PARTS - 1):
        assert world.spawn("short", (0.3, 0.2, 0.3), (0.0, 0.1, 0.0)) is not None
    assert len(world.parts) == MAX_PARTS
    assert world.spawn("short", (0.3, 0.2, 0.3), (0.0, 0.1, 0.0)) is None
    world.remove(first.id)
    assert world.removed["tall"] == 1
    assert world.spawn("tall", (0.3, 0.4, 0.3), (0.0, 0.2, 0.0)).id == f"P{MAX_PARTS + 1:06d}"


def test_part_aabb():
    part = World().spawn("box", (0.2, 0.4, 0.6), (1.0, 0.2, 0.0))
    assert part.aabb() == ((0.9, 0.0, -0.3), (1.1, 0.4, 0.3))
