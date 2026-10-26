"""Housekeeping 5: pairing for ``tools/parity_trace.py --live`` (informational mode)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("parity_trace", REPO / "tools" / "parity_trace.py")
parity_trace = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parity_trace)

CORE = [
    {"id": "PE_01", "blocked": True, "tick": 273},
    {"id": "PE_01", "blocked": False, "tick": 304},
    {"id": "PE_01", "blocked": True, "tick": 423},
]


def ev(value, tick, arrived):
    return {"type": "sensor", "id": "PE_01", "value": value, "tick": tick, "core_tick": arrived}


def test_pairs_nearest_core_edge_of_same_eye_and_direction():
    rows = parity_trace.pair_edges(CORE, [ev(True, 275, 277), ev(False, 303, 305)])
    assert [(r["core"], r["browser"], r["arrived"]) for r in rows] == [
        (273, 275, 277),
        (304, 303, 305),
    ]


def test_unmatched_browser_edge_and_window():
    rows = parity_trace.pair_edges(CORE, [ev(True, 350, 351), ev(True, 600, 601)], window=50)
    assert rows[0]["core"] is None  # 273 and 423 are both more than 50 ticks away
    assert rows[1]["core"] is None


def test_each_core_edge_pairs_once():
    rows = parity_trace.pair_edges(CORE, [ev(True, 274, 275), ev(True, 276, 277)])
    assert rows[0]["core"] == 273 and rows[1]["core"] is None


def test_live_forces_run_belts_and_emitters_only():
    forces = parity_trace.live_forces(REPO / "scenes" / "sorting-by-height.yaml")
    assert forces == ["CONV_01_RUN_DO=1", "CONV_02_RUN_DO=1", "EMT_01_EN_DO=1"]
