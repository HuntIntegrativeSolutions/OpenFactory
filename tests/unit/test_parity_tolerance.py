"""Housekeeping 4: parity tolerance is max(2 ticks, 25 % of the computed ramp time)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("parity_trace", REPO / "tools" / "parity_trace.py")
parity_trace = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parity_trace)


@pytest.mark.parametrize(
    ("mu", "ramp", "tolerance"),
    [
        (0.8, 3.1855, 2.0),  # 0.5 / 7.848 / 0.02; 25 % = 0.80 -> the 2-tick floor
        (0.3, 8.4947, 2.1237),  # 25 % = 2.12: a 2-tick delta passes, 3 fails
        (0.1, 25.4842, 6.3710),  # roller-conveyor territory (Milestone 2)
    ],
)
def test_tolerance_formula(mu, ramp, tolerance):
    got_ramp = parity_trace.ramp_ticks(0.5, mu, 0.02)
    assert got_ramp == pytest.approx(ramp, abs=1e-4)
    got = parity_trace.tolerance_ticks(got_ramp)
    assert got == pytest.approx(tolerance, abs=1e-4)
    print(f"mu {mu}: ramp {got_ramp:.4f} ticks -> tolerance {got:.4f} ticks")


def test_tolerance_is_not_rounded_up():
    assert parity_trace.tolerance_ticks(8.4947) < 3  # rounding up would loosen μ 0.3 to 3 ticks


def test_traces_carry_their_tolerance():
    data = parity_trace.trace(seconds=0.1)
    assert data["ramp_ticks"] == pytest.approx(parity_trace.ramp_ticks(0.5, 0.8, 0.02), abs=1e-6)
    assert data["tolerance_ticks"] == 2.0
