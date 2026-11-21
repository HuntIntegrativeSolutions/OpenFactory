"""PLAN 5.5, review of 2026-11-20 (second set, 1b): two belts side by side, a laid-out device on
each one's facing rail. A device rides only the belt whose rail it stands on: `openfactory
layout` places each again on its own belt, and changes no byte when nothing moved. (The editor's
side is `web/src/editor/mount.test.ts`, "two belts side by side".)"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openfactory.core.mounts import place_on_rail
from openfactory.layout import relayout_scene

GAP_M = 0.15  # between the belts' facing edges: 0.5 m belts, centres 0.65 m apart


def belt(cid: str, z: float) -> SimpleNamespace:
    return SimpleNamespace(id=cid, type="belt_conveyor", position=[0.0, 0.0, z],
                           rotation_deg=0.0, length=3.0, width=0.5)  # fmt: skip


def scene(gap: float) -> str:
    a, b = belt("CV_A", 0.0), belt("CV_B", 0.5 + gap)
    pa = place_on_rail("pusher", {}, a, "rail_right", 1.0, None)
    pb = place_on_rail("pusher", {}, b, "rail_left", 2.0, None)

    def comp(cid: str, rail: str, at: object, turn: float, conveyor: str) -> str:
        pos = ", ".join(repr(float(v)) for v in at.position)  # type: ignore[attr-defined]
        return (f"  - id: {cid}  # on {conveyor}'s {rail}\n    type: pusher\n"
                f"    description: Reject Pusher {cid[-1]}\n    position: [{pos}]\n"
                f"    rotation_deg: {float(turn)!r}\n    laid_out: true\n"
                f"    stroke: 0.4\n    extend_time_s: 0.4\n"
                f"    io:\n      extend: {cid}_EXT_DO\n")  # fmt: skip

    def conv(c: SimpleNamespace) -> str:
        return (f"  - id: {c.id}\n    type: belt_conveyor\n    description: Belt {c.id[-1]}\n"
                f"    position: [0.0, 0.0, {c.position[2]:g}]\n"
                f"    length: 3.0\n    speed_mps: 0.4\n"
                f"    width: 0.5\n    io:\n      run: {c.id}_RUN_DO\n")  # fmt: skip

    return (
        'openfactory: "0.1"\nscene:\n  name: two-belts\n  description: Two belts side by side.\n'
        "  seed: 1\nplc:\n  protocol: modbus_tcp\ncomponents:\n"
        + conv(a)
        + conv(b)
        + comp("DV_A", "right rail", pa, pa.rotation_deg, "CV_A")
        + comp("DV_B", "left rail", pb, pb.rotation_deg, "CV_B")
    )


@pytest.mark.parametrize("gap", [GAP_M, 0.3])
def test_layout_keeps_each_device_on_its_own_belt(tmp_path, gap):
    text = scene(gap)
    (tmp_path / "two.yaml").write_text(text, encoding="utf-8")
    again, _moved, _ = relayout_scene(text, tmp_path)
    if again != text:
        import difflib

        print("".join(difflib.unified_diff(text.splitlines(True), again.splitlines(True))))
    assert again == text


@pytest.mark.xfail(strict=True, reason="below a 0.10 m gap a pusher is nearer the other belt's "
                   "centre line: position alone cannot tell (review of 2026-11-20, second set, "
                   "1b); its heading or a kept mount would")  # fmt: skip
def test_below_a_tenth_of_a_metre_position_alone_cannot_tell(tmp_path):
    text = scene(0.05)
    again, _moved, _ = relayout_scene(text, tmp_path)
    assert again == text
