"""Housekeeping 4: the shipped sorter scene sorts headless under scripted PLC logic.

The script is the same logic a PLC program for this scene would run (PLAN 1.14): run both belts,
enable the emitter, and on each PE_01 rising edge (tall boxes only, the beam is above short ones)
extend the pusher after the box's travel time to the plate, then retract on the extended limit.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from openfactory.core.iotable import Writer
from openfactory.core.loader import LoadedScene, load

EXAMPLE = Path(__file__).resolve().parents[2] / "scenes" / "sorting-by-height.yaml"
DT = 0.02
#: PE_01 trips when a tall box's leading edge reaches x = 1.5 (center 1.35). The plate (x 2.35 to
#: 2.65) needs ~0.12 s to reach the box and ~0.2 s more to push it across, so extend when the
#: center is at ~2.39: (2.39 - 1.35) m / 0.5 m/s = 2.08 s after the trip.
EXTEND_DELAY_S = 2.08


class ScriptedPlc:
    """A 50 Hz PLC scan written against the scene's tags."""

    def __init__(self, loaded: LoadedScene) -> None:
        self.io = loaded.io
        self.time = 0.0
        self.pe_last = False
        self.due: list[float] = []  # extend times for tall boxes in flight
        for tag, value in (
            ("CONV_01_RUN_DO", True),
            ("CONV_01_SPD_AO", 100.0),
            ("CONV_02_RUN_DO", True),
            ("EMT_01_EN_DO", True),
        ):
            self.io.set(tag, value, Writer.PLC)

    def scan(self) -> None:
        pe = bool(self.io.get("PE_01_DI"))
        if pe and not self.pe_last:
            self.due.append(self.time + EXTEND_DELAY_S)
        self.pe_last = pe
        if self.due and self.time >= self.due[0] - 1e-9:
            self.due.pop(0)
            self.io.set("CYL_01_EXT_DO", True, Writer.PLC)
        if self.io.get("CYL_01_EXT_DI"):
            self.io.set("CYL_01_EXT_DO", False, Writer.PLC)
        self.time += DT


def run(seconds: float) -> tuple[LoadedScene, dict[str, Counter[str]], list[str]]:
    loaded = load(EXAMPLE)
    plc = ScriptedPlc(loaded)
    rem = {r: loaded.component(r) for r in ("REM_01", "REM_02")}
    pushed: list[str] = []  # parts seen moving from CONV_01 onto CONV_02 (via a short drop)
    last_belt: dict[str, str] = {}
    for _ in range(round(seconds / DT)):
        plc.scan()
        loaded.step(DT)
        for part in loaded.world.parts.values():
            if part.carrier is None:
                continue
            if last_belt.get(part.id) == "CONV_01" and part.carrier == "CONV_02":
                pushed.append(f"{part.id}:{part.name}")
            last_belt[part.id] = part.carrier
    return loaded, {r: c.removed for r, c in rem.items()}, pushed


def test_pusher_diverts_tall_boxes_and_short_boxes_reach_rem_01():
    loaded, removed, pushed = run(30.0)
    print(
        f"emitted {loaded.world.spawned}; REM_01 {dict(removed['REM_01'])}; "
        f"REM_02 {dict(removed['REM_02'])}; lost {dict(loaded.world.lost)}; pushed {pushed}"
    )
    assert removed["REM_02"]["tall"] >= 1, "no tall box was pushed onto the divert lane"
    assert removed["REM_01"]["short"] >= 1, "no short box reached REM_01"
    assert removed["REM_01"]["tall"] == 0  # every tall box was diverted
    assert removed["REM_02"]["short"] == 0  # no short box was pushed
    assert loaded.world.lost == {}
    assert pushed and all(p.endswith(":tall") for p in pushed)


def test_without_the_pusher_tall_boxes_stay_on_the_main_line():
    loaded = load(EXAMPLE)
    plc = ScriptedPlc(loaded)
    plc.due = []
    for _ in range(round(30.0 / DT)):
        plc.due.clear()  # never extend
        plc.scan()
        loaded.step(DT)
    removed = loaded.component("REM_01").removed
    assert removed["tall"] >= 1 and loaded.component("REM_02").removed == {}
