"""PLAN 4.1c fix 7 (ruled 2026-11-12): a mesh file set through the editor's form must resolve
inside the scene's folder, which lies within its project root (fix 5). Anything else is refused
at preview with a clear finding, the save is refused, the file is not served and the scene file
is unchanged. A path with ``..`` or an absolute path is refused by the schema (E100, naming the
rule, PLAN 2.15); a name inside the folder that is a symlink out passes the schema and is
refused by E104."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openfactory.core.loader import build
from openfactory.core.schema import load_scene
from openfactory.lint import lint_scene
from openfactory.server.app import create_app
from openfactory.server.editor import EditSession
from openfactory.server.runtime import Runtime

REPO = Path(__file__).resolve().parents[2]
GLB = REPO / "tests" / "scenes" / "models" / "pusher-housing.glb"


@pytest.fixture
def project(tmp_path):
    """A project (``.git`` at its root) holding the sorter in ``scenes/``, one valid mesh inside
    the scene's folder, one in the project but outside the scene's folder, and one above the
    project root."""
    root = tmp_path / "plant"
    scenes = root / "scenes"
    (scenes / "models").mkdir(parents=True)
    (root / ".git").mkdir()
    for f in (REPO / "scenes").glob("sorting-by-height*"):
        shutil.copy(f, scenes / f.name)
    shutil.copy(GLB, scenes / "models" / "inside.glb")
    (root / "shared").mkdir()
    shutil.copy(GLB, root / "shared" / "in-project.glb")
    shutil.copy(GLB, tmp_path / "above.glb")
    scene = scenes / "sorting-by-height.yaml"
    runtime = Runtime(build(load_scene(scene), base_dir=scenes), realtime=False)
    client = TestClient(create_app(runtime, web_dist=None, edit=EditSession(scene)))
    return scene, client, tmp_path


def _mesh(client: TestClient, file: str) -> tuple[str, list[dict]]:
    base = client.get("/api/edit/source").json()["base"]
    op = {"op": "set", "path": ["components", "CYL_01", "mesh"], "value": {"file": file}}
    return base, [op]


def test_a_mesh_inside_the_scenes_folder_is_previewed_and_served(project):
    scene, client, _ = project
    base, ops = _mesh(client, "models/inside.glb")
    res = client.post("/api/edit/preview", json={"base": base, "ops": ops}).json()
    assert res["errors"] == 0 and not [f for f in res["findings"] if f["code"] == "E104"]
    cyl = next(c for c in res["scene"]["components"] if c["id"] == "CYL_01")
    assert cyl["mesh"]["url"].startswith("/models/CYL_01.glb")
    assert client.get("/models/CYL_01.glb").content == GLB.read_bytes()


REFUSED = {
    "../shared/in-project.glb": ("E100", "leaves the scene's folder"),
    "../../above.glb": ("E100", "leaves the scene's folder"),
    "absolute": ("E100", "is absolute"),
    "symlink": ("E104", "outside the scene's folder"),
}


@pytest.mark.parametrize("where", list(REFUSED))
def test_a_mesh_outside_the_scenes_folder_is_refused_at_preview(project, where):
    scene, client, tmp = project
    before = scene.read_text(encoding="utf-8")
    file = where
    if where == "absolute":
        file = str(tmp / "above.glb")
    elif where == "symlink":  # inside by name, outside once followed
        link = scene.parent / "models" / "link.glb"
        try:
            os.symlink(tmp / "above.glb", link)
        except OSError:
            pytest.fail("symlinks are not supported here; run the tests on a Linux filesystem")
        file = "models/link.glb"
    base, ops = _mesh(client, file)
    res = client.post("/api/edit/preview", json={"base": base, "ops": ops}).json()
    code, words = REFUSED[where]
    found = [f for f in res["findings"] if f["code"] == code]
    assert len(found) == 1 and res["errors"] >= 1, res["findings"]
    assert words in found[0]["message"], found[0]["message"]
    if code == "E104":
        assert "CYL_01" in found[0]["message"]
        assert res["scene"] is not None  # the plant still draws, with the primitive housing
    assert client.get("/models/CYL_01.glb").status_code == 404  # never served
    saved = client.post("/api/edit/save", json={"base": base, "ops": ops})
    assert saved.status_code == 422
    assert scene.read_text(encoding="utf-8") == before


def test_lint_reports_e104_for_a_scene_file_written_by_hand(project):
    scene, _, tmp = project
    os.symlink(tmp / "above.glb", scene.parent / "models" / "out.glb")
    text = scene.read_text(encoding="utf-8").replace(
        "  - id: CYL_01\n", "  - id: CYL_01\n    mesh: {file: models/out.glb}\n", 1
    )
    scene.write_text(text, encoding="utf-8")
    codes = [f.code for f in lint_scene(scene)]
    assert "E104" in codes and "W209" not in codes  # no license search for a refused path
