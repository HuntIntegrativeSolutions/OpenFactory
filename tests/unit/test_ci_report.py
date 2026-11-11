"""tools/ci_report.py: pytest's count and each Mosquitto test's outcome as annotations, so a CI
run's broker checks are readable without the job log (review of 2026-11-10)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("ci_report", REPO / "tools" / "ci_report.py")
ci_report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci_report)

JUNIT = """<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest">
<testcase classname="tests.integration.test_sparkplug_mosquitto" name="test_a"/>
<testcase classname="tests.integration.test_sparkplug_mosquitto" name="test_b"><skipped/></testcase>
<testcase classname="tests.unit.test_other" name="test_c"><failure/></testcase>
</testsuite></testsuites>"""


def test_the_count_and_each_watched_test_are_annotations(tmp_path, monkeypatch, capsys):
    log, junit = tmp_path / "ci.log", tmp_path / "ci.xml"
    log.write_text("....\n1080 passed, 1 warning in 496.67s (0:08:16)\n", encoding="utf-8")
    junit.write_text(JUNIT, encoding="utf-8")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert ci_report.main([str(log), str(junit)]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "::notice title=pytest::1080 passed, 1 warning in 496.67s (0:08:16)",
        "::notice title=mosquitto::test_sparkplug_mosquitto::test_a: passed",
        "::notice title=mosquitto::test_sparkplug_mosquitto::test_b: skipped",
    ]
    monkeypatch.delenv("GITHUB_ACTIONS")
    junit.unlink()
    ci_report.main([str(log), str(junit)])
    assert "mosquitto: no Mosquitto test in the report" in capsys.readouterr().out
