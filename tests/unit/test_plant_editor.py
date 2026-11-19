"""PLAN 5.4e: the editor on a plant (design note §4.3). One file is edited; the whole plant is
shown, each component marked with its file; preview and save lint the whole plant; a save
writes only the edited file."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from openfactory.cli import app as cli
from openfactory.core.loader import build
from openfactory.core.schema import load_scene
from openfactory.server.app import create_app
from openfactory.server.editor import EditSession
from openfactory.server.runtime import Runtime

REPO = Path(__file__).resolve().parents[2]
SAMPLE = REPO / "scenes" / "plants" / "wash-plant"


def sample_copy(tmp_path: Path) -> Path:
    folder = tmp_path / "wash-plant"
    shutil.copytree(SAMPLE, folder, ignore=shutil.ignore_patterns("__pycache__"))
    (tmp_path / "_library").mkdir()
    shutil.copyfile(SAMPLE.parents[1] / "_library" / "driven_item.yaml",
                    tmp_path / "_library" / "driven_item.yaml")  # fmt: skip
    (tmp_path / "openfactory.toml").write_text("", encoding="utf-8")  # a project root
    return folder / "wash-plant.yaml"


def client_for(plant: Path, area: str | None) -> TestClient:
    runtime = Runtime(build(load_scene(plant), base_dir=plant.parent), realtime=False)
    session = EditSession(plant, area=area)
    return TestClient(create_app(runtime, web_dist=None, edit=session),
                      base_url="http://127.0.0.1")  # fmt: skip


def every_file(folder: Path) -> dict[Path, bytes]:
    return {p: p.read_bytes() for p in folder.parent.rglob("*") if p.is_file()}


def test_edit_area_edits_that_file_and_shows_the_whole_plant_marked_by_file(tmp_path) -> None:
    plant = sample_copy(tmp_path)
    client = client_for(plant, "WASH")
    src = client.get("/api/edit/source").json()
    assert src["name"] == "areas/wash.yaml" and src["plant"] == "wash-plant.yaml"
    assert src["text"] == (plant.parent / "areas" / "wash.yaml").read_text(encoding="utf-8")
    assert {c["id"] for c in src["data"]["components"]} >= {"CV_201", "TK_201"}
    assert "CV_101" not in {c["id"] for c in src["data"]["components"]}
    doc = client.get("/api/scene").json()
    assert doc["mode"] == "edit"
    assert doc["edit"] == {"file": "areas/wash.yaml", "plant": "wash-plant.yaml", "area": "WASH"}
    by_id = {c["id"]: c for c in doc["components"]}
    assert by_id["CV_101"]["source"] == "areas/load.yaml" and not by_id["CV_101"]["editable"]
    assert by_id["CV_201"]["source"] == "areas/wash.yaml" and by_id["CV_201"]["editable"]
    # a composite's parts carry their instance's file
    parts = [c for c in doc["components"] if (c.get("composite") or {}).get("id") == "FAN_101"]
    assert parts and all(p["source"] == "areas/load.yaml" and not p["editable"] for p in parts)


def test_edit_plant_without_area_edits_the_plant_file_with_every_area_dimmed(tmp_path) -> None:
    plant = sample_copy(tmp_path)
    client = client_for(plant, None)
    assert client.get("/api/edit/source").json()["name"] == "wash-plant.yaml"
    doc = client.get("/api/scene").json()
    assert doc["edit"]["file"] == "wash-plant.yaml" and doc["edit"]["area"] is None
    assert doc["components"] and not any(c["editable"] for c in doc["components"])


def test_a_save_under_area_writes_only_that_file(tmp_path) -> None:
    plant = sample_copy(tmp_path)
    wash = plant.parent / "areas" / "wash.yaml"
    before = every_file(plant.parent)
    client = client_for(plant, "WASH")
    base = client.get("/api/edit/source").json()["base"]
    op = {"op": "set", "path": ["components", "CV_201", "speed_mps"], "value": 0.35}
    preview = client.post("/api/edit/preview", json={"base": base, "ops": [op]}).json()
    assert preview["errors"] == 0 and preview["load_error"] is None
    cv = next(c for c in preview["scene"]["components"] if c["id"] == "CV_201")
    assert cv["editable"] and "CV_101" in {c["id"] for c in preview["scene"]["components"]}
    assert every_file(plant.parent) == before  # a preview writes nothing
    saved = client.post("/api/edit/save", json={"base": base, "ops": [op]})
    assert saved.status_code == 200 and saved.json()["saved"] == "areas/wash.yaml"
    after = every_file(plant.parent)
    changed = sorted(p.relative_to(tmp_path).as_posix() for p in after if after[p] != before[p])
    assert changed == ["wash-plant/areas/wash.yaml"]  # no other file's bytes moved
    assert set(after) == set(before)  # and none was added or removed
    assert "speed_mps: 0.35" in wash.read_text(encoding="utf-8")
    assert load_scene(plant).components  # the plant still loads


@pytest.mark.parametrize(
    ("op", "other"),
    [
        # an id the other area has (CV_101 is LOAD's)
        ({"op": "add", "component": {"id": "CV_101", "type": "photo_eye", "description": "Eye",
                                     "position": [5.0, 0.1, 3.0], "beam_length": 0.6,
                                     "io": {"detect": "PE_299_DI"}}},
         "id 'CV_101' is in both areas/load.yaml and areas/wash.yaml"),
        # a tag the other area has
        ({"op": "set", "path": ["components", "PE_201", "io", "detect"], "value": "PE_101_DI"},
         "tag 'PE_101_DI' is in both areas/load.yaml and areas/wash.yaml"),
    ],
)  # fmt: skip
def test_an_id_or_tag_another_area_has_is_refused_before_save_naming_it(tmp_path, op, other):
    plant = sample_copy(tmp_path)
    before = every_file(plant.parent)
    client = client_for(plant, "WASH")
    base = client.get("/api/edit/source").json()["base"]
    preview = client.post("/api/edit/preview", json={"base": base, "ops": [op]}).json()
    assert preview["errors"] == 1 and preview["scene"] is None
    assert [f["code"] for f in preview["findings"]] == ["E100"]
    assert other in preview["findings"][0]["message"] and other in preview["load_error"]
    refused = client.post("/api/edit/save", json={"base": base, "ops": [op]})
    assert refused.status_code == 422
    assert other in refused.json()["detail"]["load_error"]
    assert every_file(plant.parent) == before  # nothing written


def test_a_save_that_would_break_the_plant_is_refused_with_the_loaders_message(tmp_path):
    plant = sample_copy(tmp_path)
    before = every_file(plant.parent)
    client = client_for(plant, "LOAD")
    base = client.get("/api/edit/source").json()["base"]
    # CV_101 is an end of the plant's connection across the areas: the area alone still loads,
    # the plant does not
    ops = [{"op": "delete", "id": "CV_101"}, {"op": "delete", "id": "EMT_101"},
           {"op": "delete", "id": "PE_101"}]  # fmt: skip
    refused = client.post("/api/edit/save", json={"base": base, "ops": ops})
    assert refused.status_code == 422
    detail = refused.json()["detail"]
    with pytest.raises(ValueError) as loader:  # the loader's own message, word for word
        from openfactory.core.schema import parse_scene

        text = client.post("/api/edit/preview", json={"base": base, "ops": ops}).json()["text"]
        parse_scene(plant.read_text(encoding="utf-8"), plant.parent, source=plant,
                    overrides={(plant.parent / "areas" / "load.yaml").resolve(): text})  # fmt: skip
    assert detail["load_error"] == str(loader.value) and "CV_101" in detail["load_error"]
    assert every_file(plant.parent) == before


def test_a_new_component_is_numbered_past_every_id_of_the_plant(tmp_path) -> None:
    plant = sample_copy(tmp_path)
    client = client_for(plant, "LOAD")
    base = client.get("/api/edit/source").json()["base"]
    new = client.post("/api/edit/new", json={"base": base, "ops": [], "type": "photo_eye",
                                             "position": [4.0, 0, 3.0]}).json()  # fmt: skip
    ids = set(load_scene(plant).plant.sources)
    assert new["component"]["id"] not in ids


def test_edit_area_on_the_command_line(tmp_path) -> None:
    plant = sample_copy(tmp_path)
    done = CliRunner().invoke(cli, ["edit", str(plant), "--area", "NOPE"])
    assert done.exit_code == 1 and "no area 'NOPE'; its areas are LOAD, WASH" in done.output
    scene = tmp_path / "plain.yaml"
    shutil.copyfile(REPO / "scenes" / "sorting-by-height.yaml", scene)
    done = CliRunner().invoke(cli, ["edit", str(scene), "--area", "LOAD"])
    assert done.exit_code == 1 and "not a plant and has no areas" in done.output
