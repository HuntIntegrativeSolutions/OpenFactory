"""PLAN 3.8: the PlantPAx demo's Ignition UDTs and Perspective faceplates. Their members are
exactly the names the L5X's source table cites (PROCES-RM013I), the UDTs reach the controller only
through the gateway's device alias, and the faceplates bind only through their {Root} parameter."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from openfactory.core.schema import load_scene
from openfactory.export import plantpax, plantpax_hmi

REPO = Path(__file__).resolve().parents[2]


def scene(name: str):  # noqa: ANN201
    return load_scene(REPO / "scenes" / f"{name}.yaml")


@pytest.mark.parametrize("name", sorted(plantpax.MAPS))
def test_each_udt_has_exactly_the_cited_members_of_its_object(name):
    doc = plantpax_hmi.plantpax_udts(scene(name))
    types, area = doc["tags"]
    aois = {i.aoi for i in plantpax.MAPS[name].instances}
    assert {t["name"] for t in types["tags"]} == {f"{a}_UDT" for a in aois}
    for udt in types["tags"]:
        aoi = udt["name"].removesuffix("_UDT")
        names = [m["name"] for m in udt["tags"]]
        assert names == plantpax_hmi.members(aoi)
        assert set(names) == {m for a, m in plantpax.CITED if a == aoi}
        for member in udt["tags"]:
            assert member["opcItemPath"] == f"ns=1;s=[{{Device}}]{{InstanceName}}.{member['name']}"
            assert member["accessRights"] == "Read_Only"
            assert member["historyEnabled"] == ((aoi, member["name"]) == ("P_AIn", "Val"))
    instances = {i["name"]: i for i in area["tags"]}
    for inst in plantpax.MAPS[name].instances:
        got = instances[inst.tag.removeprefix("DEMO_")]
        assert got["typeId"] == f"{inst.aoi}_UDT"
        assert got["parameters"]["InstanceName"]["value"] == inst.tag
    text = json.dumps(doc)
    assert not re.search(r"\d+\.\d+\.\d+\.\d+", text)  # never an IP address


def test_the_device_alias_is_a_parameter():
    doc = plantpax_hmi.plantpax_udts(scene("tank-level-pid"), device="Bench_L83")
    instance = doc["tags"][1]["tags"][0]
    assert instance["parameters"]["Device"]["value"] == "Bench_L83"


@pytest.mark.parametrize("name", sorted(plantpax.MAPS))
def test_faceplates_bind_only_cited_members_through_their_root(name):
    doc = plantpax_hmi.plantpax_faceplates(scene(name))
    assert plantpax_hmi.as_json(doc)[:300].count("generated, pending PE review") == 1
    aois = {i.aoi for i in plantpax.MAPS[name].instances}
    assert set(doc["views"]) == {f"PlantPAx/{a}_Faceplate" for a in aois}
    for key, view in doc["views"].items():
        aoi = view["custom"]["object"]
        paths = re.findall(r'"tagPath": "([^"]*)"', json.dumps(view))
        for path in paths:
            if path.startswith("[default]"):
                continue  # the parameter's placeholder default, overridden when the view opens
            member = path.removeprefix("{Root}/").removeprefix("{Root}.")
            assert path.startswith("{Root}"), (key, path)
            assert member == "Documentation" or (aoi, member) in plantpax.CITED, (key, path)
        commands = [c for c in view["root"]["children"] if c["meta"]["name"] == "Commands"]
        assert "Display only" in commands[0]["props"]["text"]
        assert "ia.input.button" not in json.dumps(view)  # writes nothing until OCmd_ is verified


def test_a_scene_without_a_map_is_refused():
    with pytest.raises(ValueError, match="no PlantPAx map"):
        plantpax_hmi.plantpax_udts(scene("batch-mix-heat"))
