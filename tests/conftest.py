"""Shared pytest fixtures and session hooks."""

from pathlib import Path

import pytest

from fixtures.build import build_all

TESTS_DIR = Path(__file__).parent


@pytest.fixture(scope="session")
def workbooks(tmp_path_factory) -> dict[str, Path]:
    """Every fixture workbook, generated once per test session."""
    return build_all(tmp_path_factory.mktemp("workbooks"))


@pytest.fixture(scope="session")
def corpus(tmp_path_factory):
    """The full twelve workbook corpus, built once.

    Building it takes a couple of seconds, so the tests that only read it
    share one build and the ones that damage it copy this into tmp_path.
    """
    from materia.corpus.build import build_corpus

    directory = tmp_path_factory.mktemp("corpus") / "corpus"
    return directory, build_corpus(directory)


@pytest.fixture
def corpus_copy(corpus, tmp_path):
    """A throwaway copy, for tests that edit or delete workbooks."""
    import shutil

    directory, _ = corpus
    target = tmp_path / "corpus"
    shutil.copytree(directory, target)
    return target


def pytest_sessionfinish(session, exitstatus):
    """Treat an empty suite as a pass, but only while the suite is empty.

    pytest exits 5 when it collects nothing, which would make `make verify`
    fail on the T01 scaffold before any test exists. Tolerating that
    unconditionally would hide a worse problem later: if the suite stopped
    being collected at all, verify would go green with nothing running. So the
    exit code is only downgraded while tests/ genuinely contains no test
    modules.
    """
    if exitstatus != pytest.ExitCode.NO_TESTS_COLLECTED:
        return
    if any(TESTS_DIR.rglob("test_*.py")):
        return
    print("no test files exist yet, T01 scaffold only. This stops being OK at T02.")
    session.exitstatus = pytest.ExitCode.OK
