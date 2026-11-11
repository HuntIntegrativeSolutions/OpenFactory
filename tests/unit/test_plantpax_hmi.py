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
        cited = {**plantpax.CITED, **plantpax.DISPLAY}
        assert set(names) == {m for a, m in cited if a == aoi}
        assert not any("alarms" in member for member in udt["tags"])  # none rationalized
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
            cited = (aoi, member) in plantpax.CITED or (aoi, member) in plantpax.DISPLAY
            assert member == "Documentation" or cited, (key, path)
        bound = {p.removeprefix("{Root}/") for p in paths if p.startswith("{Root}/")}
        assert bound == set(plantpax_hmi.shown(aoi)), key  # each shown member bound, once
        display = {m for a, m in plantpax.DISPLAY if a == aoi}
        assert display <= bound, (key, display - bound)  # every faceplate citation is used
        commands = [c for c in view["root"]["children"] if c["meta"]["name"] == "Commands"]
        assert "Display only" in commands[0]["props"]["text"]
        assert "ia.input.button" not in json.dumps(view)  # writes nothing until OCmd_ is verified


def test_a_scene_without_a_map_is_refused():
    with pytest.raises(ValueError, match="no PlantPAx map"):
        plantpax_hmi.plantpax_udts(scene("batch-mix-heat"))


def test_every_udt_member_has_its_rm013_data_type():
    """Each member's Ignition type follows plantpax.DATA_TYPE (BOOL -> Boolean, REAL -> Float4),
    so a newly cited status bit such as P_PIDE.Sts_Prog is a Boolean (review of 2026-11-10)."""
    types, _ = plantpax_hmi.plantpax_udts(scene("plantpax-tank-level"))["tags"]
    seen = 0
    for udt in types["tags"]:
        aoi = udt["name"].removesuffix("_UDT")
        for m in udt["tags"]:
            want = {"BOOL": "Boolean", "REAL": "Float4", "DINT": "Int4"}[
                plantpax.DATA_TYPE[aoi, m["name"]]
            ]
            assert m["dataType"] == want, (aoi, m["name"])
            seen += 1
    assert seen == len(plantpax.DATA_TYPE)
    assert plantpax.DATA_TYPE["P_PIDE", "Sts_Prog"] == "BOOL"


def test_each_faceplate_shows_mode_owner_state_interlock_and_alarms_from_cited_members():
    """PLAN 3.8 follow-up (ruled 2026-11-10): mode, owner, running or position state, interlock
    state and alarms on all four faceplates, display only, from the cited members."""
    doc = plantpax_hmi.plantpax_faceplates(scene("plantpax-tank-level"))
    views = {v["custom"]["object"]: json.dumps(v) for v in doc["views"].values()}
    assert set(views) == {"P_AIn", "P_PIDE", "P_ValveC", "P_Motor"}
    for aoi in ("P_PIDE", "P_ValveC", "P_Motor"):
        text = views[aoi]
        assert "Sts_Prog" in text and "Sts_Oper" in text and "Sts_OoS" in text  # mode
        assert "Val_Owner" in text and "Sts_IntlkTrip" in text  # owner, interlock
    assert "Sts_Auto" in views["P_PIDE"] and "Sts_Man" in views["P_PIDE"]  # loop mode
    assert "Sts_Running" in views["P_Motor"] and "Sts_Stopped" in views["P_Motor"]
    assert "Val_Pos" in views["P_ValveC"]
    assert "no command source" in views["P_AIn"]  # P_AIn has none
    for aoi, bits in plantpax_hmi.ALARMS.items():
        assert all(b in views[aoi] for b in bits)
        assert "not rationalized" in views[aoi] and plantpax_hmi.ALARM in views[aoi]
        assert "ia.input" not in views[aoi]  # still display only
