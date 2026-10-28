"""PLAN 2.7: every shipped scene comes with both lockfiles, an OpenPLC program, a stand-in script,
and a guide, and runs 60 s under its script with the result its guide states. The same 60 s
under OpenPLC is recorded in each scene's commit (it needs the OpenPLC container, not CI).
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pytest

from openfactory.core.loader import load
from openfactory.pyplc import ScriptPlc

REPO = Path(__file__).resolve().parents[2]
DT = 0.02

#: Scene name -> parts each remover has taken after 60 s under the stand-in script.
EXPECTED: dict[str, dict[str, dict[str, int]]] = {
    "sorting-by-height": {"REM_01": {"short": 9}, "REM_02": {"tall": 9}},
    "conveyor-start-stop": {"REM_01": {"box": 17}},
}
#: The sorter's guide is the OpenPLC quickstart (PLAN 1.14).
GUIDES = {"sorting-by-height": "openplc-quickstart.md"}


def shipped() -> list[str]:
    return sorted(p.stem for p in (REPO / "scenes").glob("*.yaml") if ".modbus" not in p.name)


def run_script(name: str, seconds: float = 60.0, actions=None):
    loaded = load(REPO / "scenes" / f"{name}.yaml")
    plc = ScriptPlc(REPO / "examples" / "pyplc" / f"{name}.py", loaded.io)
    t = 0.0
    for k in range(round(seconds / DT)):
        if actions and k in actions:
            actions[k](loaded)
        plc.scan(t)
        loaded.step(DT)
        t += DT
    return loaded


def removers(loaded) -> dict[str, dict[str, int]]:
    return {c.id: dict(Counter(c.removed)) for c in loaded.components if c.type == "part_remover"}


def test_every_shipped_scene_has_an_expected_result():
    assert shipped() == sorted(EXPECTED)


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_scene_ships_lockfiles_programs_and_a_guide(name):
    for path in (
        f"scenes/{name}.float32.modbus.yaml",
        f"scenes/{name}.int16_scaled.modbus.yaml",
        f"examples/openplc/{name}.st",
        f"examples/pyplc/{name}.py",
        f"docs/guides/{GUIDES.get(name, name + '.md')}",
    ):
        assert (REPO / path).is_file(), f"{name}: missing {path}"
    index = (REPO / "docs" / "guides" / "README.md").read_text(encoding="utf-8")
    assert f"`{name}`" in index, f"{name} is not listed in docs/guides/README.md"


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_openplc_program_and_script_use_the_same_tags(name):
    st = (REPO / "examples" / "openplc" / f"{name}.st").read_text(encoding="utf-8")
    script = (REPO / "examples" / "pyplc" / f"{name}.py").read_text(encoding="utf-8")
    declared = set(re.findall(r"^\s*([A-Z][A-Z0-9_]+) AT %[IQ]", st, flags=re.MULTILINE))
    body = st.rsplit("END_VAR", 1)[1]  # the logic, after the last declaration block
    st_tags = {tag for tag in declared if re.search(rf"\b{tag}\b", body)}
    script_tags = set(re.findall(r'io\["([A-Z][A-Z0-9_]+)"\]', script))
    assert st_tags == script_tags, (st_tags - script_tags, script_tags - st_tags)


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_sixty_seconds_under_the_script(name):
    loaded = run_script(name)
    got = removers(loaded)
    lost = dict(loaded.world.lost)
    print(f"{name}: spawned {loaded.world.spawned}, by remover {got}, lost {lost}")
    assert got == EXPECTED[name]
    assert not loaded.world.lost


def test_hello_world_stop_drops_the_seal_in_and_a_selector_cycle_restarts():
    def press_stop(loaded):
        loaded.component("PB_STOP").press()

    def select(position):
        return lambda loaded: loaded.component("SS_01").set_position(position)

    # stop at 10 s: stopped at 10.5 s; then OFF at 20 s and RUN at 21 s restart it
    loaded = run_script(
        "conveyor-start-stop",
        seconds=10.5,
        actions={500: press_stop},
    )
    assert loaded.io.get("CONV_01_RUN_DO") is False and loaded.io.get("PL_RUN_DO") is False
    loaded = run_script(
        "conveyor-start-stop",
        seconds=25.0,
        actions={500: press_stop, 1000: select("a"), 1050: select("b")},
    )
    assert loaded.io.get("CONV_01_RUN_DO") is True and loaded.io.get("EMT_01_EN_DO") is True
