"""Shared pytest fixtures and session hooks.

No fixtures yet. Workbook fixtures arrive with the preflight validator in T02.
"""

from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).parent


def pytest_sessionfinish(session, exitstatus):
    """Treat an empty suite as a pass, but only while the suite is empty.

    pytest exits 5 when it collects nothing, which would make `make verify`
    fail on the T01 scaffold before any test exists. Tolerating that
    unconditionally would hide a worse problem later: if the suite stopped
    being collected at all, verify would go green with nothing running. So the
    exit code is only downgraded while tests/ genuinely contains no test
    modules. Once T02 adds one, a zero collection run fails again.
    """
    if exitstatus != pytest.ExitCode.NO_TESTS_COLLECTED:
        return
    if any(TESTS_DIR.rglob("test_*.py")):
        return
    print("no test files exist yet, T01 scaffold only. This stops being OK at T02.")
    session.exitstatus = pytest.ExitCode.OK
