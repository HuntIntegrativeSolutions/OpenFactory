"""PLAN 1.14: the OpenPLC example program agrees with the scene's int16_scaled register map."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

from openfactory.core.schema import load_scene
from openfactory.export.io_list_csv import io_list_csv
from openfactory.protocols.modbus import lockfile_path, read_lock, scene_register_map

REPO = Path(__file__).resolve().parents[2]
SCENE = REPO / "scenes" / "sorting-by-height.yaml"
PROGRAM = REPO / "examples" / "openplc" / "sorting-by-height.st"
#: OpenPLC places Slave Device points from address 100 onward (its Slave Devices page).
OPENPLC_BASE = 100
LOCATION = re.compile(
    r"^\s*([A-Z][A-Z0-9_]*_(?:DI|DO|AI|AO))\s+AT\s+%(IX|QX|IW|QW)(\d+)(?:\.(\d+))?"
)

spec = importlib.util.spec_from_file_location(
    "openplc_configure", REPO / "tools" / "openplc_configure.py"
)
openplc_configure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(openplc_configure)


def located_variables() -> dict[str, str]:
    """``{tag: Modbus label}`` from the program's ``NAME AT %IX100.4`` declarations."""
    out = {}
    for line in PROGRAM.read_text(encoding="utf-8").splitlines():
        m = LOCATION.match(line)
        if not m:
            continue
        name, area, word, bit = m.groups()
        if area in ("IX", "QX"):
            assert int(word) == OPENPLC_BASE, line
            out[name] = f"{'DI' if area == 'IX' else 'CO'}:{int(bit)}"
        else:
            out[name] = f"{'IR' if area == 'IW' else 'HR'}:{int(word) - OPENPLC_BASE}"
    return out


def test_program_locations_match_the_int16_scaled_lockfile():
    register_map = scene_register_map(
        load_scene(SCENE), "int16_scaled", scene_path=SCENE, write=False
    )
    expected = {a.tag: a.label for a in register_map.assignments}
    assert located_variables() == expected  # every scene tag, at its locked address
    lock = read_lock(lockfile_path(SCENE, "int16_scaled"))
    assert lock is not None and lock.encoding == "int16_scaled"


def test_slave_device_ranges_from_the_exported_csv(tmp_path):
    csv_path = tmp_path / "io.csv"
    csv_path.write_text(
        io_list_csv(load_scene(SCENE), "int16_scaled", scene_path=SCENE, write_lock=False),
        encoding="utf-8",
    )
    assert openplc_configure.table_ranges(csv_path) == {
        "DI": (0, 5),
        "CO": (0, 4),
        "IR": (0, 0),
        "HR": (0, 2),
    }


def test_program_writes_full_speed_and_has_the_configuration_block():
    text = PROGRAM.read_text(encoding="utf-8")
    assert "SPEED_RAW : INT := 32767" in text  # 100 % in int16_scaled
    assert "CONV_01_SPD_AO := SPEED_RAW;" in text and "CONV_02_SPD_AO := SPEED_RAW;" in text
    assert "PROGRAM instance0 WITH task0 : sorter;" in text
