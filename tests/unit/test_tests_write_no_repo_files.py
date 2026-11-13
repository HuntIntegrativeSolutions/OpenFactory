"""PLAN 4.1c fix 4 (ruled 2026-11-12): tests write screenshots and other output to their own
temporary folder (``tmp_path``, or an ``OPENFACTORY_*_OUT`` folder the person running them
names), never into the repository. A static check over the test sources: no screenshot or
``--out`` path is built from the repository root."""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WRITES = re.compile(r"(screenshot\(path=|\"--out\",)[^\n]*\bREPO\b")


def test_no_test_writes_a_screenshot_into_the_repository():
    found = [
        f"{path.relative_to(REPO)}:{n}"
        for path in sorted((REPO / "tests").rglob("*.py"))
        if path != Path(__file__).resolve()  # this file holds an example of what it rejects
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if WRITES.search(line)
    ]
    assert not found, f"screenshots written into the repository: {found}"


def test_the_check_sees_a_write_into_the_repository():
    assert WRITES.search('page.screenshot(path=str(REPO / "tools" / "screenshots" / "x.png"))')
    assert not WRITES.search('page.screenshot(path=str(out / "x.png"))')
