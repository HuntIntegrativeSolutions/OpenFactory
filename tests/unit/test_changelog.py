"""PLAN 4.8: CHANGELOG.md from the commit log, grouped by milestone (tools/changelog.py)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("changelog", REPO / "tools" / "changelog.py")
changelog = importlib.util.module_from_spec(spec)
sys.modules["changelog"] = changelog  # dataclasses look their module up while being defined
spec.loader.exec_module(changelog)
Commit = changelog.Commit

PLAN = """
### Milestone 0 — Project foundation (this week)
### Milestone 1 — Walking skeleton (target 2 weeks)
- [x] 1.4 Components
### Milestone 2 — Discrete library
- [x] 2.5 More components
### Milestone 3 — Process simulation
- [ ] 3.4 Pumps
"""


def log(*subjects: str) -> list:
    return [Commit(f"{i:07x}", "2026-11-06", s) for i, s in enumerate(subjects)]


def test_task_numbers_pick_the_milestone_and_untagged_commits_follow_the_one_before():
    commits = log(
        "chore: project foundation",
        "feat: components (1.4)",
        "docs: notes on the components",
        "feat: a stopper (PLAN 2.5)",
        "fix: cite RM014; PLAN 3.4 sourced to RM002",  # 3 has not started: not work on it
        "feat: lift (2.5b, item 2 of 8)",
    )
    groups = changelog.group(commits, changelog.started(PLAN))
    assert {m: {k: [c.sha for c in v] for k, v in g.items()} for m, g in groups.items()} == {
        0: {changelog.OTHER: ["0000000"]},
        1: {"Features": ["0000001"], "Documentation": ["0000002"]},
        2: {"Features": ["0000003", "0000005"], "Fixes": ["0000004"]},
    }


def test_task_numbers_are_whole_tokens():
    begun = {0, 1, 2}
    assert changelog.milestone_of("feat: tick-locked physics (2.0a)", begun) == 2
    assert changelog.milestone_of("fix: cite 1756-RM014 and rapier 0.21.0", begun) is None
    assert changelog.milestone_of("perf: 12.5 % faster", begun) is None


def test_the_rendered_changelog_names_the_version_milestones_and_kinds():
    commits = log(
        "chore: start",
        "feat: components (1.4)",
        "fix: a gap (1.4)",
        "feat: tools/x.py prints (1.4)",
    )
    text = changelog.render(commits, PLAN, "0.2.0")
    assert "## 0.2.0 (not yet tagged)" in text
    assert "4 commits, `0000000` (2026-11-06) to `0000003` (2026-11-06)." in text
    assert "### Milestone 0 — Project foundation\n" in text  # the "(this week)" note dropped
    assert "### Milestone 1 — Walking skeleton\n" in text
    assert "#### Features\n\n- Components (1.4) (`0000001`, 2026-11-06)" in text
    assert "#### Fixes\n\n- A gap (1.4) (`0000002`, 2026-11-06)" in text
    assert "- Chore: start (`0000000`, 2026-11-06)" in text  # other kinds keep their prefix
    assert "- tools/x.py prints (1.4) (`0000003`, 2026-11-06)" in text  # a path keeps its case
    assert "not a safety-rated simulation" in text


def test_the_committed_changelog_is_for_the_package_version():
    text = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## {changelog.version()} (not yet tagged)" in text
