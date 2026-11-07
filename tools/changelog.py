"""CHANGELOG.md from the commit log, grouped by milestone (PLAN 4.8).

Usage::

    uv run python tools/changelog.py            # rewrite CHANGELOG.md for the current version
    uv run python tools/changelog.py --stdout   # print it instead

Every commit since the first goes under the version in ``src/openfactory/__init__.py`` (nothing
has been tagged yet, so there is one section). Within it, commits are grouped by PLAN milestone,
then by kind (the conventional-commit prefix: feat, fix, perf, docs, test, and the rest as
maintenance), oldest first. A commit's milestone is that of the first PLAN task number in its
subject, such as ``(2.5)`` or ``(PLAN 2.9)``, if that milestone has started (a ticked task in
docs/PLAN.md; a commit citing a later milestone's task, as ``3602275`` cites 3.4, is not work on
it). A commit whose subject names no such task belongs to the milestone of the commit before it:
they are interleaved with the task work they follow. Commits
before the first task number are Milestone 0, the project foundation. Milestone titles come from
the ``### Milestone N — Title`` headings in docs/PLAN.md.

Regenerate it when cutting a release, after the last commit that goes in; the file cannot list
the commit that adds it, so it is not checked in CI.
"""

from __future__ import annotations

import argparse
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "CHANGELOG.md"
#: A PLAN task number in a commit subject (1.4, 2.0a, 2.13b, "PLAN 2.9"), not inside a longer one.
TASK = re.compile(r"(?<![\w.])([0-4])\.(\d{1,2})[a-z]?(?![\w.])")
TICKED = re.compile(r"^- \[x\] ([0-4])\.", re.MULTILINE)
MILESTONE = re.compile(r"^### Milestone (\d+) — (.+?)(?: \(.*\))?$", re.MULTILINE)
PREFIX = re.compile(r"^(?P<kind>[a-z]+)(?:\((?P<scope>[^)]*)\))?: (?P<text>.+)$")
KINDS = {
    "feat": "Features",
    "fix": "Fixes",
    "perf": "Performance",
    "docs": "Documentation",
    "test": "Tests",
}
OTHER = "Maintenance (build, CI, chores)"


@dataclass
class Commit:
    sha: str
    date: str
    subject: str


def commits() -> list[Commit]:
    """Every commit on HEAD, oldest first."""
    out = subprocess.run(
        ["git", "log", "--reverse", "--format=%h%x09%ad%x09%s", "--date=short"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    return [Commit(*line.split("\t", 2)) for line in out.splitlines() if line]


def plan() -> str:
    return (REPO / "docs" / "PLAN.md").read_text(encoding="utf-8")


def milestone_titles(text: str) -> dict[int, str]:
    return {int(n): title.strip() for n, title in MILESTONE.findall(text)}


def started(text: str) -> set[int]:
    """Milestones with at least one ticked task, and Milestone 0."""
    return {0, *(int(n) for n in TICKED.findall(text))}


def version() -> str:
    text = (REPO / "src" / "openfactory" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__ = "([^"]+)"', text).group(1)  # type: ignore[union-attr]


def milestone_of(subject: str, begun: set[int]) -> int | None:
    """The milestone of the first task number in `subject` among milestones in `begun`."""
    for match in TASK.finditer(subject):
        if int(match.group(1)) in begun:
            return int(match.group(1))
    return None


def group(log: list[Commit], begun: set[int]) -> dict[int, dict[str, list[Commit]]]:
    """{milestone: {kind heading: [commits]}}, milestones and commits in log order."""
    groups: dict[int, dict[str, list[Commit]]] = {}
    current = 0
    for commit in log:
        found = milestone_of(commit.subject, begun)
        if found is not None:
            current = found
        prefix = PREFIX.match(commit.subject)
        kind = KINDS.get(prefix.group("kind"), OTHER) if prefix else OTHER
        groups.setdefault(current, {}).setdefault(kind, []).append(commit)
    return groups


def render(log: list[Commit], plan_text: str, release: str) -> str:
    groups = group(log, started(plan_text))
    titles = milestone_titles(plan_text)
    first, last = log[0], log[-1]
    lines = [
        "# Changelog",
        "",
        "OpenFactory is a training and demo tool, not a safety-rated simulation.",
        "",
        "Generated from the commit log by `tools/changelog.py` (PLAN 4.8), grouped by the PLAN",
        "milestone each commit worked on; `docs/PLAN.md` holds the reasoning behind each task. The",
        "project follows semantic versioning from 0.2.0; before 1.0.0 a minor version may change",
        "the scene format or the CLI, and the changelog says so.",
        "",
        f"## {release} (not yet tagged)",
        "",
        f"{len(log)} commits, `{first.sha}` ({first.date}) to `{last.sha}` ({last.date}).",
    ]
    order = [*KINDS.values(), OTHER]
    for number in sorted(groups):
        title = titles.get(number, "")
        lines += ["", f"### Milestone {number}{f' — {title}' if title else ''}"]
        for kind in order:
            entries = groups[number].get(kind)
            if not entries:
                continue
            lines += ["", f"#### {kind}", ""]
            for c in entries:
                prefix = PREFIX.match(c.subject)
                known = prefix is not None and prefix.group("kind") in KINDS
                text = prefix.group("text") if known else c.subject  # type: ignore[union-attr]
                if not re.match(r"\S*[/._`]", text):  # a path or code name keeps its case
                    text = text[0].upper() + text[1:]
                lines.append(f"- {text} (`{c.sha}`, {c.date})")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CHANGELOG.md from the commit log (PLAN 4.8).")
    parser.add_argument("--stdout", action="store_true", help="print instead of writing the file")
    args = parser.parse_args(argv)
    text = render(commits(), plan(), version())
    if args.stdout:
        print(text, end="")
    else:
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
