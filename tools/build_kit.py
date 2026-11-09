"""Build or check a demo kit, ``kits/<scene>/`` (PLAN 2.14).

Usage::

    uv run python tools/build_kit.py                       # rebuild every kit under kits/
    uv run python tools/build_kit.py sorting-by-height      # build (or create) one kit
    uv run python tools/build_kit.py --check                # exit 1 if a kit is stale

A kit is everything a client demo of one scene needs, in one folder. This tool regenerates every
part code can produce (ruled 2026-10-30):

- the scene and its committed lockfiles, copied from ``scenes/`` (or ``scenes/examples/``);
- every library composite the scene names, copied to ``_library/`` beside it (PLAN 2.5b), so
  the kit loads with no ``scenes/`` folder around it;
- the OpenPLC program (``examples/openplc/<scene>.st``) and the stand-in script
  (``examples/pyplc/<scene>.py``, or the script beside the scene), copied;
- the I/O list CSV for each analog encoding that has a lockfile (``export``);
- the Studio 5000 controller tag stubs (``export --format l5x``, PLAN 2.3);
- the Ignition 8.3 tag JSON (``export --format ignition``, PLAN 2.4);
- the GRAFCET chart of the OpenPLC program (``tools/grafcet_from_st.py``, PLAN 2.8), for the
  scenes that tool knows; charts are generated, never hand-edited (ruled 2026-10-31);
- for a scene with a PlantPAx map (``openfactory.export.plantpax.MAPS``): the PlantPAx demo L5X
  (``export --format plantpax``, PLAN 3.4), its Ignition UDTs and its Perspective faceplates
  (``--format plantpax-udts`` and ``plantpax-faceplates``, PLAN 3.8);
- a 60 s recording under the stand-in script (``test --script --record``, PLAN 2.12; the
  build fails if the scene's checks do), replayable with no scene file and identical on every
  build (``--check`` compares it decompressed);
- ``<scene>.CONTEXT.md`` (PLAN 2.19), written by those exports, which run on the kit's own copy
  of the scene so the context names the kit's files;
- ``README.md``, the kit's index, listing every file and the commands to use it.

A kit's L5X is the tag stub file; full controller programs are the user's own work, outside
this repository (ruled 2026-10-31). The one file made outside this tool is a Perspective view
(``<scene>-perspective.json``, following the ``/perspective-view`` skill's method), committed by
hand with the text ``generated, pending PE review`` in its first 300 characters (JSON has no
comment syntax, so a field near the top carries it). The README lists it when it is present,
with what it needs before client use, and never lists anything as missing. The tool never
deletes or rewrites it.

``--check`` builds into a temporary folder and compares it byte for byte with ``kits/``; CI runs
it, so a change to a scene, a program, or an exporter that is not rebuilt into its kit fails.
It also fails when a scene's OpenPLC program changed since its GRAFCET sequence data was last
edited (the program hash stored beside the sequence, ``grafcet_from_st.stale``): rebuilding
alone cannot fix a chart whose sequence was read from an older program.
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
sys.path.insert(0, str(REPO / "tools"))
import grafcet_from_st as grafcet  # noqa: E402 - a sibling tool, not a package

from openfactory.core.schema import load_scene  # noqa: E402
from openfactory.export.plantpax import MAPS as PLANTPAX  # noqa: E402

KITS = REPO / "kits"
PE_HEADER = "generated, pending PE review"
#: Files made outside this tool that a kit may hold: file name pattern -> what makes it.
SKILL_FILES = {
    "{scene}-perspective.json": "the `/perspective-view` method, by hand (Perspective faceplate)",
}
#: What a Perspective view still needs before client use (PLAN 2.14 ruling, 2026-10-31).
PERSPECTIVE_OPEN = [
    "Load the view in an Ignition Designer: every binding resolves, no red or orange indicators.",
    "Component-level role gating on START/STOP (today a role check inside the scripts).",
    "Confirm the gateway role names (Operator, Engineer) and set write permissions on the tags.",
    "Callers pass `tagPath` and `equipmentName`; the defaults point at the demo's CONV_01.",
    "HIS Controls PE review (the view is marked generated, pending PE review).",
]
ENCODING_SUFFIX = {"float32": "", "int16_scaled": "-int16"}
RECORD_SECONDS = 60


def openfactory(*args: str) -> None:
    done = subprocess.run(
        [sys.executable, "-m", "openfactory", *args], cwd=REPO, capture_output=True, text=True
    )
    if done.returncode != 0:
        raise SystemExit(f"openfactory {' '.join(args)} failed:\n{done.stdout}{done.stderr}")


def source(scene: str) -> Path:
    """The scene file: ``scenes/<scene>.yaml``, else ``scenes/examples/<scene>.yaml``."""
    for folder in (REPO / "scenes", REPO / "scenes" / "examples"):
        if (folder / f"{scene}.yaml").is_file():
            return folder / f"{scene}.yaml"
    raise SystemExit(f"no scene scenes/{scene}.yaml or scenes/examples/{scene}.yaml")


def script(scene: str) -> Path:
    """The stand-in script: ``examples/pyplc/<scene>.py``, else the one beside the scene."""
    shipped = REPO / "examples" / "pyplc" / f"{scene}.py"
    return shipped if shipped.is_file() else source(scene).with_suffix(".py")


def program(scene: str) -> Path:
    return REPO / "examples" / "openplc" / f"{scene}.st"


def libraries(scene: str) -> list[Path]:
    """The library composite files the scene names (PLAN 2.5b), in the order it names them."""
    return load_scene(source(scene)).library_files()


def generated(scene: str) -> list[str]:
    """The file names this tool writes for ``scene``, in README order."""
    names = [f"{scene}.yaml"]
    names += [f"_library/{p.name}" for p in libraries(scene)]
    encodings = [e for e in ENCODING_SUFFIX if lock(scene, e).is_file()]
    names += [f"{scene}.{e}.modbus.yaml" for e in encodings]
    names += [f"{scene}.st"] if program(scene).is_file() else []
    names += [f"{scene}.py", f"{scene}.CONTEXT.md"]
    names += [f"{scene}-io{ENCODING_SUFFIX[e]}.csv" for e in encodings]
    names += [f"{scene}-tags.L5X", f"{scene}-ignition-tags.json"]
    names += [f"{scene}-grafcet.html"] if scene in grafcet.SCENES else []
    if scene in PLANTPAX:  # PLAN 3.4, 3.8: the PlantPAx L5X, its UDTs and faceplates
        names += [f"{scene}-plantpax.L5X", f"{scene}-plantpax-udts.json"]
        names += [f"{scene}-plantpax-faceplates.json"]
    names += [f"{scene}.ofrec", "README.md"]
    return names


def lock(scene: str, encoding: str) -> Path:
    return source(scene).parent / f"{scene}.{encoding}.modbus.yaml"


def build(scene: str, out: Path) -> None:
    """Write every generated file of ``scene``'s kit into ``out``."""
    src = source(scene)
    out.mkdir(parents=True, exist_ok=True)
    for path in (src, *(lock(scene, e) for e in ENCODING_SUFFIX), program(scene), script(scene)):
        if path.is_file():
            shutil.copyfile(path, out / path.name)
    copy_libraries(scene, out)
    # Exports run on the kit's own copy, so the lockfiles they read are the kit's and the
    # <scene>.CONTEXT.md each one writes beside its scene (PLAN 2.19) lands in the kit, naming
    # the kit's script; scenes/ is never written.
    kit_scene = out / src.name
    for enc, suffix in ENCODING_SUFFIX.items():
        if lock(scene, enc).is_file():
            csv = out / f"{scene}-io{suffix}.csv"
            openfactory("export", str(kit_scene), "--analog-encoding", enc, "-o", str(csv))
    openfactory("export", str(kit_scene), "--format", "l5x", "-o", str(out / f"{scene}-tags.L5X"))
    openfactory(
        "export", str(kit_scene), "--format", "ignition",
        "-o", str(out / f"{scene}-ignition-tags.json"),
    )  # fmt: skip
    if scene in grafcet.SCENES:
        grafcet.write(scene, out / f"{scene}-grafcet.html")
    if scene in PLANTPAX:
        for fmt, name in (
            ("plantpax", f"{scene}-plantpax.L5X"),
            ("plantpax-udts", f"{scene}-plantpax-udts.json"),
            ("plantpax-faceplates", f"{scene}-plantpax-faceplates.json"),
        ):
            openfactory("export", str(kit_scene), "--format", fmt, "-o", str(out / name))
    # The recording runs a copy of the scene in a scratch folder, so `run` cannot touch scenes/.
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp) / src.name
        shutil.copyfile(src, scratch)
        copy_libraries(scene, Path(tmp))
        for enc in ENCODING_SUFFIX:
            if lock(scene, enc).is_file():
                shutil.copyfile(lock(scene, enc), Path(tmp) / lock(scene, enc).name)
        # `test` serves no protocol, and fails the build if the scene's checks fail
        openfactory(
            "test", str(scratch), "--script", str(script(scene)),
            "--duration", str(RECORD_SECONDS), "--record", str(out / f"{scene}.ofrec"), "-q",
        )  # fmt: skip
    (out / "README.md").write_text(readme(scene, out), encoding="utf-8")


def copy_libraries(scene: str, folder: Path) -> None:
    """Copy the scene's library composites to ``folder/_library/``, where the scene finds them."""
    for path in libraries(scene):
        (folder / "_library").mkdir(exist_ok=True)
        shutil.copyfile(path, folder / "_library" / path.name)


def skill_status(scene: str, folder: Path) -> list[tuple[str, str, str]]:
    """The made-outside files present in ``folder``; one that is absent is not listed."""
    rows = []
    for pattern, maker in SKILL_FILES.items():
        name = pattern.format(scene=scene)
        path = folder / name
        if not path.is_file():
            continue
        if PE_HEADER in path.read_text(encoding="utf-8", errors="replace")[:300]:
            rows.append((name, maker, "present, pending PE review"))
        else:
            rows.append((name, maker, f"present, **no `{PE_HEADER}` header line**"))
    return rows


def readme(scene: str, folder: Path) -> str:
    """The kit index. ``folder`` is where the skill-made files are looked for."""
    home = source(scene).parent.relative_to(REPO).as_posix()
    files = {
        f"{scene}.yaml": f"the scene (copied from `{home}/`)",
        **{
            f"_library/{p.name}": "a library composite the scene names (copied from "
            "`scenes/_library/`, PLAN 2.5b)"
            for p in libraries(scene)
        },
        f"{scene}.float32.modbus.yaml": "Modbus address lockfile, float32 analogs (ADR-0004)",
        f"{scene}.int16_scaled.modbus.yaml": "Modbus address lockfile, int16_scaled (OpenPLC)",
        f"{scene}.st": "OpenPLC program (copied from `examples/openplc/`)",
        f"{scene}.py": "stand-in PLC script (copied from "
        f"`{script(scene).parent.relative_to(REPO).as_posix()}/`)",
        f"{scene}.CONTEXT.md": "tags, addresses, components, faults, checks and commands for an "
        "assistant (PLAN 2.19)",
        f"{scene}-io.csv": "I/O list, float32 Modbus addresses",
        f"{scene}-io-int16.csv": "I/O list, int16_scaled Modbus addresses (OpenPLC)",
        f"{scene}-tags.L5X": "Studio 5000 controller tag stubs (PLAN 2.3)",
        f"{scene}-ignition-tags.json": "Ignition 8.3 tags, a UDT instance per component (PLAN 2.4)",
        f"{scene}-grafcet.html": "GRAFCET of the OpenPLC program, pending PE review (PLAN 2.8)",
        f"{scene}-plantpax.L5X": "PlantPAx demo L5X: P_ objects wired to the scene, pending PE "
        "review; verify on import as docs/plantpax.md lists (PLAN 3.4)",
        f"{scene}-plantpax-udts.json": "Ignition 8.3 UDTs for those P_ objects, members the cited "
        "names only (PLAN 3.8)",
        f"{scene}-plantpax-faceplates.json": "Perspective faceplates for them, display only, "
        "pending PE review (PLAN 3.8)",
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
    made = skill_status(scene, folder)
    if made:
        lines += [
            "",
            "## Made outside this tool (committed by hand, pending PE review)",
            "",
            "| File | Made by | Status |",
            "|------|---------|--------|",
        ]
        lines += [f"| `{n}` | {m} | {s} |" for n, m, s in made]
    if any(n.endswith("-perspective.json") for n, _, _ in made):
        lines += ["", "### Needs Designer load before client use", ""]
        lines += [f"- {item}" for item in PERSPECTIVE_OPEN]
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
    ]
    if program(scene).is_file():
        lines += [
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
        names = (p.relative_to(kit).as_posix() for p in kit.rglob("*") if p.is_file())
        extra = sorted(n for n in names if n not in set(generated(scene)) | skill)
        problems += [f"{scene}: {name} is not a kit file" for name in extra]
        if scene in grafcet.SCENES and (why := grafcet.stale(scene)):
            problems.append(f"{scene}: {scene}-grafcet.html is stale: {why}")
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
        if any("GRAFCET sequence" not in p for p in problems):
            print("rebuild with: uv run python tools/build_kit.py", file=sys.stderr)
        if problems:
            return 1
        print(f"kits current: {', '.join(scenes)}")
        return 0
    for scene in scenes:
        build(scene, KITS / scene)
        print(f"built kits/{scene}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
