"""PLAN 1.2: I/O table set/get, change callbacks, thread safety, kind-based write restriction."""

from __future__ import annotations

import threading

import pytest

from openfactory.core.iotable import IOTable, Tag, TagWriteError, Writer


@pytest.fixture
def table() -> IOTable:
    return IOTable(
        [
            Tag.new("PE_01_DI", "DI", description="Infeed photo eye", owner="PE_01"),
            Tag.new("CONV_01_RUN_DO", "DO", owner="CONV_01"),
            Tag.new("LT_01_AI", "AI", range=(0.0, 2.0), units="m"),
            Tag.new("CONV_01_SPD_AO", "AO", range=(0.0, 100.0), units="%"),
            Tag.new("CNT_01_AI", "AI", dtype="int16"),
        ]
    )


# --- set / get ---------------------------------------------------------------------------------


def test_initial_values_and_metadata(table):
    assert table.get("PE_01_DI") is False
    assert table.get("LT_01_AI") == 0.0
    assert table.get("CNT_01_AI") == 0
    tag = table.tag("CONV_01_SPD_AO")
    assert (tag.kind, tag.dtype, tag.range, tag.units) == ("AO", "float32", (0.0, 100.0), "%")
    assert table.tag("PE_01_DI").owner == "PE_01"
    assert len(table) == 5 and "PE_01_DI" in table and "NOPE_DI" not in table


def test_set_get_roundtrip(table):
    assert table.set("PE_01_DI", True, Writer.SIM) is True
    assert table.set("CONV_01_SPD_AO", 75, Writer.PLC) is True
    assert table.get("PE_01_DI") is True
    assert table.get("CONV_01_SPD_AO") == 75.0
    assert isinstance(table.get("CONV_01_SPD_AO"), float)
    assert table.values()["PE_01_DI"] is True


def test_same_value_write_reports_no_change(table):
    table.set("PE_01_DI", True, Writer.SIM)
    assert table.set("PE_01_DI", True, Writer.SIM) is False


def test_coercion_and_validation(table):
    table.set("CONV_01_RUN_DO", 1, Writer.PLC)  # Modbus-style 0/1
    assert table.get("CONV_01_RUN_DO") is True
    with pytest.raises(TypeError):
        table.set("CONV_01_RUN_DO", 2, Writer.PLC)
    with pytest.raises(TypeError):
        table.set("LT_01_AI", "1.5", Writer.SIM)
    with pytest.raises(ValueError, match="NaN"):
        table.set("LT_01_AI", float("nan"), Writer.SIM)
    with pytest.raises(ValueError, match="int16"):
        table.set("CNT_01_AI", 40000, Writer.SIM)


def test_unknown_and_duplicate_tags(table):
    with pytest.raises(KeyError):
        table.set("NOPE_DI", True, Writer.SIM)
    with pytest.raises(KeyError, match="duplicate"):
        table.add(Tag.new("PE_01_DI", "DI"))


# --- kind-based write restriction --------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "value", "writer"),
    [
        ("PE_01_DI", True, Writer.PLC),  # PLC cannot write a DI
        ("LT_01_AI", 1.0, Writer.PLC),  # PLC cannot write an AI
        ("CONV_01_RUN_DO", True, Writer.SIM),  # simulation cannot fake a PLC command
        ("CONV_01_SPD_AO", 50.0, Writer.SIM),
    ],
)
def test_wrong_side_cannot_write(table, name, value, writer):
    before = table.get(name)
    with pytest.raises(TagWriteError):
        table.set(name, value, writer)
    assert table.get(name) == before


def test_set_many_is_all_or_nothing(table):
    with pytest.raises(TagWriteError):
        table.set_many({"CONV_01_RUN_DO": True, "PE_01_DI": True}, Writer.PLC)
    assert table.get("CONV_01_RUN_DO") is False
    changed = table.set_many({"CONV_01_RUN_DO": True, "CONV_01_SPD_AO": 40}, Writer.PLC)
    assert changed == ["CONV_01_RUN_DO", "CONV_01_SPD_AO"]


# --- callbacks ---------------------------------------------------------------------------------


def test_callback_fires_once_per_change_with_details(table):
    seen = []
    table.on_change(lambda *args: seen.append(args))
    table.set("PE_01_DI", True, Writer.SIM)
    table.set("PE_01_DI", True, Writer.SIM)  # no change, no callback
    table.set("CONV_01_SPD_AO", 25.0, Writer.PLC)
    assert seen == [
        ("PE_01_DI", False, True, Writer.SIM),
        ("CONV_01_SPD_AO", 0.0, 25.0, Writer.PLC),
    ]


def test_unsubscribe(table):
    seen = []
    unsubscribe = table.on_change(lambda *args: seen.append(args))
    unsubscribe()
    unsubscribe()  # idempotent
    table.set("PE_01_DI", True, Writer.SIM)
    assert seen == []


def test_callback_may_write_the_table_without_deadlock(table):
    # e.g. a component reacting to a PLC command by updating its feedback DI
    def mirror(name, _old, new, _writer):
        if name == "CONV_01_RUN_DO":
            table.set("PE_01_DI", new, Writer.SIM)

    table.on_change(mirror)
    table.set("CONV_01_RUN_DO", True, Writer.PLC)
    assert table.get("PE_01_DI") is True


def test_failing_callback_does_not_stop_others(table, caplog):
    seen = []

    def broken(*_args):
        raise RuntimeError("subscriber bug")

    table.on_change(broken)
    table.on_change(lambda *args: seen.append(args[0]))
    assert table.set("PE_01_DI", True, Writer.SIM) is True
    assert seen == ["PE_01_DI"]
    assert "callback failed" in caplog.text


# --- thread safety -----------------------------------------------------------------------------


def test_concurrent_writers_readers_and_callbacks():
    n_threads, n_writes = 8, 2000
    table = IOTable([Tag.new(f"T{i}_AI", "AI") for i in range(n_threads)])
    callback_count = 0
    count_lock = threading.Lock()

    def count(*_args):
        nonlocal callback_count
        with count_lock:
            callback_count += 1

    table.on_change(count)
    stop = threading.Event()
    reader_errors: list[Exception] = []

    def reader():
        while not stop.is_set():
            try:
                snapshot = table.values()
                assert len(snapshot) == n_threads
            except Exception as exc:  # pragma: no cover - only on failure
                reader_errors.append(exc)

    def writer(i):
        name = f"T{i}_AI"
        for v in range(1, n_writes + 1):
            table.set(name, float(v), Writer.SIM)

    readers = [threading.Thread(target=reader) for _ in range(2)]
    writers = [threading.Thread(target=writer, args=(i,)) for i in range(n_threads)]
    for t in readers + writers:
        t.start()
    for t in writers:
        t.join()
    stop.set()
    for t in readers:
        t.join()

    assert reader_errors == []
    assert callback_count == n_threads * n_writes
    assert all(table.get(f"T{i}_AI") == float(n_writes) for i in range(n_threads))
