"""PLAN 4.14 item 1: status colors are exact at every quality level.

In headless Chromium (SwiftShader), at low, standard and high, the test samples the rendered
pixels on a running belt and a stopped belt (sorting-by-height, CONV_01 forced on) and on a lit
pilot light (``tests/scenes/operator-devices-only.yaml``, amber, and a copy of it with the lamp
red) and on a pressed green pushbutton (the same scene, ruled 2026-11-12), and finds each
within ``TOLERANCE`` of its hex value in the style table (``web/src/isa101.ts``).

The sample is the per-channel median of a 9 x 9 pixel patch around the projected point, so a
belt slat line or an antialiased edge inside the patch does not decide it.

Tolerance: 2 levels (of 255) per channel. A status material is unlit and not tone mapped, so
the only change between its hex value and the screen is three's colour management: the sRGB
hex is converted to linear on the CPU, written to the framebuffer through the sRGB output
transform in the shader, and quantised to 8 bits, which can move a channel by 1. One more level
covers SwiftShader's float precision. A lit or tone-mapped material misses by far more: before
4.14 the running belt rendered #24B457 against #22C55E (19 levels on green), and the reverted
spike's tone mapping gave #8BDC93. Measured on 2026-11-11: every sample, at every level,
was off by 0.
"""

from __future__ import annotations

import importlib.util
import statistics
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "tools" / "screenshot.py"
spec = importlib.util.spec_from_file_location("screenshot_tool", TOOL)
screenshot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(screenshot)

SORTER = REPO / "scenes" / "sorting-by-height.yaml"
DEVICES = REPO / "tests" / "scenes" / "operator-devices-only.yaml"

#: ISA-101 style table (web/src/isa101.ts, ~/.claude/rules ignition-conventions "Style Standards").
RUNNING = 0x22C55E
STOPPED = 0x9CA3AF
ALARM_HIGH = 0xEF4444
ALARM_MEDIUM = 0xFACC15

TOLERANCE = 2
LEVELS = ("low", "standard", "high")


@pytest.fixture(autouse=True)
def _viewer_built(prerequisite):
    prerequisite(
        (REPO / "web" / "dist" / "index.html").is_file(), "viewer not built (npm run build)"
    )


def _rgb(hex_value: int) -> tuple[int, int, int]:
    return (hex_value >> 16) & 0xFF, (hex_value >> 8) & 0xFF, hex_value & 0xFF


def _patch_median(png: Path, x: float, y: float, half: int = 4) -> tuple[int, int, int]:
    width, height, channels, pixels = screenshot.read_png(png)
    cx, cy = round(x), round(y)
    assert half <= cx < width - half and half <= cy < height - half, f"({cx}, {cy}) off screen"
    starts = [
        ((cy + dy) * width + cx + dx) * channels
        for dy in range(-half, half + 1)
        for dx in range(-half, half + 1)
    ]
    rows = [pixels[i : i + 3] for i in starts]
    return tuple(int(statistics.median(p[i] for p in rows)) for i in range(3))  # type: ignore[return-value]


def _sample(
    tmp_path: Path, scene: Path, forces: list[str], level: str, points: dict, before=None
) -> dict:
    """Run `scene` at `level`, wait for state frames, call `before(page)` if given, and sample
    each `points` entry ({name: (component id, local point)}) as an RGB triple."""
    copy = tmp_path / scene.name  # `run` writes files next to the scene: run a copy
    copy.write_text(scene.read_text(encoding="utf-8"), encoding="utf-8")
    png = tmp_path / f"{scene.stem}-{level}.png"
    with screenshot.running_viewer(copy, forces=forces, quality=level) as (page, _port, console):
        screenshot.wait_for_viewer(page, console, seconds=1.0, min_sensor_events=0, timeout=60.0)
        assert page.evaluate("() => window.__openfactory.quality") == level
        if before is not None:
            before(page)
        page.wait_for_timeout(300)  # the last state frame painted
        where = {
            name: page.evaluate("([id, p]) => window.__openfactoryScreenOf(id, p)", [cid, local])
            for name, (cid, local) in points.items()
        }
        page.screenshot(path=str(png))
    return {name: _patch_median(png, *xy) for name, xy in where.items()}


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#" + "".join(f"{c:02X}" for c in rgb)


def _assert_exact(measured: dict, wanted: dict, level: str) -> None:
    for name, rgb in measured.items():
        target = _rgb(wanted[name])
        worst = max(abs(a - b) for a, b in zip(rgb, target, strict=True))
        print(f"{level:8s} {name:13s} {_hex(rgb)} vs #{wanted[name]:06X}: {worst}")
        assert worst <= TOLERANCE, f"{level} {name}: {_hex(rgb)} vs #{wanted[name]:06X}"


@pytest.mark.parametrize("level", LEVELS)
def test_belts_render_their_exact_status_colors(tmp_path, level):
    measured = _sample(
        tmp_path,
        SORTER,
        ["CONV_01_RUN_DO=1"],
        level,
        # on the belt surface, clear of the emitter outline (x 0.2), the eye's beam (x 1.5) and
        # the remover outline at the far end
        {
            "running belt": ("CONV_01", [1.0, 0.0, 0.1]),
            "stopped belt": ("CONV_02", [0.9, 0.0, 0.0]),
        },
    )
    _assert_exact(measured, {"running belt": RUNNING, "stopped belt": STOPPED}, level)


@pytest.mark.parametrize("level", LEVELS)
@pytest.mark.parametrize(("color", "hex_value"), [("amber", ALARM_MEDIUM), ("red", ALARM_HIGH)])
def test_a_lit_pilot_light_renders_its_exact_alarm_color(tmp_path, level, color, hex_value):
    scene = tmp_path / "src" / DEVICES.name
    scene.parent.mkdir()
    text = DEVICES.read_text(encoding="utf-8")
    assert "color: amber" in text  # the scene's own lamp; the red case recolors a copy
    scene.write_text(text.replace("color: amber", f"color: {color}"), encoding="utf-8")
    measured = _sample(tmp_path, scene, ["PL_01_DO=1"], level, {"lamp": ("PL_01", [0.0, 0.0, 0.0])})
    _assert_exact(measured, {"lamp": hex_value}, level)


def _press(page) -> None:
    """Click PB_01's cap in the viewer (a maintained button stays pressed); wait for the state."""
    x, y = page.evaluate("() => window.__openfactoryScreenOf('PB_01', [0, 0, 0])")
    page.mouse.click(x, y)
    for _ in range(100):
        state = page.evaluate("() => fetch('/api/state').then((r) => r.json())")
        if state["components"]["PB_01"].get("pressed") is True:
            return
        page.wait_for_timeout(100)
    raise AssertionError("PB_01 did not report pressed after a click")


@pytest.mark.parametrize("level", LEVELS)
def test_a_pressed_pushbutton_renders_its_exact_status_color(tmp_path, level):
    scene = tmp_path / "src" / DEVICES.name
    scene.parent.mkdir()
    text = DEVICES.read_text(encoding="utf-8")
    assert "color: green\n" in text
    # maintained, so one click holds it pressed for the screenshot (momentary lasts 300 ms)
    scene.write_text(
        text.replace("color: green\n", "color: green\n    mode: maintained\n"), encoding="utf-8"
    )
    point = {"button": ("PB_01", [0.0, 0.0, 0.0])}
    released = _sample(tmp_path, scene, [], level, point)
    pressed = _sample(tmp_path, scene, [], level, point, before=_press)
    _assert_exact(pressed, {"button": RUNNING}, level)
    # released, the cap is shaded paint, visibly not the status color
    off = max(abs(a - b) for a, b in zip(released["button"], _rgb(RUNNING), strict=True))
    print(f"{level:8s} released      {_hex(released['button'])} vs #{RUNNING:06X}: {off}")
    assert off > TOLERANCE, (
        f"{level}: a released button reads as pressed ({_hex(released['button'])})"
    )
