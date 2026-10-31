"""SIM_HEARTBEAT_DI (ruled 2026-10-30, PLAN §6.1, ADR-0004 decision 10): a 1 Hz square wave in
simulated time on every scene, allocated after the scene's DIs, appended to an existing lockfile at
the next free discrete input without renumbering anything, and reserved."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from openfactory.core.heartbeat import HEARTBEAT_TAG, heartbeat_value
from openfactory.core.loader import build_iotable, load
from openfactory.core.schema import load_scene
from openfactory.protocols.modbus import (
    AddressLock,
    allocate,
    lock_text,
    lockfile_path,
    read_lock,
    scene_register_map,
)

REPO = Path(__file__).resolve().parents[2]
SORTER = REPO / "scenes" / "sorting-by-height.yaml"


def test_square_wave_changes_every_half_second_of_simulated_time():
    values = [heartbeat_value(n, 0.02) for n in range(101)]
    edges = [n for n in range(1, 101) if values[n] != values[n - 1]]
    assert values[0] is False and edges == [25, 50, 75, 100]
    assert [heartbeat_value(n, 0.1) for n in (4, 5, 9, 10)] == [False, True, True, False]


def test_every_tick_writes_it_and_nothing_reads_the_clock():
    loaded = load(SORTER)
    assert loaded.io.get(HEARTBEAT_TAG) is False
    tag = loaded.io.tag(HEARTBEAT_TAG)
    assert (tag.kind, tag.dtype, tag.owner) == ("DI", "bool", "")
    seen = []
    for _ in range(100):
        loaded.step(0.02)
        seen.append(loaded.io.get(HEARTBEAT_TAG))
    assert seen[23:26] == [False, True, True] and seen[48:51] == [True, False, False]
    changes = sum(1 for a, b in zip([False, *seen[:-1]], seen, strict=True) if a != b)
    assert changes == 4  # after ticks 25, 50, 75 and 100


def test_fresh_allocation_puts_it_after_the_scene_dis():
    tags = build_iotable(load_scene(SORTER)).tags()
    assert tags[-1].name == HEARTBEAT_TAG  # last in the table, after every bound port
    dis = [a for a in allocate(tags, "float32").assignments if a.kind == "DI"]
    assert dis[-1].tag == HEARTBEAT_TAG and dis[-1].address == len(dis) - 1


def test_a_lockfile_from_before_it_gains_one_line_at_the_next_free_address(tmp_path):
    scene = tmp_path / SORTER.name
    scene.write_text(SORTER.read_text(encoding="utf-8"), encoding="utf-8")
    current = read_lock(lockfile_path(SORTER, "float32"))
    assert current is not None and current.addresses[HEARTBEAT_TAG] == 5
    # The lockfile as it was before the heartbeat, plus a retired DI at 6 to show "next free"
    # means after the highest address in use, retired ones included.
    old = {k: v for k, v in current.addresses.items() if k != HEARTBEAT_TAG}
    old["PE_09_DI"] = 6
    lock = lockfile_path(scene, "float32")
    before = allocate(
        [t for t in build_iotable(load_scene(scene)).tags() if t.name != HEARTBEAT_TAG],
        "float32",
        lock=AddressLock("float32", old),
    )
    lock.write_text(lock_text(before, scene.name), encoding="utf-8")
    old_text = lock.read_text(encoding="utf-8")

    after = scene_register_map(load_scene(scene), "float32", scene_path=scene)
    new_text = lock.read_text(encoding="utf-8")
    added = [ln for ln in new_text.splitlines() if ln not in old_text.splitlines()]
    removed = [ln for ln in old_text.splitlines() if ln not in new_text.splitlines()]
    assert added == ["  SIM_HEARTBEAT_DI: 7"] and removed == []
    assert {a.tag: a.address for a in after.assignments if a.tag in old} == {
        k: v for k, v in old.items() if k != "PE_09_DI"
    }


def test_the_name_is_reserved(tmp_path):
    data = yaml.safe_load(SORTER.read_text(encoding="utf-8"))
    pe = next(c for c in data["components"] if c["id"] == "PE_01")
    pe["io"]["detect"] = HEARTBEAT_TAG
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ValidationError, match="reserved for the simulator's own heartbeat"):
        load_scene(bad)
