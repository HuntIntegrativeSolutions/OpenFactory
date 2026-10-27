"""photo_eye: beam/part intersection and 20 ms debounce on both edges."""

from __future__ import annotations

import pytest

PE = {
    "id": "PE_01",
    "type": "photo_eye",
    "description": "Infeed Box Present Photo Eye",
    "position": [1.5, 0.15, 0.0],
    "beam_length": 0.6,
    "io": {"detect": "PE_01_DI"},
}
BOX = (0.3, 0.2, 0.3)


@pytest.fixture
def pe(rig):
    return rig(PE)


def put_part(r, x, y=0.1, z=0.0, size=BOX):
    return r.world.spawn("short", size, (x, y, z))


def test_clear_beam_reads_false(pe):
    put_part(pe, x=0.5)  # upstream of the beam
    pe.step(5)
    assert pe.io.get("PE_01_DI") is False
    assert pe["PE_01"].state() == {"detect": False, "blocked": False, "beam_offset_m": 0.0}


def test_detect_after_20_ms_debounce_at_50_hz(pe):
    put_part(pe, x=1.5)
    pe.step(1)  # first step sees the block: raw blocked, not yet reported
    assert pe["PE_01"].blocked is True
    assert pe.io.get("PE_01_DI") is False
    pe.step(1)  # 20 ms later
    assert pe.io.get("PE_01_DI") is True


def test_release_is_debounced_too(pe):
    part = put_part(pe, x=1.5)
    pe.step(2)
    assert pe.io.get("PE_01_DI") is True
    pe.world.remove(part.id)
    pe.step(1)
    assert pe.io.get("PE_01_DI") is True
    pe.step(1)
    assert pe.io.get("PE_01_DI") is False


def test_short_blip_is_filtered(pe):
    part = put_part(pe, x=1.5)
    pe.step(1)  # seen for one step only (0 ms persistence)
    pe.world.remove(part.id)
    pe.step(5)
    assert pe.io.get("PE_01_DI") is False


def test_debounce_with_smaller_step(pe):
    put_part(pe, x=1.5)
    pe.step(2, dt=0.01)  # first observation, then 10 ms
    assert pe.io.get("PE_01_DI") is False
    pe.step(1, dt=0.01)  # 20 ms after first observation
    assert pe.io.get("PE_01_DI") is True


@pytest.mark.parametrize(
    ("x", "y", "z", "expected"),
    [
        (1.5 + 0.149, 0.1, 0.0, True),  # box edge just reaches the beam
        (1.5 + 0.151, 0.1, 0.0, False),  # box edge just past the beam
        (1.5, 0.3, 0.0, False),  # box above the beam (beam at y=0.15, box spans 0.2-0.4)
        (1.5, 0.1, 0.4, True),  # off center but within the 0.6 m beam
        (1.5, 0.1, 0.5, False),  # beyond the beam end (beam spans z ±0.3, box ±0.15)
    ],
)
def test_beam_geometry(pe, x, y, z, expected):
    put_part(pe, x=x, y=y, z=z)
    pe.step(2)
    assert pe.io.get("PE_01_DI") is expected


def test_rotated_beam_runs_along_world_x(rig):
    r = rig({**PE, "rotation_deg": 90})  # beam now spans world x 1.2-1.8 at z=0
    put_part(r, x=1.75, z=0.0, size=(0.05, 0.2, 0.05))
    r.step(2)
    assert r.io.get("PE_01_DI") is True


def test_geometry(pe):
    assert pe["PE_01"].geometry()["beam_length"] == 0.6
