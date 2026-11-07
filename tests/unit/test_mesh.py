"""PLAN 2.15: a custom mesh per component. ``mesh.file`` is a .glb beside the scene, validated at
schema level (no absolute path, no '..', no URL, .glb only); a missing or broken file falls back to
the primitive with one warning line; the app serves only declared files, by component id; a
recording carries no mesh; the example housing rebuilds byte for byte; every mesh in the repo is
under 1 MB with a license record (W209 otherwise)."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from typer.testing import CliRunner

from openfactory.cli import app as cli
from openfactory.core.loader import load
from openfactory.core.schema import MeshSpec
from openfactory.lint import lint_scene
from openfactory.server.app import create_app
from openfactory.server.runtime import Runtime

REPO = Path(__file__).resolve().parents[2]
SCENE = REPO / "tests" / "scenes" / "sorter-custom-housing.yaml"
GLB = REPO / "tests" / "scenes" / "models" / "pusher-housing.glb"
SCRIPT = REPO / "examples" / "pyplc" / "sorting-by-height.py"


def copy_scene(tmp_path: Path, mesh_file: str | None = None, *, with_model: bool = True) -> Path:
    """The test scene in ``tmp_path``, optionally with another mesh.file and without the model."""
    text = SCENE.read_text(encoding="utf-8")
    if mesh_file is not None:
        text = text.replace("file: models/pusher-housing.glb", f"file: {mesh_file!r}")
    scene = tmp_path / SCENE.name
    scene.write_text(text, encoding="utf-8")
    if with_model:
        (tmp_path / "models").mkdir()
        shutil.copy(GLB, tmp_path / "models" / GLB.name)
        shutil.copy(GLB.with_name(GLB.name + ".license.txt"), tmp_path / "models")
    return scene


@pytest.mark.parametrize(
    "bad, why",
    [
        ("../pusher-housing.glb", "'..'"),
        ("models/../../x.glb", "'..'"),
        ("models\\..\\x.glb", "'..'"),
        ("/srv/models/x.glb", "absolute"),
        ("C:\\models\\x.glb", "absolute"),
        ("\\\\server\\share\\x.glb", "absolute"),
        ("https://example.com/x.glb", "URL"),
        ("file:models/x.glb", "URL"),
        ("models/x.gltf", ".glb"),
        ("models/x.obj", ".glb"),
    ],
)
def test_mesh_file_must_be_a_local_glb(bad, why):
    with pytest.raises(ValidationError, match=why):
        MeshSpec(file=bad)


def test_good_paths_and_scale_forms():
    assert MeshSpec(file="models\\X.GLB").file == "models/X.GLB"
    assert MeshSpec(file="m.glb", scale=2).scale3() == (2, 2, 2)
    assert MeshSpec(file="m.glb", scale=[1, 2, 3]).scale3() == (1, 2, 3)
    with pytest.raises(ValidationError):
        MeshSpec(file="m.glb", scale=0)


@pytest.mark.parametrize("bad", ["../pusher-housing.glb", "https://example.com/x.glb"])
def test_validate_fails_a_scene_with_dotdot_or_a_url(tmp_path, bad):
    scene = copy_scene(tmp_path, bad)
    result = CliRunner().invoke(cli, ["validate", str(scene)])
    assert result.exit_code == 1, result.output
    assert "mesh.file" in result.output


def test_a_resolved_mesh_is_in_the_scene_document_and_served_by_id():
    loaded = load(SCENE)
    assert loaded.warnings == [] and set(loaded.meshes) == {"CYL_01"}
    runtime = Runtime(loaded, realtime=False)
    doc = runtime.scene_document()
    cyl = next(c for c in doc["components"] if c["id"] == "CYL_01")
    assert cyl["mesh"] == {
        "url": "/models/CYL_01.glb", "scale": [1.0, 1.0, 1.0], "offset": [0.0, 0.0, 0.0],
        "rotation_deg": 0.0,
    }  # fmt: skip
    assert all("mesh" not in c for c in doc["components"] if c["id"] != "CYL_01")
    assert all("mesh" not in c for c in runtime.scene_document(meshes=False)["components"])
    client = TestClient(create_app(runtime, web_dist=None))
    res = client.get("/models/CYL_01.glb")
    assert res.status_code == 200 and res.content == GLB.read_bytes()
    assert res.headers["content-type"] == "model/gltf-binary"
    for other in (
        "CONV_01.glb",
        "CYL_01.gltf",
        "pusher-housing.glb",
        "..%2Fsorter-custom-housing.yaml",
    ):
        assert client.get(f"/models/{other}").status_code == 404, other


def test_a_missing_file_falls_back_with_one_warning_line(tmp_path):
    scene = copy_scene(tmp_path, with_model=False)
    loaded = load(scene)
    assert loaded.meshes == {}
    assert loaded.warnings == [
        "CYL_01: mesh file models/pusher-housing.glb not found; drawing the primitive housing"
    ]
    doc = Runtime(loaded, realtime=False).scene_document()
    assert all("mesh" not in c for c in doc["components"])  # the viewer requests nothing
    run = subprocess.run(
        [sys.executable, "-m", "openfactory.cli", "run", str(scene), "--as-fast-as-possible",
         "--duration", "1", "--script", str(SCRIPT)],
        capture_output=True, text=True, timeout=120, cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(REPO / "src")},
    )  # fmt: skip
    assert run.returncode == 0, run.stderr
    lines = [line for line in (run.stdout + run.stderr).splitlines() if "mesh file" in line]
    assert len(lines) == 1, lines
    assert "WARNING" in lines[0] and "drawing the primitive housing" in lines[0]
    result = CliRunner().invoke(cli, ["validate", str(scene)])
    assert result.exit_code == 0
    assert result.output.count("mesh file models/pusher-housing.glb not found") == 1


@pytest.mark.parametrize(
    "content, why",
    [
        (b"not a glb at all", "is not a binary glTF (.glb) file"),
        (b"glTF" + (1).to_bytes(4, "little") + (12).to_bytes(4, "little"), "glTF version 1"),
        (None, "truncated or padded"),
    ],
)
def test_a_broken_file_falls_back(tmp_path, content, why):
    scene = copy_scene(tmp_path)
    model = tmp_path / "models" / GLB.name
    model.write_bytes(content if content is not None else GLB.read_bytes()[:-8])
    loaded = load(scene)
    assert loaded.meshes == {} and len(loaded.warnings) == 1
    assert why in loaded.warnings[0], loaded.warnings


def test_a_symlink_out_of_the_scene_folder_is_not_served(tmp_path):
    inner = tmp_path / "scene"
    inner.mkdir()
    scene = copy_scene(inner, with_model=False)
    (inner / "models").mkdir()
    outside = tmp_path / "elsewhere.glb"
    shutil.copy(GLB, outside)
    try:
        (inner / "models" / GLB.name).symlink_to(outside)
    except OSError:
        pytest.skip("no symlink support on this filesystem")
    loaded = load(scene)
    assert loaded.meshes == {} and "outside the scene's folder" in loaded.warnings[0]


def test_lint_w209_without_a_license_record(tmp_path):
    scene = copy_scene(tmp_path)
    codes = [f.code for f in lint_scene(scene, [SCRIPT])]
    assert "W209" not in codes  # the sidecar is there
    (tmp_path / "models" / (GLB.name + ".license.txt")).unlink()
    w209 = [f for f in lint_scene(scene, [SCRIPT]) if f.code == "W209"]
    assert len(w209) == 1 and w209[0].level == "warning"
    assert "CYL_01" in w209[0].message and "license.txt" in w209[0].message
    (tmp_path / "THIRD_PARTY.md").write_text(
        "| `models/pusher-housing.glb` | someone | CC0 |\n", encoding="utf-8"
    )
    assert not [f for f in lint_scene(scene, [SCRIPT]) if f.code == "W209"]


def test_the_example_housing_rebuilds_byte_for_byte():
    spec = importlib.util.spec_from_file_location(
        "make_example_mesh", REPO / "tools" / "make_example_mesh.py"
    )
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    assert tool.glb() == GLB.read_bytes()


def test_every_mesh_in_the_repo_is_small_and_licensed():
    spec = importlib.util.spec_from_file_location("third_party", REPO / "tools" / "third_party.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    meshes = tool.model_files()
    assert GLB in meshes
    for mesh in meshes:
        assert mesh.stat().st_size < 1_000_000, mesh  # the 1 MB binary rule (CLAUDE.md)
    lines, problems = tool.models_section()
    assert problems == []
    text = (REPO / "THIRD_PARTY.md").read_text(encoding="utf-8")
    assert text.endswith("\n".join(lines)), "THIRD_PARTY.md: run tools/third_party.py --models"
    attribution = (REPO / "ATTRIBUTION.md").read_text(encoding="utf-8")  # PLAN 4.2, same tool
    assert attribution == tool.attribution_text(), (
        "ATTRIBUTION.md: run tools/third_party.py --models"
    )
    for mesh in meshes:
        assert f"**`{mesh.relative_to(REPO).as_posix()}`** by " in attribution
