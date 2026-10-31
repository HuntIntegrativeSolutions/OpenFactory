"""Build or check a demo kit, ``kits/<scene>/`` (PLAN 2.14).

Usage::

    uv run python tools/build_kit.py                       # rebuild every kit under kits/
    uv run python tools/build_kit.py sorting-by-height      # build (or create) one kit
    uv run python tools/build_kit.py --check                # exit 1 if a kit is stale

A kit is everything a client demo of one scene needs, in one folder. This tool regenerates every
part code can produce (ruled 2026-10-30):

- the scene and its committed lockfiles, copied from ``scenes/``;
- the OpenPLC program (``examples/openplc/<scene>.st``) and the stand-in script
  (``examples/pyplc/<scene>.py``), copied;
- the I/O list CSV for each analog encoding that has a lockfile (``export``);
- the Studio 5000 controller tag stubs (``export --format l5x``, PLAN 2.3);
- the Ignition 8.3 tag JSON (``export --format ignition``, PLAN 2.4);
- a 60 s recording under the stand-in script (``test --script --record``, PLAN 2.12; the
  build fails if the scene's checks do), replayable with no scene file and identical on every
  build (``--check`` compares it decompressed);
- ``README.md``, the kit's index, listing every file and the commands to use it.

Files only the HIS Controls skills can make (the full L5X from ``/io-to-l5x``, the Perspective
view from ``/perspective-view``, the GRAFCET chart from ``/grafcet-chart-creator``) are not
generated here. They are committed into the kit by hand, each starting with the line
``generated, pending PE review`` (inside the file format's comment syntax where it has one; a
JSON file has none, so there the text goes in a field near the top), and the README lists them
as present or missing. The tool never deletes or rewrites them.

``--check`` builds into a temporary folder and compares it byte for byte with ``kits/``; CI runs
it, so a change to a scene, a program, or an exporter that is not rebuilt into its kit fails.
The tool reads only this repository and writes only under ``kits/``.
"""

from __future__ import annotations

import argparse
import filecmp
import gzip
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
KITS = REPO / "kits"
PE_HEADER = "generated, pending PE review"
#: Skill-made files a kit may hold: file name pattern -> what makes it.
SKILL_FILES = {
    "{scene}-full.L5X": "`/io-to-l5x` on the I/O CSV (PlantPAx AOI instances)",
    "{scene}-perspective.json": "`/perspective-view` (Ignition Perspective demo screen)",
    "{scene}-grafcet.html": "`/grafcet-chart-creator` on the OpenPLC program (PLAN 2.8)",
}
ENCODING_SUFFIX = {"float32": "", "int16_scaled": "-int16"}
RECORD_SECONDS = 60


def openfactory(*args: str) -> None:
    done = subprocess.run(
        [sys.executable, "-m", "openfactory", *args], cwd=REPO, capture_output=True, text=True
    )
    if done.returncode != 0:
        raise SystemExit(f"openfactory {' '.join(args)} failed:\n{done.stdout}{done.stderr}")


def generated(scene: str) -> list[str]:
    """The file names this tool writes for ``scene``, in README order."""
    names = [f"{scene}.yaml"]
    encodings = [e for e in ENCODING_SUFFIX if lock(scene, e).is_file()]
    names += [f"{scene}.{e}.modbus.yaml" for e in encodings]
    names += [f"{scene}.st", f"{scene}.py"]
    names += [f"{scene}-io{ENCODING_SUFFIX[e]}.csv" for e in encodings]
    names += [f"{scene}-tags.L5X", f"{scene}-ignition-tags.json", f"{scene}.ofrec", "README.md"]
    return names


def lock(scene: str, encoding: str) -> Path:
    return REPO / "scenes" / f"{scene}.{encoding}.modbus.yaml"


def build(scene: str, out: Path) -> None:
    """Write every generated file of ``scene``'s kit into ``out``."""
    src = REPO / "scenes" / f"{scene}.yaml"
    if not src.is_file():
        raise SystemExit(f"no scene scenes/{scene}.yaml")
    out.mkdir(parents=True, exist_ok=True)
    for path in (
        src,
        *(lock(scene, e) for e in ENCODING_SUFFIX),
        REPO / "examples" / "openplc" / f"{scene}.st",
        REPO / "examples" / "pyplc" / f"{scene}.py",
    ):
        if path.is_file():
            shutil.copyfile(path, out / path.name)
    for enc, suffix in ENCODING_SUFFIX.items():
        if lock(scene, enc).is_file():
            csv = out / f"{scene}-io{suffix}.csv"
            openfactory("export", str(src), "--analog-encoding", enc, "-o", str(csv))
    openfactory("export", str(src), "--format", "l5x", "-o", str(out / f"{scene}-tags.L5X"))
    openfactory(
        "export", str(src), "--format", "ignition", "-o", str(out / f"{scene}-ignition-tags.json")
    )
    # The recording runs a copy of the scene in a scratch folder, so `run` cannot touch scenes/.
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp) / src.name
        shutil.copyfile(src, scratch)
        for enc in ENCODING_SUFFIX:
            if lock(scene, enc).is_file():
                shutil.copyfile(lock(scene, enc), Path(tmp) / lock(scene, enc).name)
        # `test` serves no protocol, and fails the build if the scene's checks fail
        openfactory(
            "test", str(scratch), "--script", str(REPO / "examples" / "pyplc" / f"{scene}.py"),
            "--duration", str(RECORD_SECONDS), "--record", str(out / f"{scene}.ofrec"), "-q",
        )  # fmt: skip
    (out / "README.md").write_text(readme(scene, out), encoding="utf-8")


def skill_status(scene: str, folder: Path) -> list[tuple[str, str, str]]:
    rows = []
    for pattern, maker in SKILL_FILES.items():
        name = pattern.format(scene=scene)
        path = folder / name
        if not path.is_file():
            rows.append((name, maker, "missing"))
        elif PE_HEADER in path.read_text(encoding="utf-8", errors="replace")[:300]:
            rows.append((name, maker, "present, pending PE review"))
        else:
            rows.append((name, maker, f"present, **no `{PE_HEADER}` header line**"))
    return rows


def readme(scene: str, folder: Path) -> str:
    """The kit index. ``folder`` is where the skill-made files are looked for."""
    files = {
        f"{scene}.yaml": "the scene (copied from `scenes/`)",
        f"{scene}.float32.modbus.yaml": "Modbus address lockfile, float32 analogs (ADR-0004)",
        f"{scene}.int16_scaled.modbus.yaml": "Modbus address lockfile, int16_scaled (OpenPLC)",
        f"{scene}.st": "OpenPLC program (copied from `examples/openplc/`)",
        f"{scene}.py": "stand-in PLC script (copied from `examples/pyplc/`)",
        f"{scene}-io.csv": "I/O list, float32 Modbus addresses",
        f"{scene}-io-int16.csv": "I/O list, int16_scaled Modbus addresses (OpenPLC)",
        f"{scene}-tags.L5X": "Studio 5000 controller tag stubs (PLAN 2.3)",
        f"{scene}-ignition-tags.json": "Ignition 8.3 tags, a UDT instance per component (PLAN 2.4)",
        f"{scene}.ofrec": f"a {RECORD_SECONDS} s recording under the stand-in script (PLAN 2.12)",
        "README.md": "this index",
    }
    lines = [
        f"# Demo kit: {scene}",
        "",
        "Generated by `tools/build_kit.py` (PLAN 2.14); do not edit the generated files by hand,",
        "rebuild them. OpenFactory is a training and demo tool, not a safety-rated simulation.",
        "",
        "## Generated files",
        "",
        "| File | What it is |",
        "|------|------------|",
    ]
    lines += [f"| `{name}` | {files[name]} |" for name in generated(scene)]
    lines += [
        "",
        "## Skill-made files (committed by hand, each pending PE review)",
        "",
        "| File | Made by | Status |",
        "|------|---------|--------|",
    ]
    lines += [f"| `{n}` | {m} | {s} |" for n, m, s in skill_status(scene, folder)]
    lines += [
        "",
        "## Use it",
        "",
        "```bash",
        f"uv run openfactory run kits/{scene}/{scene}.yaml --script kits/{scene}/{scene}.py",
        f"uv run openfactory replay kits/{scene}/{scene}.ofrec      # no scene or PLC needed",
        f"uv run openfactory run kits/{scene}/{scene}.yaml --opcua  # Ignition: docs/ignition.md",
        "```",
        "",
        f"With OpenPLC, load `{scene}.st` and configure its Slave Device from",
        f"`{scene}-io-int16.csv` (docs/guides/openplc-quickstart.md).",
        "",
    ]
    return "\n".join(lines)


def same(a: Path, b: Path) -> bool:
    """Byte-equal; a recording is compared decompressed, because another zlib build (another
    machine, CI) may deflate the same content to different bytes."""
    if a.suffix == ".ofrec":
        return gzip.decompress(a.read_bytes()) == gzip.decompress(b.read_bytes())
    return filecmp.cmp(a, b, shallow=False)


def kit_names() -> list[str]:
    return sorted(p.name for p in KITS.iterdir() if p.is_dir()) if KITS.is_dir() else []


def check(scene: str) -> list[str]:
    """Differences between ``kits/<scene>`` and a fresh build (empty when current)."""
    kit = KITS / scene
    with tempfile.TemporaryDirectory() as tmp:
        fresh = Path(tmp) / scene
        build(scene, fresh)
        (fresh / "README.md").write_text(readme(scene, kit), encoding="utf-8")
        problems = []
        for name in generated(scene):
            if not (kit / name).is_file():
                problems.append(f"{scene}: {name} missing")
            elif not same(kit / name, fresh / name):
                problems.append(f"{scene}: {name} is stale")
        skill = {pattern.format(scene=scene) for pattern in SKILL_FILES}
        extra = sorted(p.name for p in kit.iterdir() if p.name not in set(generated(scene)) | skill)
        problems += [f"{scene}: {name} is not a kit file" for name in extra]
        problems += [
            f"{scene}: {n} has no '{PE_HEADER}' header line"
            for n, _, s in skill_status(scene, kit)
            if "no `" in s
        ]
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build or check the demo kits (PLAN 2.14).")
    parser.add_argument("scenes", nargs="*", help="scene names (default: every kit in kits/)")
    parser.add_argument("--check", action="store_true", help="exit 1 if a kit is stale")
    args = parser.parse_args(argv)
    scenes = args.scenes or kit_names()
    if not scenes:
        print("no kits; name a scene, e.g. build_kit.py sorting-by-height", file=sys.stderr)
        return 2
    if args.check:
        problems = [p for scene in scenes for p in check(scene)]
        for p in problems:
            print(p, file=sys.stderr)
        if problems:
            print("rebuild with: uv run python tools/build_kit.py", file=sys.stderr)
            return 1
        print(f"kits current: {', '.join(scenes)}")
        return 0
    for scene in scenes:
        build(scene, KITS / scene)
        print(f"built kits/{scene}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
