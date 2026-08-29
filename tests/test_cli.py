"""Command line tests.

`make corpus` and `make corpus-check` are what a reproducer runs first, so
they are exercised here rather than trusted. The exit codes matter as much as
the output: make stops on a non zero exit, and a corpus check that failed
silently would let a run score a different corpus from ours.
"""

import pytest

from materia.__main__ import main


def run(capsys, *arguments) -> tuple[int, str, str]:
    code = main(list(arguments))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


class TestCorpusBuild:
    def test_it_writes_the_corpus_and_reports_what_it_did(self, capsys, tmp_path):
        code, out, _ = run(capsys, "corpus", "build", "--directory", str(tmp_path))
        assert code == 0
        assert "12 workbooks written" in out
        assert "C10" in out and "legitimate pattern breaks" in out
        assert (tmp_path / "manifest.json").exists()
        assert (tmp_path / "checksums.txt").exists()


class TestCorpusCheck:
    def test_it_passes_on_a_matching_corpus(self, capsys, corpus):
        code, out, _ = run(capsys, "corpus", "check", "--directory", str(corpus[0]))
        assert code == 0
        assert "12 workbooks match" in out

    def test_it_fails_on_an_edited_workbook(self, capsys, corpus_copy):
        (corpus_copy / "C03.xlsx").write_bytes(b"edited")
        code, _, err = run(capsys, "corpus", "check", "--directory", str(corpus_copy))
        assert code == 1
        assert "C03.xlsx" in err
        assert "openpyxl version" in err  # points at the usual cause

    def test_it_fails_on_a_missing_workbook(self, capsys, corpus_copy):
        (corpus_copy / "C07.xlsx").unlink()
        code, _, err = run(capsys, "corpus", "check", "--directory", str(corpus_copy))
        assert code == 1
        assert "missing: C07.xlsx" in err

    def test_it_says_what_to_run_when_there_is_no_corpus(self, capsys, tmp_path):
        code, _, err = run(capsys, "corpus", "check", "--directory", str(tmp_path))
        assert code == 1
        assert "make corpus" in err


class TestEntrypoint:
    def test_no_command_prints_help_and_fails(self, capsys):
        code, _, err = run(capsys)
        assert code == 1
        assert "usage" in err

    def test_version(self, capsys):
        with pytest.raises(SystemExit) as exit_code:
            main(["--version"])
        assert exit_code.value.code == 0
        assert "materia" in capsys.readouterr().out
