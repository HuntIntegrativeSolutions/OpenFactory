"""The recipe checks (PLAN 3.3, for batch-mix-heat): fractions_within and temperature_within,
each passing and failing, an empty tank failing both, a run that ends before after_s, and the
load errors that catch a typo."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from openfactory.checks import evaluate
from openfactory.core.iotable import Writer
from openfactory.core.loader import build
from openfactory.core.schema import Scene


def valve(n: int, supply: str) -> list[dict]:
    return [
        {"id": f"XV_0{n}", "type": "valve_onoff", "description": f"Inlet Valve {n}",
         "position": [-1, 1, n], "from": supply, "to": "TK_01.top", "kv": 4, "travel_s": 0.2,
         "io": {"open": f"XV_0{n}_OPEN_DO"}},
    ]  # fmt: skip


COMPONENTS = [
    {"id": "SUP_01", "type": "supply", "description": "Water Supply", "position": [-2, 1, 1],
     "temperature_c": 15},
    *valve(1, "SUP_01"),
    {"id": "SUP_02", "type": "supply", "description": "Syrup Supply", "position": [-2, 1, 2],
     "temperature_c": 80, "ingredient": "syrup"},
    *valve(2, "SUP_02"),
    {"id": "TK_01", "type": "tank", "description": "Mixing Tank", "position": [0, 0, 0],
     "diameter": 0.4, "height": 0.6, "initial_level": 0.0},
    {"id": "HTR_01", "type": "heater", "description": "Mixing Tank Heater",
     "position": [0, 0.05, 0], "tank": "TK_01", "rated_kw": 45, "min_level": 0.05,
     "io": {"on": "HTR_01_ON_DO"}},
]  # fmt: skip


def scene(checks: dict, components: list[dict] = COMPONENTS) -> Scene:
    return Scene.model_validate({
        "openfactory": "0.1", "scene": {"name": "recipe-test", "description": "Recipe checks."},
        "plc": {"protocol": "none"}, "checks": {"duration_s": 60, **checks},
        "components": components,
    })  # fmt: skip


def run(checks: dict, plan: dict[float, dict[str, bool]], seconds: float) -> dict:
    """Step the scene, setting outputs at the planned times; grade it."""
    loaded = build(scene(checks))
    for k in range(round(seconds / 0.02)):
        t = round(k * 0.02, 2)
        for tag, value in plan.get(t, {}).items():
            loaded.io.set(tag, value, Writer.PLC)
        loaded.step(0.02)
    return {
        r.name.split(":")[0]: (r.passed, r.detail) for r in evaluate(loaded.scene.checks, loaded)
    }  # type: ignore[arg-type]


#: water for 6 s, then syrup for 4 s: about 60/40 by volume
RECIPE = {0.0: {"XV_01_OPEN_DO": True}, 6.0: {"XV_01_OPEN_DO": False, "XV_02_OPEN_DO": True},
          10.0: {"XV_02_OPEN_DO": False}}  # fmt: skip


def test_fractions_within_passes_a_recipe_and_fails_a_tank_of_one_ingredient():
    good = {"fractions_within": {"TK_01": {"fractions": {"syrup": [30, 50]}, "after_s": 12}}}
    passed, detail = run(good, RECIPE, 15.0)["fractions_within TK_01"]
    assert passed, detail
    assert detail.startswith("syrup ") and detail.endswith(" %")
    water_only = {0.0: {"XV_01_OPEN_DO": True}, 10.0: {"XV_01_OPEN_DO": False}}
    passed, detail = run(good, water_only, 15.0)["fractions_within TK_01"]
    assert not passed and detail == "syrup 0.00 to 0.00 %"  # no inflow brought any: 0 %


def test_temperature_within_needs_the_heat_and_fails_an_overshoot():
    band = {"temperature_within": {"TK_01": {"band": [45, 60], "after_s": 30}}}
    cold = run(band, RECIPE, 32.0)["temperature_within TK_01"]
    assert not cold[0], cold  # mixed at about 40 °C, never heated
    heated = {**RECIPE, 12.0: {"XV_02_OPEN_DO": False, "HTR_01_ON_DO": True},
              20.0: {"HTR_01_ON_DO": False}}  # fmt: skip
    passed, detail = run(band, heated, 32.0)["temperature_within TK_01"]
    assert passed, detail
    always = {**RECIPE, 12.0: {"XV_02_OPEN_DO": False, "HTR_01_ON_DO": True}}
    assert not run(band, always, 60.0)["temperature_within TK_01"][0]  # kept heating past 60


def test_an_empty_tank_fails_both_recipe_checks():
    checks = {
        "fractions_within": {"TK_01": {"fractions": {"water": [90, 100]}, "after_s": 1}},
        "temperature_within": {"TK_01": {"band": [0, 100], "after_s": 1}},
    }
    results = run(checks, {}, 3.0)  # nothing ever filled it
    assert results["fractions_within TK_01"] == (False, "the tank was empty at 1.00 s")
    assert results["temperature_within TK_01"] == (False, "the tank was empty at 1.00 s")


def test_a_run_that_ends_before_after_s_fails():
    checks = {"temperature_within": {"TK_01": {"band": [0, 100], "after_s": 10}}}
    assert run(checks, RECIPE, 5.0)["temperature_within TK_01"] == (
        False,
        "the run ended before 10 s",
    )


@pytest.mark.parametrize(
    ("checks", "message"),
    [
        (
            {"fractions_within": {"TK_01": {"fractions": {"sugar": [0, 10]}}}},
            "checks.fractions_within.TK_01: no supply delivers 'sugar' "
            "\\(the scene's ingredients: syrup, water\\)",
        ),
        (
            {"temperature_within": {"HTR_01": {"band": [0, 50]}}},
            "checks.temperature_within: 'HTR_01' is a heater, not a tank",
        ),
        (
            {"fractions_within": {"TK_01": {"fractions": {"syrup": [40, 30]}}}},
            "fractions.syrup: band \\[lo, hi\\] needs lo < hi",
        ),
        ({"fractions_within": {"TK_01": {"fractions": {}}}}, "at least 1 item"),
        ({"temperature_within": {"TK_01": {"band": [60, 50]}}}, "needs lo < hi"),
    ],
)
def test_recipe_checks_name_real_tanks_and_ingredients(checks, message):
    with pytest.raises(ValidationError, match=message):
        scene(checks)
