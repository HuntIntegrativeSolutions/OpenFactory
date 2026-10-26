"""Shared test fixtures.

``prerequisite`` handles tests that need something outside Python: the built viewer
(``web/dist``), node with ``web/node_modules``, or Playwright's Chromium. On a developer machine a
missing prerequisite skips the test with the reason. Under CI (``CI=true``, which GitHub Actions
sets and ``tools/ci_local.sh`` exports) it fails the test instead, so CI can never pass by
quietly skipping the viewer, screenshot, or parity checks (PLAN 1.15).

CLI output: Typer renders help and errors with Rich, which emits ANSI colour codes whenever it
decides the terminal supports them (GitHub Actions runners do). ``NO_COLOR`` and ``TERM=dumb`` are
set at import time so Rich stays plain, and every test that inspects CLI output passes it through
:func:`strip_ansi` (the ``strip_ansi`` fixture), so the tests pass even with ``FORCE_COLOR=1``.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable

import pytest

os.environ["NO_COLOR"] = "1"
os.environ["TERM"] = "dumb"

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)")


def strip_ansi(text: str) -> str:
    """``text`` without ANSI escape sequences (colours, styles, cursor moves, hyperlinks)."""
    return _ANSI.sub("", text)


@pytest.fixture(name="strip_ansi")
def strip_ansi_fixture() -> Callable[[str], str]:
    """:func:`strip_ansi`, for CLI tests that inspect output."""
    return strip_ansi


def in_ci() -> bool:
    return os.environ.get("CI", "").strip().lower() in {"1", "true", "yes"}


def _prerequisite(ok: bool, reason: str) -> None:
    if ok:
        return
    if in_ci():
        pytest.fail(f"{reason}; CI requires it (CI=true), so this is a failure, not a skip")
    pytest.skip(reason)


@pytest.fixture
def prerequisite() -> Callable[[bool, str], None]:
    """``prerequisite(ok, reason)``: skip locally, fail under CI, when ``ok`` is false."""
    return _prerequisite
