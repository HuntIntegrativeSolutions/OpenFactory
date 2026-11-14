"""PLAN 2.18: scene bundles. `openfactory bundle` packs a scene with its meshes, licenses,
lockfiles and script into a reproducible .ofz; `run`, `test` and `validate` open one directly,
after checking every member, and never run its script unless asked."""

from __future__ import annotations

import hashlib
import json
import shutil
import stat
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openfactory import bundle as bundle_mod
from openfactory.bundle import (
    BundleError,
    collect,
    find_script,
    open_bundle,
    read_bundle,
    write_bundle,
)
from openfactory.cli import app
from openfactory.core.loader import build
from openfactory.core.schema import load_scene
from openfactory.protocols.modbus import lock_text, read_lock, scene_register_map

REPO = Path(__file__).resolve().parents[2]
runner = CliRunner()


def sorter_copy(root: Path) -> Path:
    """The shipped sorter, its lockfiles and its script, in the repository's layout."""
    (root / "scenes").mkdir(parents=True)
    (root / "examples" / "pyplc").mkdir(parents=True)
    (root / "openfactory.toml").write_text("", encoding="utf-8")  # the project root
    for path in (REPO / "scenes").glob("sorting-by-height.*"):
        shutil.copy(path, root / "scenes" / path.name)
    shutil.copy(REPO / "examples/pyplc/sorting-by-height.py", root / "examples/pyplc")
    return root / "scenes" / "sorting-by-height.yaml"


def mesh_copy(root: Path) -> Path:
    """The 2.15 mesh scene (float32 lockfile only) with its .glb and license sidecar."""
    shutil.copytree(REPO / "tests" / "scenes" / "models", root / "models")
    for path in (REPO / "tests" / "scenes").glob("sorter-custom-housing.*"):
        shutil.copy(path, root / path.name)
    return root / "sorter-custom-housing.yaml"


# --- Done-when --------------------------------------------------------------------------------


def test_sorter_runs_from_its_bundle_alone(tmp_path, monkeypatch):
    """Bundle the sorter, delete the originals, and grade it from the .ofz: checks pass."""
    scene = sorter_copy(tmp_path / "copy")
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.chdir(out)
    made = runner.invoke(app, ["bundle", str(scene)])
    assert made.exit_code == 0, made.output
    assert "script   scripts/sorting-by-height.py" in made.output
    shutil.rmtree(tmp_path / "copy")
    result = runner.invoke(app, ["test", "sorting-by-height.ofz", "--bundled-script"])
    print(result.output)
    assert result.exit_code == 0
    assert "PASS: 4 of 4 checks passed" in result.output


def test_mesh_scene_loads_its_mesh_from_the_bundle(tmp_path, monkeypatch):
    scene = mesh_copy(tmp_path / "copy")
    target = write_bundle(collect(scene, no_script=True), tmp_path / "mesh.ofz")
    shutil.rmtree(tmp_path / "copy")
    monkeypatch.chdir(tmp_path)
    with open_bundle(target) as opened:
        loaded = build(load_scene(opened.scene_path), base_dir=opened.scene_path.parent)
        mesh = loaded.meshes["CYL_01"]
        assert mesh.is_file() and mesh.is_relative_to(opened.scene_path.parent)
        assert mesh.read_bytes() == (REPO / "tests/scenes/models/pusher-housing.glb").read_bytes()
        assert not loaded.warnings
    result = runner.invoke(app, ["validate", str(target)])
    assert result.exit_code == 0, result.output
    assert "OK   " in result.output and "Modbus float32, int16_scaled" in result.output


# --- what a bundle holds ----------------------------------------------------------------------


def test_bundle_contents_and_manifest(tmp_path):
    scene = mesh_copy(tmp_path)
    contents = collect(scene, script=REPO / "examples/pyplc/sorting-by-height.py")
    target = write_bundle(contents, tmp_path / "x.ofz")
    with zipfile.ZipFile(target) as zf:
        names = zf.namelist()
        assert names[0] == "manifest.json"
        assert names[1:] == sorted(names[1:])
        assert all(i.date_time == (1980, 1, 1, 0, 0, 0) for i in zf.infolist())
        meta = json.loads(zf.read("manifest.json"))
    assert meta["format"] == "openfactory-scene-bundle"
    assert meta["bundle_version"] == 1 and meta["openfactory"] == "0.1"
    assert meta["scene"] == "sorter-custom-housing.yaml"
    assert meta["script"] == "scripts/sorting-by-height.py"
    roles = {f["path"]: f["role"] for f in meta["files"]}
    assert roles == {
        "models/pusher-housing.glb": "mesh",
        "models/pusher-housing.glb.license.txt": "license",
        "scripts/sorting-by-height.py": "script",
        "sorter-custom-housing.float32.modbus.yaml": "lockfile",
        "sorter-custom-housing.int16_scaled.modbus.yaml": "lockfile",
        "sorter-custom-housing.yaml": "scene",
    }
    for entry in meta["files"]:
        data = zipfile.ZipFile(target).read(entry["path"])
        assert entry["bytes"] == len(data)
        assert entry["sha256"] == hashlib.sha256(data).hexdigest()


def test_bundling_twice_gives_the_same_bytes(tmp_path):
    scene = sorter_copy(tmp_path)
    one = write_bundle(collect(scene), tmp_path / "one.ofz").read_bytes()
    two = write_bundle(collect(scene), tmp_path / "two.ofz").read_bytes()
    assert one == two


def test_a_missing_lockfile_is_generated_as_run_would_write_it(tmp_path):
    scene = mesh_copy(tmp_path)  # has float32 only
    int16 = next(f for f in collect(scene).files if "int16_scaled" in f.path)
    assert int16.note == "generated, not on disk"
    parsed = load_scene(scene)
    expected = lock_text(scene_register_map(parsed, "int16_scaled", scene_path=scene, write=False),
                         scene.name)  # fmt: skip
    assert int16.data.decode("utf-8") == expected
    assert not (tmp_path / "sorter-custom-housing.int16_scaled.modbus.yaml").exists()
    float32 = read_lock(tmp_path / "sorter-custom-housing.float32.modbus.yaml")
    generated = tmp_path / "gen.yaml"
    generated.write_bytes(int16.data)
    digital = {n: a for n, a in float32.addresses.items() if n.endswith(("_DI", "_DO"))}
    assert {n: a for n, a in read_lock(generated).addresses.items() if n in digital} == digital


def test_a_stale_lockfile_is_refused(tmp_path):
    scene = sorter_copy(tmp_path)
    lock = scene.with_name("sorting-by-height.float32.modbus.yaml")
    lock.write_text(lock.read_text(encoding="utf-8").replace("CONV_01_RUN_DO", "OLD_RUN_DO"),
                    encoding="utf-8")  # fmt: skip
    with pytest.raises(BundleError, match="does not match the scene.*openfactory export"):
        collect(scene)


def test_meshes_must_exist_and_carry_their_license(tmp_path):
    scene = mesh_copy(tmp_path)
    (tmp_path / "models" / "pusher-housing.glb.license.txt").unlink()
    with pytest.raises(BundleError, match="has no pusher-housing.glb.license.txt beside it"):
        collect(scene)
    (tmp_path / "models" / "pusher-housing.glb").unlink()
    with pytest.raises(BundleError, match="not found"):
        collect(scene)


def test_script_discovery(tmp_path):
    scene = sorter_copy(tmp_path)
    assert find_script(scene) == (tmp_path / "examples/pyplc/sorting-by-height.py").resolve()
    beside = scene.with_suffix(".py")
    beside.write_text("def step(io, t):\n    pass\n", encoding="utf-8")
    assert find_script(scene) == beside
    assert collect(scene, no_script=True).script is None


def test_script_search_stops_at_the_project_root(tmp_path):
    """examples/pyplc/<scene>.py above the project root is never used (ruled 2026-11-13; the
    same bound as library composites, PLAN 4.1c fix 5)."""
    project = tmp_path / "project"
    scene = sorter_copy(project)
    (project / "examples").rename(tmp_path / "examples")  # the script now sits above the root
    assert find_script(scene) is None


# --- opening an untrusted bundle --------------------------------------------------------------


def crafted(path: Path, members: dict[str, bytes], *, manifest: dict | None = None,
            listed: dict[str, bytes] | None = None, version: int = 1) -> Path:  # fmt: skip
    """A zip with ``members``; the manifest lists ``listed`` (default: the members)."""
    listed = members if listed is None else listed
    meta = manifest or {
        "format": "openfactory-scene-bundle",
        "bundle_version": version,
        "openfactory": "0.1",
        "scene": "s.yaml",
        "script": None,
        "files": [
            {"path": n, "role": "scene", "bytes": len(d), "sha256": hashlib.sha256(d).hexdigest()}
            for n, d in listed.items()
        ],
    }
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(meta))
        for name, data in members.items():
            zf.writestr(name, data)
    return path


SCENE = b"openfactory: '0.1'\n"


@pytest.mark.parametrize(
    "name",
    ["../evil.txt", "/etc/evil.txt", "a/../../evil.txt", "a\\evil.txt", "C:evil.txt", "a//b.txt",
     "./s.txt"],
)  # fmt: skip
def test_unsafe_paths_are_refused_before_anything_is_written(tmp_path, name):
    (tmp_path / "b").mkdir()
    target = crafted(tmp_path / "b" / "x.ofz", {"s.yaml": SCENE, name: b"x"})
    with pytest.raises(BundleError, match="not a plain relative path"), open_bundle(target):
        pass
    assert not (tmp_path / "evil.txt").exists() and not Path("/etc/evil.txt").exists()


def test_tampered_unlisted_and_missing_members_are_refused(tmp_path):
    good = {"s.yaml": SCENE}
    tampered = crafted(tmp_path / "t.ofz", {"s.yaml": b"openfactory: '0.2'\n"}, listed=good)
    with pytest.raises(BundleError, match="does not match its SHA-256"):
        read_bundle(tampered)
    extra = crafted(tmp_path / "e.ofz", {"s.yaml": SCENE, "more.py": b"x"}, listed=good)
    with pytest.raises(BundleError, match="more.py is in the bundle but not in the manifest"):
        read_bundle(extra)
    missing = crafted(tmp_path / "m.ofz", {}, listed=good)
    with pytest.raises(BundleError, match="in the manifest but not in the bundle"):
        read_bundle(missing)


def test_size_lies_links_and_limits_are_refused(tmp_path, monkeypatch):
    big = b"y" * 5000
    entry = {"path": "s.yaml", "role": "scene", "bytes": 10,
             "sha256": hashlib.sha256(big).hexdigest()}  # fmt: skip
    meta = {"format": "openfactory-scene-bundle", "bundle_version": 1, "scene": "s.yaml",
            "script": None, "files": [entry]}  # fmt: skip
    with pytest.raises(BundleError, match="11 bytes, the manifest says 10"):
        read_bundle(crafted(tmp_path / "lie.ofz", {"s.yaml": big}, manifest=meta))

    target = b"/etc/passwd"
    link_entry = {"path": "l.yaml", "role": "mesh", "bytes": len(target),
                  "sha256": hashlib.sha256(target).hexdigest()}  # fmt: skip
    meta = {"format": "openfactory-scene-bundle", "bundle_version": 1, "scene": "l.yaml",
            "script": None, "files": [link_entry]}  # fmt: skip
    link = tmp_path / "link.ofz"
    with zipfile.ZipFile(link, "w") as zf:
        zf.writestr("manifest.json", json.dumps(meta))
        info = zipfile.ZipInfo("l.yaml")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, target)
    with pytest.raises(BundleError, match="l.yaml is not a regular file"):
        read_bundle(link)

    monkeypatch.setattr(bundle_mod, "MAX_TOTAL_BYTES", 10)
    with pytest.raises(BundleError, match="over 10 bytes unpacked"):
        read_bundle(crafted(tmp_path / "total.ofz", {"s.yaml": SCENE}))
    monkeypatch.setattr(bundle_mod, "MAX_MEMBERS", 1)
    with pytest.raises(BundleError, match="the limit is 1"):
        read_bundle(crafted(tmp_path / "count.ofz", {"s.yaml": SCENE}))


def test_not_a_bundle_and_newer_versions(tmp_path):
    (tmp_path / "plain.ofz").write_text("hello", encoding="utf-8")
    with pytest.raises(BundleError, match="is not a scene bundle"):
        read_bundle(tmp_path / "plain.ofz")
    with zipfile.ZipFile(tmp_path / "bare.ofz", "w") as zf:
        zf.writestr("s.yaml", SCENE)
    with pytest.raises(BundleError, match="no manifest.json"):
        read_bundle(tmp_path / "bare.ofz")
    with pytest.raises(BundleError, match="bundle version 2, made by a newer OpenFactory"):
        read_bundle(crafted(tmp_path / "v2.ofz", {"s.yaml": SCENE}, version=2))


# --- the script is code: it runs only when asked for ------------------------------------------

MARKER_SCRIPT = """\
from pathlib import Path

Path({marker!r}).write_text("ran", encoding="utf-8")


def step(io, t):
    pass
"""


def test_a_bundled_script_never_runs_unless_asked(tmp_path, monkeypatch):
    scene = sorter_copy(tmp_path / "copy")
    marker = tmp_path / "ran.txt"
    script = tmp_path / "copy" / "evil.py"
    script.write_text(MARKER_SCRIPT.format(marker=str(marker)), encoding="utf-8")
    target = write_bundle(collect(scene, script=script), tmp_path / "s.ofz")
    monkeypatch.chdir(tmp_path)

    for args in (
        ["validate", str(target)],
        ["run", str(target), "--as-fast-as-possible", "--duration", "0.1"],
        ["test", str(target), "--script", str(REPO / "examples/pyplc/sorting-by-height.py")],
    ):
        result = runner.invoke(app, args)
        assert result.exit_code in (0, 1), result.output
        assert not marker.exists(), args
    neither = runner.invoke(app, ["test", str(target)])
    assert neither.exit_code == 2 and "--bundled-script" in neither.output
    assert not marker.exists()

    asked = runner.invoke(app, ["run", str(target), "--as-fast-as-possible", "--duration", "0.1",
                                "--bundled-script"])  # fmt: skip
    assert asked.exit_code == 0, asked.output
    assert marker.read_text(encoding="utf-8") == "ran"


def test_bundled_script_flag_errors(tmp_path):
    scene = sorter_copy(tmp_path)
    no_script = write_bundle(collect(scene, no_script=True), tmp_path / "n.ofz")
    plain = runner.invoke(app, ["test", str(scene), "--bundled-script"])
    assert plain.exit_code == 2 and "is not a .ofz bundle" in plain.output
    empty = runner.invoke(app, ["test", str(no_script), "--bundled-script"])
    assert empty.exit_code == 1 and "the bundle carries no script" in empty.output
    both = runner.invoke(app, ["run", str(no_script), "--bundled-script", "--script", "x.py"])
    assert both.exit_code == 2 and "not both" in both.output


def test_the_unpacked_folder_is_removed_when_the_command_ends(tmp_path, monkeypatch):
    scene = sorter_copy(tmp_path / "copy")
    target = write_bundle(collect(scene), tmp_path / "s.ofz")
    made: list[Path] = []
    real = bundle_mod.tempfile.mkdtemp

    def spy(**kwargs):
        made.append(Path(real(**kwargs)))
        return str(made[-1])

    monkeypatch.setattr(bundle_mod.tempfile, "mkdtemp", spy)
    result = runner.invoke(app, ["test", str(target), "--bundled-script"])
    assert result.exit_code == 0, result.output
    assert len(made) == 1 and not made[0].exists()
