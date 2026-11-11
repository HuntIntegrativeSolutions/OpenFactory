"""Report pytest's result as GitHub Actions annotations (PLAN 3.5, review of 2026-11-10).

Usage::

    uv run python tools/ci_report.py PYTEST_LOG JUNIT_XML

Prints pytest's closing count line, and the outcome of every test in a module named in
``WATCHED`` (the Sparkplug node against Mosquitto), so a run's results can be read through the
public check-runs API without the job log. Off GitHub Actions (``GITHUB_ACTIONS`` not "true") it
prints the same lines without the ``::notice`` prefix. It never fails the build: pytest's own exit
status decides that.
"""

from __future__ import annotations

import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

#: Test modules whose every test outcome is reported.
WATCHED = ("test_sparkplug_mosquitto",)


def outcomes(junit: Path) -> list[tuple[str, str]]:
    """(test id, outcome) for each watched test in a JUnit XML report."""
    out = []
    for case in ET.parse(junit).getroot().iter("testcase"):
        module = case.get("classname", "")
        if not any(w in module for w in WATCHED):
            continue
        kinds = {child.tag for child in case}
        outcome = next((k for k in ("failure", "error", "skipped") if k in kinds), "passed")
        out.append((f"{module.rsplit('.', 1)[-1]}::{case.get('name')}", outcome))
    return out


def lines(log: Path, junit: Path) -> list[tuple[str, str]]:
    count = [
        line.strip()
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines()
        if re.search(r"\d+ (passed|failed|errors?|skipped).* in [0-9.]+s", line)
    ]
    out = [("pytest", count[-1])] if count else [("pytest", "no count line found")]
    watched = outcomes(junit) if junit.is_file() else []
    out += [("mosquitto", f"{test}: {outcome}") for test, outcome in watched]
    if not watched:
        out.append(("mosquitto", "no Mosquitto test in the report"))
    return out


def main(argv: list[str]) -> int:
    log, junit = Path(argv[0]), Path(argv[1])
    actions = os.environ.get("GITHUB_ACTIONS") == "true"
    for title, text in lines(log, junit):
        print(f"::notice title={title}::{text}" if actions else f"{title}: {text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
