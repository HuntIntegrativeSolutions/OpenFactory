"""Scene bundles (PLAN 2.18): one ``.ofz`` file that carries a scene and everything it needs.

Behavior spec
-------------
``openfactory bundle scene.yaml`` writes ``scene.ofz``, a zip holding:

- the scene file, at the top level under its own name (composites are declared inside it);
- every custom mesh the scene references (PLAN 2.15), at the same relative path, each with its
  ``<file>.license.txt`` sidecar. A mesh with no sidecar is refused: a mesh that travels must
  carry its license, and a ``THIRD_PARTY.md`` entry (which lint W209 also accepts) stays behind;
- for a Modbus scene, both address lockfiles (ADR-0004). A lockfile on disk must already match the
  scene (it is never changed by bundling); a missing one is generated exactly as ``run`` or
  ``export`` would write it, seeded from the other encoding's digital addresses;
- the stand-in PLC script (PLAN 2.10), if any: ``--script F.py``, else ``<scene>.py`` beside the
  scene, else ``examples/pyplc/<scene>.py`` in the nearest folder above the scene that has one;
  ``--no-script`` leaves it out. It is stored as ``scripts/<file>``;
- ``manifest.json``: the format name, bundle version, the scene's schema version
  (``openfactory: "0.1"``), the scene and script paths, and every file with its role, size and
  SHA-256.

The zip is reproducible: entries sorted, manifest first, a fixed 1980-01-01 timestamp, fixed
permissions and compression, and no build time in the manifest, so bundling the same files twice
gives the same bytes.

``run``, ``test`` and ``validate`` accept a ``.ofz``: :func:`open_bundle` checks it and unpacks it
into a temporary folder that lives until the command ends (the viewer serves meshes from it). A
bundle is untrusted input, since it arrives by email, so before anything is written every member
must be listed in the manifest with a matching size and SHA-256, have a plain relative path (no
absolute path, ``..``, backslash, drive letter or empty part), not be a link, and fit the member
count and size limits. A run that updates a lockfile updates the unpacked copy; the ``.ofz`` is
never rewritten. The bundled script is Python and runs only when asked for with
``--bundled-script``; opening a bundle never executes it.
"""

from __future__ import annotations

import hashlib
import io
import json
import posixpath
import shutil
import stat
import tempfile
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import get_args

from openfactory import __version__
from openfactory.core.loader import glb_problem
from openfactory.core.schema import Scene, load_scene
from openfactory.protocols.modbus import (
    LOCKFILE_SUFFIX,
    Encoding,
    lock_text,
    lockfile_path,
    scene_register_map,
)

BUNDLE_SUFFIX = ".ofz"
FORMAT = "openfactory-scene-bundle"
BUNDLE_VERSION = 1
MANIFEST = "manifest.json"
SCRIPT_DIR = "scripts"
LICENSE_SUFFIX = ".license.txt"

MAX_MEMBERS = 1000
MAX_MEMBER_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024

_DATE = (1980, 1, 1, 0, 0, 0)
_MODE = 0o100644 << 16  # a regular file, rw-r--r--
ROLES = ("scene", "lockfile", "mesh", "license", "script")


class BundleError(ValueError):
    """A scene that cannot be bundled, or a ``.ofz`` that is not a sound bundle."""


@dataclass(frozen=True)
class BundleFile:
    """One file in a bundle: its path inside the zip, role, bytes, and a note for the listing."""

    path: str
    role: str
    data: bytes
    note: str = ""

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


@dataclass
class Contents:
    """What :func:`collect` gathered: the files, the script path inside the bundle, and notes."""

    scene: str
    scene_version: str
    script: str | None
    files: list[BundleFile] = field(default_factory=list)


@dataclass(frozen=True)
class OpenedBundle:
    """An unpacked bundle: the scene file in the temporary folder, and the script if carried."""

    source: Path
    scene_path: Path
    script: Path | None
    manifest: dict


def is_bundle(path: str | Path) -> bool:
    return Path(path).suffix.lower() == BUNDLE_SUFFIX


# --- writing -------------------------------------------------------------------------------------


def find_script(scene_path: Path) -> Path | None:
    """``<stem>.py`` beside the scene, else ``examples/pyplc/<stem>.py`` in the nearest folder at
    or above the scene's folder that has one."""
    beside = scene_path.with_suffix(".py")
    if beside.is_file():
        return beside
    for folder in scene_path.resolve().parents:
        candidate = folder / "examples" / "pyplc" / f"{scene_path.stem}.py"
        if candidate.is_file():
            return candidate
    return None


def _lockfiles(scene: Scene, scene_path: Path) -> list[BundleFile]:
    out: list[BundleFile] = []
    for encoding in get_args(Encoding):
        path = lockfile_path(scene_path, encoding)
        register_map = scene_register_map(scene, encoding, scene_path=scene_path, write=False)
        text = lock_text(register_map, scene_path.name)
        if path.is_file():
            if path.read_text(encoding="utf-8") != text:
                raise BundleError(
                    f"{path.name} does not match the scene (a tag was added, removed or "
                    "re-pinned since it was written). Run `openfactory export "
                    f"{scene_path} --analog-encoding {encoding}` to update it, check the "
                    "change, then bundle again"
                )
            out.append(BundleFile(path.name, "lockfile", path.read_bytes()))
        else:
            out.append(
                BundleFile(path.name, "lockfile", text.encode("utf-8"), "generated, not on disk")
            )
    return out


def _meshes(scene: Scene, scene_path: Path) -> list[BundleFile]:
    root = scene_path.parent.resolve()
    out: dict[str, BundleFile] = {}
    for comp in scene.components:
        if comp.mesh is None:
            continue
        name = posixpath.normpath(comp.mesh.file)
        path = (root / name).resolve()
        if not path.is_relative_to(root):
            raise BundleError(f"{comp.id}: mesh {comp.mesh.file} is outside the scene's folder")
        if not path.is_file():
            raise BundleError(f"{comp.id}: mesh {comp.mesh.file} not found; nothing to bundle")
        why = glb_problem(path)
        if why:
            raise BundleError(f"{comp.id}: mesh {comp.mesh.file} {why}")
        sidecar = path.with_name(path.name + LICENSE_SUFFIX)
        if not sidecar.is_file():
            raise BundleError(
                f"{comp.id}: mesh {comp.mesh.file} has no {sidecar.name} beside it; a bundled "
                "mesh carries its license (Author:, License: and Source: lines)"
            )
        if name not in out:
            out[name] = BundleFile(name, "mesh", path.read_bytes())
            out[name + LICENSE_SUFFIX] = BundleFile(
                name + LICENSE_SUFFIX, "license", sidecar.read_bytes()
            )
    return list(out.values())


def collect(
    scene_path: str | Path, *, script: str | Path | None = None, no_script: bool = False
) -> Contents:
    """Everything a bundle of ``scene_path`` holds; raises :class:`BundleError` or the loader's
    errors for a scene that does not load."""
    scene_path = Path(scene_path)
    if scene_path.name.endswith(LOCKFILE_SUFFIX):
        raise BundleError(f"{scene_path} is a Modbus address lockfile, not a scene")
    scene = load_scene(scene_path)
    files = [BundleFile(scene_path.name, "scene", scene_path.read_bytes())]
    if scene.plc.protocol == "modbus_tcp":
        files += _lockfiles(scene, scene_path)
    files += _meshes(scene, scene_path)
    script_path = None if no_script else (Path(script) if script else find_script(scene_path))
    inner = None
    if script_path is not None:
        if not script_path.is_file():
            raise BundleError(f"script {script_path} not found")
        inner = f"{SCRIPT_DIR}/{script_path.name}"
        files.append(BundleFile(inner, "script", script_path.read_bytes(), str(script_path)))
    names = [f.path for f in files]
    clash = sorted({n for n in names if names.count(n) > 1})
    if clash:
        raise BundleError(f"two files would share the bundle path {clash[0]}")
    return Contents(scene_path.name, scene.openfactory, inner, files)


def manifest(contents: Contents) -> dict:
    return {
        "format": FORMAT,
        "bundle_version": BUNDLE_VERSION,
        "openfactory": contents.scene_version,
        "created_by": f"openfactory {__version__}",
        "scene": contents.scene,
        "script": contents.script,
        "files": [
            {"path": f.path, "role": f.role, "bytes": len(f.data), "sha256": f.sha256}
            for f in sorted(contents.files, key=lambda f: f.path)
        ],
    }


def _info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=_DATE)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = _MODE
    return info


def bundle_bytes(contents: Contents) -> bytes:
    """The ``.ofz`` for ``contents``, byte for byte reproducible."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        text = json.dumps(manifest(contents), indent=2) + "\n"
        zf.writestr(_info(MANIFEST), text.encode("utf-8"), compresslevel=9)
        for f in sorted(contents.files, key=lambda f: f.path):
            zf.writestr(_info(f.path), f.data, compresslevel=9)
    return buffer.getvalue()


def write_bundle(contents: Contents, out: str | Path) -> Path:
    out = Path(out)
    out.write_bytes(bundle_bytes(contents))
    return out


# --- reading -------------------------------------------------------------------------------------


def _safe_path(name: object) -> str:
    if not isinstance(name, str) or not name or len(name) > 255:
        raise BundleError(f"bundle path {name!r} is not a plain relative path")
    pure = PurePosixPath(name)
    bad = (
        "\\" in name
        or ":" in name
        or "\x00" in name
        or name.startswith("/")
        or pure.as_posix() != name
        or any(part in ("", ".", "..") for part in name.split("/"))
    )
    if bad:
        raise BundleError(f"bundle path {name!r} is not a plain relative path")
    return name


def _read_member(zf: zipfile.ZipFile, info: zipfile.ZipInfo, size: int) -> bytes:
    with zf.open(info) as f:
        data = f.read(size + 1)  # never trust the header's size alone
    if len(data) != size:
        raise BundleError(f"{info.filename}: {len(data)} bytes, the manifest says {size}")
    return data


def read_bundle(path: str | Path) -> tuple[dict, dict[str, bytes]]:
    """Check a ``.ofz`` and return its manifest and files; raises :class:`BundleError`."""
    path = Path(path)
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise BundleError(f"{path} is not a scene bundle: {exc}") from None
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_MEMBERS:
            raise BundleError(f"{path}: {len(infos)} members; the limit is {MAX_MEMBERS}")
        by_name: dict[str, zipfile.ZipInfo] = {}
        for info in infos:
            if info.filename in by_name:
                raise BundleError(f"{path}: {info.filename} appears twice")
            by_name[info.filename] = info
        if MANIFEST not in by_name:
            raise BundleError(f"{path} is not a scene bundle (no {MANIFEST})")
        raw = _read_member(zf, by_name[MANIFEST], min(by_name[MANIFEST].file_size, 1 << 20))
        try:
            meta = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BundleError(f"{path}: {MANIFEST} is not JSON: {exc}") from None
        _check_manifest(path, meta)
        files: dict[str, bytes] = {}
        total = 0
        for entry in meta["files"]:
            name = _safe_path(entry.get("path"))
            if name == MANIFEST or name in files:
                raise BundleError(f"{path}: {name} is listed twice in the manifest")
            info = by_name.get(name)
            if info is None:
                raise BundleError(f"{path}: {name} is in the manifest but not in the bundle")
            kind = (info.external_attr >> 16) & 0o170000
            if info.is_dir() or kind not in (0, stat.S_IFREG):
                raise BundleError(f"{path}: {name} is not a regular file")
            size = entry.get("bytes")
            if not isinstance(size, int) or size < 0 or size > MAX_MEMBER_BYTES:
                raise BundleError(f"{path}: {name} size {size!r} is over the limit or invalid")
            total += size
            if total > MAX_TOTAL_BYTES:
                raise BundleError(f"{path}: over {MAX_TOTAL_BYTES} bytes unpacked")
            data = _read_member(zf, info, size)
            if hashlib.sha256(data).hexdigest() != entry.get("sha256"):
                raise BundleError(f"{path}: {name} does not match its SHA-256 in the manifest")
            files[name] = data
        extra = sorted(set(by_name) - set(files) - {MANIFEST})
        if extra:
            raise BundleError(f"{path}: {extra[0]} is in the bundle but not in the manifest")
    if meta["scene"] not in files or "/" in meta["scene"]:
        raise BundleError(f"{path}: the scene {meta['scene']!r} is not a top-level file in it")
    if meta.get("script") is not None and meta["script"] not in files:
        raise BundleError(f"{path}: the script {meta['script']!r} is not in the bundle")
    return meta, files


def _check_manifest(path: Path, meta: object) -> None:
    if not isinstance(meta, dict) or meta.get("format") != FORMAT:
        raise BundleError(f"{path}: {MANIFEST} is not an OpenFactory scene bundle manifest")
    version = meta.get("bundle_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise BundleError(f"{path}: bundle_version {version!r} is not valid")
    if version > BUNDLE_VERSION:
        raise BundleError(
            f"{path} is bundle version {version}, made by a newer OpenFactory; this one reads "
            f"version {BUNDLE_VERSION}. Upgrade OpenFactory to open it"
        )
    if not isinstance(meta.get("files"), list) or not isinstance(meta.get("scene"), str):
        raise BundleError(f"{path}: {MANIFEST} has no files list or scene")
    script = meta.get("script")
    if script is not None and not isinstance(script, str):
        raise BundleError(f"{path}: {MANIFEST} script must be a path or null")


@contextmanager
def open_bundle(path: str | Path) -> Iterator[OpenedBundle]:
    """Check and unpack a ``.ofz`` into a temporary folder that is removed on exit."""
    meta, files = read_bundle(path)
    folder = Path(tempfile.mkdtemp(prefix="openfactory-bundle-"))
    try:
        root = folder.resolve()
        for name, data in files.items():
            target = (root / name).resolve()
            if not target.is_relative_to(root):  # _safe_path already rules this out
                raise BundleError(f"{path}: {name} would unpack outside the bundle folder")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        script = root / meta["script"] if meta.get("script") else None
        yield OpenedBundle(Path(path), root / meta["scene"], script, meta)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
