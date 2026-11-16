"""A committed reference of a shipped scene's run, for the scenes no demo kit holds (PLAN §9
rule 13).

Every shipped scene (``scenes/*.yaml`` and ``scenes/examples/*.yaml``) must run as it did, and a
change to its run must fail a test that names the scene and the tick where the run first moved.
A scene with a demo kit is held by the kit's 60 s recording, which ``tools/build_kit.py --check``
compares line for line in CI. A scene with no kit is held here: ``tests/references/<scene>.run``
holds the check results of the same 60 s run under the same stand-in script, a digest of the run
summary, and one short digest per recorded line (every tick an I/O tag changed, and the full
state frame every ``FRAME_EVERY`` ticks), so the reference is a few tens of KB of text rather
than a recording. The recording's header is not digested: it carries the OpenFactory version
and the scene's own text, which change without the run changing.

``tests/integration/test_runs_unchanged.py`` runs the check. A reference is renewed only when a
change to the run is intended and the owner has ruled so, in the commit that makes the change,
which names the scene and says why (PLAN §9 rule 13)::

    uv run python tools/run_reference.py --update accumulation-demo

With no option it checks every reference and exits 1 if a run moved.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REFERENCES = REPO / "tests" / "references"
_spec = importlib.util.spec_from_file_location("build_kit", REPO / "tools" / "build_kit.py")
build_kit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_kit)

from openfactory.record import (  # noqa: E402 - after the sibling tool
    first_difference,
    line_digest,
    read_recording,
    run_lines,
)

HEAD = "# OpenFactory run reference (PLAN §9 rule 13). Renew only as that rule says:"


def shipped() -> list[str]:
    """Every shipped scene's name: ``scenes/*.yaml`` and ``scenes/examples/*.yaml``."""
    folders = (REPO / "scenes", REPO / "scenes" / "examples")
    return sorted(
        p.stem for f in folders for p in f.glob("*.yaml") if not p.name.endswith(".modbus.yaml")
    )


def reference_names() -> list[str]:
    return sorted(p.stem for p in REFERENCES.glob("*.run"))


def digest(scene: str, scene_file: Path | None = None) -> str:
    """The reference text of ``scene``'s 60 s run (of ``scene_file``, a changed copy, if given)."""
    with tempfile.TemporaryDirectory() as tmp:
        ofrec = Path(tmp) / f"{scene}.ofrec"
        checks = build_kit.record(scene, ofrec, scene_file)
        dt = read_recording(ofrec).dt
        lines = run_lines(ofrec)
    body = [HEAD, f"#   uv run python tools/run_reference.py --update {scene}"]
    body += [f"scene {scene}", f"dt {dt!r}", f"seconds {build_kit.RECORD_SECONDS}", checks]
    body += [f"{'end' if k is None else k} {line_digest(line)}" for k, line in lines]
    return "\n".join(body) + "\n"


def parse(text: str) -> tuple[list[str], float, list[tuple[int | None, str]]]:
    """(the check-result lines, dt, the (tick, digest) list) of a reference's text."""
    rows = [r for r in text.splitlines() if not r.startswith("#")]
    dt = float(next(r for r in rows if r.startswith("dt "))[3:])
    start = rows.index("checks:")
    end = next(i for i in range(start, len(rows)) if rows[i].startswith(("PASS:", "FAIL:")))
    lines = [r.split(" ") for r in rows[end + 1 :]]
    return rows[start : end + 1], dt, [(None if k == "end" else int(k), d) for k, d in lines]


def compare(scene: str, committed: str, fresh: str) -> str | None:
    """``None`` when ``fresh`` is the committed run, else what moved, naming scene and tick."""
    checks_a, dt, run_a = parse(committed)
    checks_b, _, run_b = parse(fresh)
    if where := first_difference(run_a, run_b, dt):
        return f"{scene}: the run moved: it {where} (tests/references/{scene}.run)"
    if checks_a != checks_b:
        changed = [b for a, b in zip(checks_a, checks_b, strict=False) if a != b]
        return f"{scene}: the check results moved: {changed or checks_b}"
    return None


def check(scene: str) -> str | None:
    path = REFERENCES / f"{scene}.run"
    return compare(scene, path.read_text(encoding="utf-8"), digest(scene))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check or renew run references (PLAN §9 r13).")
    parser.add_argument("scenes", nargs="*", help="scene names (default: every reference)")
    parser.add_argument(
        "--update", action="store_true", help="renew the named references (owner's ruling)"
    )
    args = parser.parse_args(argv)
    if args.update:
        if not args.scenes:
            print("name the scenes to renew; renewing needs the owner's ruling", file=sys.stderr)
            return 2
        REFERENCES.mkdir(parents=True, exist_ok=True)
        for scene in args.scenes:
            (REFERENCES / f"{scene}.run").write_text(digest(scene), encoding="utf-8", newline="\n")
            print(f"renewed tests/references/{scene}.run")
        return 0
    problems = [p for scene in args.scenes or reference_names() if (p := check(scene))]
    for p in problems:
        print(p, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
