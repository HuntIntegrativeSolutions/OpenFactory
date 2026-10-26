"""Shared test fixtures.

``prerequisite`` handles tests that need something outside Python: the built viewer
(``web/dist``), node with ``web/node_modules``, or Playwright's Chromium. On a developer machine a
missing prerequisite skips the test with the reason. Under CI (``CI=true``, which GitHub Actions
sets and ``tools/ci_local.sh`` exports) it fails the test instead, so CI can never pass by
quietly skipping the viewer, screenshot, or parity checks (PLAN 1.15).
"""

from __future__ import annotations

import os
from collections.abc import Callable

import pytest


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
