"""Register allocation, address lockfile, and analog encoding (PLAN §6.1, ADR-0003/0004)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from openfactory.core.iotable import Tag
from openfactory.core.loader import build, build_iotable, load
from openfactory.core.schema import load_scene
from openfactory.protocols.modbus import (
    AddressError,
    AddressLock,
    allocate,
    decode,
    encode,
    legacy_lockfile_path,
    lock_text,
    lockfile_path,
    read_lock,
    scene_register_map,
)

EXAMPLE = Path(__file__).resolve().parents[2] / "scenes" / "sorting-by-height.yaml"


def test_allocation_sorted_by_kind_then_name_and_packed():
    tags = [
        Tag.new("B_AO", "AO"),
        Tag.new("Z_DI", "DI"),
        Tag.new("A_AO", "AO"),
        Tag.new("A_DI", "DI"),
        Tag.new("M_DO", "DO"),
        Tag.new("K_AI", "AI"),
    ]
    m = allocate(tags, "float32")
    assert [a.label for a in m.assignments] == [
        "DI:0",
        "DI:1",
        "CO:0",
        "IR:0-1",
        "HR:0-1",
        "HR:2-3",
    ]
    assert [a.tag for a in m.assignments] == ["A_DI", "Z_DI", "M_DO", "K_AI", "A_AO", "B_AO"]
    assert m.size == {"di": 2, "co": 1, "ir": 2, "hr": 4}
    m16 = allocate(tags, "int16_scaled")
    assert [a.label for a in m16.assignments][-2:] == ["HR:0", "HR:1"]


def test_allocation_is_stable_across_runs_and_input_order():
    a = allocate(load(EXAMPLE).io.tags(), "float32").assignments
    b = allocate(reversed(load(EXAMPLE).io.tags()), "float32").assignments
    assert a == b


def test_describe_lists_every_tag():
    text = allocate(load(EXAMPLE).io.tags(), "int16_scaled").describe()
    assert text.splitlines()[0] == "Modbus register map (int16_scaled, zero-based addresses):"
    assert "HR:0       CONV_01_SPD_AO" in text


@pytest.mark.parametrize("value", [0.0, 12.5, -3.75, 1e6])
def test_float32_round_trip(value):
    tag = Tag.new("X_AO", "AO", range=(0.0, 100.0))
    assert decode(tag, encode(tag, value, "float32"), "float32") == pytest.approx(value)


def test_int16_scaled_scaling_and_clamping():
    tag = Tag.new("LT_01_AI", "AI", range=(-1.0, 3.0))
    assert encode(tag, -1.0, "int16_scaled") == [0]
    assert encode(tag, 3.0, "int16_scaled") == [32767]
    assert encode(tag, 1.0, "int16_scaled") == [round(32767 / 2)]
    assert encode(tag, 9.0, "int16_scaled") == [32767]
    assert encode(tag, -9.0, "int16_scaled") == [0]
    assert decode(tag, [32767], "int16_scaled") == pytest.approx(3.0)
    assert decode(tag, [65535], "int16_scaled") == pytest.approx(3.0)  # unsigned over range
    assert decode(tag, [0], "int16_scaled") == pytest.approx(-1.0)


def test_non_finite_float_rejected():
    tag = Tag.new("X_AO", "AO")
    with pytest.raises(ValueError, match="non-finite"):
        decode(tag, [0x7F80, 0x0000], "float32")  # +inf


def test_hook_never_raises():
    import asyncio

    from pymodbus.constants import ExcCodes

    from openfactory.core.config import Endpoint
    from openfactory.protocols.modbus import ModbusServer

    server = ModbusServer(load(EXAMPLE).io, "float32", Endpoint(port=1502))
    # FC23 (read/write multiple registers) is routed to the hook by pymodbus but not supported here
    assert asyncio.run(server._hook(23, 0, 0, 1, [0, 0], None)) == ExcCodes.ILLEGAL_FUNCTION
    # an internal failure is reported to the client as a device failure, not raised
    assert asyncio.run(server._hook(3, 0, 0, 1, None, None)) == ExcCodes.DEVICE_FAILURE


# --- address lockfile and pins (ADR-0004) ---------------------------------------------------------

PE_00 = {
    "id": "PE_00",
    "type": "photo_eye",
    "description": "Infeed Entry Photo Eye",
    "position": [0.6, 0.15, 0.0],
    "beam_length": 0.6,
    "io": {"detect": "PE_00_DI"},
}


#: The Milestone 1 five-component sorter, so these tests do not move when the example grows.
BASE_IDS = ("CONV_01", "PE_01", "CYL_01", "EMT_01", "REM_01")


def scene_copy(tmp_path, extra_components=(), edit=None):
    """A copy of the example scene in tmp_path (tests never write next to the shipped scene)."""
    data = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    data["components"] = [c for c in data["components"] if c["id"] in BASE_IDS]
    data.pop("checks", None)  # the sorter's checks name removers this reduced copy leaves out
    data["components"] += list(extra_components)
    if edit:
        edit(data)
    path = tmp_path / "sorter.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def labels(register_map):
    return {a.tag: a.label for a in register_map.assignments}


def test_first_run_writes_lockfile_with_the_packed_allocation(tmp_path):
    path = scene_copy(tmp_path)
    m = scene_register_map(load_scene(path), scene_path=path)
    lock = lockfile_path(path, "float32")
    assert lock.name == "sorter.float32.modbus.yaml" and lock.is_file()
    assert labels(m) == labels(allocate(build_iotable(load_scene(path)).tags(), "float32"))
    assert read_lock(lock) == AddressLock("float32", {a.tag: a.address for a in m.assignments})


def test_adding_a_tag_does_not_renumber_existing_ones(tmp_path):
    path = scene_copy(tmp_path)
    before = labels(scene_register_map(load_scene(path), scene_path=path))
    assert before["PE_01_DI"] == "DI:3"

    # PE_00_DI sorts before PE_01_DI: a fresh allocation would move PE_01_DI to DI:4
    path = scene_copy(tmp_path, [PE_00])
    fresh = labels(allocate(build_iotable(load_scene(path)).tags(), "float32"))
    assert fresh["PE_00_DI"] == "DI:3" and fresh["PE_01_DI"] == "DI:4"

    after = labels(scene_register_map(load_scene(path), scene_path=path))
    assert {tag: after[tag] for tag in before} == before  # nothing renumbered
    # appended after the highest used address (DI:4 is SIM_HEARTBEAT_DI, after the scene's DIs)
    assert after["PE_00_DI"] == "DI:5" and after["SIM_HEARTBEAT_DI"] == "DI:4"
    assert "  PE_00_DI: 5\n" in lockfile_path(path, "float32").read_text(encoding="utf-8")
    print(f"before {before}\nafter  {after}")


def test_new_analog_tag_appends_after_highest_register(tmp_path):
    path = scene_copy(tmp_path)
    scene_register_map(load_scene(path), scene_path=path)
    conv0 = {
        "id": "CONV_00",
        "type": "belt_conveyor",
        "description": "Pre-Infeed Belt Conveyor",
        "position": [-2.0, 0.0, 0.0],
        "length": 2.0,
        "width": 0.5,
        "speed_mps": 0.5,
        "io": {"run": "CONV_00_RUN_DO", "speed": "CONV_00_SPD_AO"},
    }
    path = scene_copy(tmp_path, [conv0])
    after = labels(scene_register_map(load_scene(path), scene_path=path))
    assert after["CONV_01_SPD_AO"] == "HR:0-1" and after["CONV_00_SPD_AO"] == "HR:2-3"
    assert after["CONV_01_RUN_DO"] == "CO:0" and after["CONV_00_RUN_DO"] == "CO:3"


def test_lockfile_is_rewritten_only_when_it_changes(tmp_path):
    path = scene_copy(tmp_path)
    scene_register_map(load_scene(path), scene_path=path)
    lock = lockfile_path(path, "float32")
    text, mtime = lock.read_text(encoding="utf-8"), lock.stat().st_mtime_ns
    for _ in range(2):
        scene_register_map(load_scene(path), scene_path=path)
    assert lock.read_text(encoding="utf-8") == text and lock.stat().st_mtime_ns == mtime


def test_removed_tag_keeps_its_address_reserved_and_gets_it_back(tmp_path):
    path = scene_copy(tmp_path)
    scene_register_map(load_scene(path), scene_path=path)

    def drop_pe(data):
        data["components"] = [c for c in data["components"] if c["id"] != "PE_01"]

    path = scene_copy(tmp_path, [PE_00], edit=drop_pe)
    m = scene_register_map(load_scene(path), scene_path=path)
    assert labels(m)["PE_00_DI"] == "DI:5"  # DI:3 stays reserved for the removed PE_01_DI
    assert m.retired == {"PE_01_DI": 3}
    assert "  PE_01_DI: 3  # not in scene\n" in lockfile_path(path, "float32").read_text(
        encoding="utf-8"
    )

    path = scene_copy(tmp_path, [PE_00])  # PE_01 comes back
    assert labels(scene_register_map(load_scene(path), scene_path=path))["PE_01_DI"] == "DI:3"


def test_pinned_address_overrides_allocation_and_is_locked(tmp_path):
    def pin(data):
        conv = data["components"][0]
        conv["io"]["run"] = {"tag": "CONV_01_RUN_DO", "address": 10}

    path = scene_copy(tmp_path, edit=pin)
    scene = load_scene(path)
    assert {b.name: b.address for b in scene.tag_bindings()}["CONV_01_RUN_DO"] == 10
    m = scene_register_map(scene, scene_path=path)
    assert labels(m)["CONV_01_RUN_DO"] == "CO:10"
    # allocation never fills holes: the others append after the highest used address
    assert labels(m)["CYL_01_EXT_DO"] == "CO:11" and labels(m)["EMT_01_EN_DO"] == "CO:12"
    assert read_lock(lockfile_path(path, "float32")).addresses["CONV_01_RUN_DO"] == 10
    assert build(scene).component("CONV_01").bindings["run"] == "CONV_01_RUN_DO"


def test_pin_overrides_an_existing_lockfile_entry(tmp_path):
    path = scene_copy(tmp_path)
    scene_register_map(load_scene(path), scene_path=path)

    def pin(data):
        data["components"][0]["io"]["run"] = {"tag": "CONV_01_RUN_DO", "address": 7}

    path = scene_copy(tmp_path, edit=pin)
    after = labels(scene_register_map(load_scene(path), scene_path=path))
    assert after["CONV_01_RUN_DO"] == "CO:7" and after["CYL_01_EXT_DO"] == "CO:1"


@pytest.mark.parametrize(
    ("pins", "lock", "message"),
    [
        ({"CONV_01_RUN_DO": 1, "CYL_01_EXT_DO": 1}, None, "both use CO:1"),
        ({"CONV_01_RUN_DO": 1}, {"CYL_01_EXT_DO": 1}, "both use CO:1"),
        ({"CONV_01_SPD_AO": 65535}, None, "outside 0-65535"),  # float32 needs two registers
        ({"CONV_01_SPD_AO": 4}, {"CONV_01_SPD_AO_X": 5}, "no _DI/_DO/_AI/_AO suffix"),
        ({"NOPE_DO": 0}, None, "unknown tag"),
    ],
)
def test_conflicting_addresses_are_rejected(pins, lock, message):
    tags = load(EXAMPLE).io.tags()
    with pytest.raises(AddressError, match=message):
        allocate(tags, "float32", pins=pins, lock=AddressLock("float32", lock or {}))


def test_pinned_float32_pair_cannot_straddle_another_tag():
    tags = [Tag.new("A_AO", "AO"), Tag.new("B_AO", "AO")]
    with pytest.raises(AddressError, match="both use HR:3"):
        allocate(tags, "float32", pins={"A_AO": 2, "B_AO": 3})
    m = allocate(tags, "int16_scaled", pins={"A_AO": 2, "B_AO": 3})
    assert [a.label for a in m.assignments] == ["HR:2", "HR:3"]


def test_float32_and_int16_scaled_lockfiles_coexist(tmp_path):
    """ADR-0004 amendment: one lockfile per encoding; neither run touches the other's file."""
    path = scene_copy(tmp_path)
    f32 = labels(scene_register_map(load_scene(path), "float32", scene_path=path))
    f32_lock = lockfile_path(path, "float32")
    f32_text, f32_mtime = f32_lock.read_text(encoding="utf-8"), f32_lock.stat().st_mtime_ns

    i16 = labels(scene_register_map(load_scene(path), "int16_scaled", scene_path=path))
    i16_lock = lockfile_path(path, "int16_scaled")
    assert i16_lock.name == "sorter.int16_scaled.modbus.yaml" and i16_lock.is_file()
    assert f32_lock.read_text(encoding="utf-8") == f32_text  # untouched
    assert f32_lock.stat().st_mtime_ns == f32_mtime
    assert f32["CONV_01_SPD_AO"] == "HR:0-1" and i16["CONV_01_SPD_AO"] == "HR:0"
    assert read_lock(i16_lock).encoding == "int16_scaled"

    # Each encoding grows on its own: add a tag, run float32 only, then int16_scaled.
    i16_text = i16_lock.read_text(encoding="utf-8")
    path = scene_copy(tmp_path, [PE_00])
    f32_after = labels(scene_register_map(load_scene(path), "float32", scene_path=path))
    assert i16_lock.read_text(encoding="utf-8") == i16_text  # float32 run left it alone
    i16_after = labels(scene_register_map(load_scene(path), "int16_scaled", scene_path=path))
    assert {t: f32_after[t] for t in f32} == f32 and {t: i16_after[t] for t in i16} == i16
    assert f32_after["PE_00_DI"] == i16_after["PE_00_DI"] == "DI:5"  # DI:4 is the heartbeat
    print(f"float32      {f32_after}\nint16_scaled {i16_after}")


def test_hand_renamed_lockfile_is_an_error(tmp_path):
    path = scene_copy(tmp_path)
    scene_register_map(load_scene(path), "float32", scene_path=path)
    lockfile_path(path, "float32").rename(lockfile_path(path, "int16_scaled"))
    with pytest.raises(AddressError, match="written for float32 but this map is int16_scaled"):
        scene_register_map(load_scene(path), "int16_scaled", scene_path=path)
    tags = load(EXAMPLE).io.tags()
    with pytest.raises(AddressError, match="written for float32"):
        allocate(tags, "int16_scaled", lock=AddressLock("float32", {}))


def test_pre_amendment_lockfile_name_is_an_error_naming_the_rename(tmp_path):
    path = scene_copy(tmp_path)
    scene_register_map(load_scene(path), scene_path=path, write=True)
    lockfile_path(path, "float32").rename(legacy_lockfile_path(path))
    with pytest.raises(AddressError, match=r"git mv .*sorter\.modbus\.yaml .*sorter\.float32"):
        scene_register_map(load_scene(path), scene_path=path)
    assert not lockfile_path(path, "float32").exists()  # nothing re-allocated behind its back


def test_malformed_lockfile_is_an_error(tmp_path):
    lock = tmp_path / "x.modbus.yaml"
    lock.write_text("encoding: float32\ncoils:\n  PE_01_DI: 0\n", encoding="utf-8")
    with pytest.raises(AddressError, match="listed under coils"):
        read_lock(lock)
    lock.write_text("coils: {}\n", encoding="utf-8")
    with pytest.raises(AddressError, match="no valid 'encoding'"):
        read_lock(lock)
    assert read_lock(tmp_path / "missing.modbus.yaml") is None


def test_shipped_lockfile_matches_the_shipped_scene():
    m = scene_register_map(load_scene(EXAMPLE), scene_path=EXAMPLE, write=False)
    assert lockfile_path(EXAMPLE, "float32").read_text(encoding="utf-8") == lock_text(
        m, EXAMPLE.name
    )


def test_new_encoding_lockfile_copies_digital_tables_from_the_other(tmp_path):
    """ADR-0004 decision 8: digital addresses are identical across encodings."""
    path = scene_copy(tmp_path)
    scene_register_map(load_scene(path), "float32", scene_path=path)

    def drop_cyl_ret(data):  # retire CYL_01_RET_DI (DI:2) in the float32 lockfile
        cyl = next(c for c in data["components"] if c["id"] == "CYL_01")
        del cyl["io"]["retracted"]

    path = scene_copy(tmp_path, [PE_00], edit=drop_cyl_ret)
    f32 = labels(scene_register_map(load_scene(path), "float32", scene_path=path))
    assert f32["PE_01_DI"] == "DI:3" and f32["PE_00_DI"] == "DI:5"  # appended, not sorted
    fresh = labels(allocate(build_iotable(load_scene(path)).tags(), "int16_scaled"))
    assert fresh["PE_00_DI"] != "DI:5"  # without the copy the maps would differ

    i16 = labels(scene_register_map(load_scene(path), "int16_scaled", scene_path=path))
    digital = {t: a for t, a in f32.items() if t.endswith(("_DI", "_DO"))}
    assert {t: i16[t] for t in digital} == digital
    assert i16["CONV_01_SPD_AO"] == "HR:0" and f32["CONV_01_SPD_AO"] == "HR:0-1"
    i16_text = lockfile_path(path, "int16_scaled").read_text(encoding="utf-8")
    assert "  CYL_01_RET_DI: 2  # not in scene\n" in i16_text  # the reservation is copied too
    print(f"float32      {f32}\nint16_scaled {i16}")


def test_existing_lockfile_is_never_reseeded(tmp_path):
    path = scene_copy(tmp_path)
    scene_register_map(load_scene(path), "int16_scaled", scene_path=path)  # created first
    i16 = lockfile_path(path, "int16_scaled")
    i16_text = i16.read_text(encoding="utf-8")
    f32 = lockfile_path(path, "float32")
    scene_register_map(load_scene(path), "float32", scene_path=path)  # seeded from int16_scaled
    f32.write_text(  # the float32 map later diverges (a test-only edit)
        f32.read_text(encoding="utf-8").replace("PE_01_DI: 3", "PE_01_DI: 7"), encoding="utf-8"
    )
    scene_register_map(load_scene(path), "int16_scaled", scene_path=path)
    assert i16.read_text(encoding="utf-8") == i16_text  # an existing lockfile is never reseeded


def test_shipped_lockfiles_share_their_digital_tables():
    f32 = read_lock(lockfile_path(EXAMPLE, "float32")).addresses
    i16 = read_lock(lockfile_path(EXAMPLE, "int16_scaled")).addresses
    digital = {t for t in f32 if t.endswith(("_DI", "_DO"))}
    assert {t: f32[t] for t in digital} == {t: i16[t] for t in digital}
    assert f32["PE_01_DI"] == i16["PE_01_DI"] == 3
