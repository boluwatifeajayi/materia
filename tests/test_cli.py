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


class TestLlmCheck:
    """The command that confirms a model id is real before a run depends on it."""

    def test_it_reports_a_missing_key_rather_than_failing_obscurely(
        self, capsys, monkeypatch
    ):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        code, _, err = run(capsys, "llm", "check", "--provider", "openai")
        assert code == 1
        assert "OPENAI_API_KEY" in err

    def test_an_unavailable_model_gets_its_own_exit_code(self, capsys, monkeypatch):
        """Exit 2 rather than 1, so a caller can tell a wrong model id from a
        network problem without reading the message."""
        from materia.llm import ModelNotAvailable

        class Refusing:
            provider, model = "groq", "made-up"

            def complete(self, *_, **__):
                raise ModelNotAvailable("no such model")

        monkeypatch.setattr("materia.llm.get_client", lambda *_a, **_k: Refusing())
        code, _, err = run(capsys, "llm", "check", "--provider", "groq")
        assert code == 2
        assert "no such model" in err

    def test_a_model_that_ignores_the_tool_fails(self, capsys, monkeypatch):
        """A provider that answers in prose cannot drive the adjudicator, so
        passing the check would be misleading."""
        from materia.llm import AgentResponse

        class Chatty:
            provider, model = "groq", "chatty"

            def complete(self, *_, **__):
                return AgentResponse(text="42", model="chatty", provider="groq")

        monkeypatch.setattr("materia.llm.get_client", lambda *_a, **_k: Chatty())
        code, out, err = run(capsys, "llm", "check", "--provider", "groq")
        assert code == 1
        assert "NOT CALLED" in out
        assert "did not call the tool" in err


class TestAudit:
    """The command a reproducer runs on one workbook."""

    @staticmethod
    def _scripted(monkeypatch):
        from materia.llm import AgentResponse, ToolCall, Usage

        class Declines:
            provider, model = "scripted", "scripted-1"

            def complete(self, *_, **__):
                return AgentResponse(
                    text=None,
                    tool_calls=(
                        ToolCall(
                            "v1",
                            "submit_verdict",
                            {
                                "verdict": "INTENTIONAL",
                                "confidence": "high",
                                "evidence": ["the row label says Actual"],
                                "reasoning": "Deliberate.",
                            },
                        ),
                    ),
                    stop_reason="tool_calls",
                    usage=Usage(800, 40),
                )

        monkeypatch.setattr("materia.llm.get_client", lambda *_a, **_k: Declines())

    def test_it_prints_the_funnel_and_the_report(self, capsys, monkeypatch, tmp_path):
        self._scripted(monkeypatch)
        code, out, _ = run(
            capsys,
            "audit",
            "corpus/C03.xlsx",
            "--traces",
            str(tmp_path),
            "--max-candidates",
            "2",
        )
        assert code == 0
        assert "MODEL HEALTH" in out
        assert "formulas parsed" in out
        assert "WHAT WAS SET ASIDE" in out

    def test_explain_shows_where_every_figure_came_from(self, capsys, monkeypatch, tmp_path):
        self._scripted(monkeypatch)
        _, out, _ = run(
            capsys, "audit", "corpus/C03.xlsx", "--explain",
            "--traces", str(tmp_path), "--max-candidates", "1",
        )
        assert "HOW TO CHECK THIS" in out
        assert ".jsonl" in out
        assert "scripted" in out

    def test_it_writes_a_result_set_when_asked(self, capsys, monkeypatch, tmp_path):
        self._scripted(monkeypatch)
        results = tmp_path / "results"
        run(
            capsys, "audit", "corpus/C03.xlsx",
            "--traces", str(tmp_path), "--results", str(results),
            "--max-candidates", "1",
        )
        assert (results / "C03.json").exists()
        assert (results / "provider.json").exists()

    def test_a_rejected_workbook_gets_its_own_exit_code(
        self, capsys, monkeypatch, tmp_path, workbooks
    ):
        """Exit 2, so a caller can tell an unsupported file from a failure."""
        self._scripted(monkeypatch)
        code, _, err = run(
            capsys, "audit", str(workbooks["vba"]),
            "--outputs", "Sheet!A1", "--traces", str(tmp_path),
        )
        assert code == 2
        assert "VBA_PRESENT" in err
        assert "was not audited" in err

    def test_a_workbook_with_unknown_outputs_says_what_to_pass(
        self, capsys, monkeypatch, tmp_path, workbooks
    ):
        self._scripted(monkeypatch)
        code, _, err = run(
            capsys, "audit", str(workbooks["clean"]), "--traces", str(tmp_path)
        )
        assert code == 1
        assert "--outputs" in err

    def test_a_missing_key_stops_before_any_work(self, capsys, monkeypatch, tmp_path):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        code, _, err = run(
            capsys, "audit", "corpus/C03.xlsx",
            "--provider", "openai", "--traces", str(tmp_path),
        )
        assert code == 1
        assert "OPENAI_API_KEY" in err


class TestRepairFlag:
    @staticmethod
    def _isolated(tmp_path):
        """A copy of the workbook and its trajectories.

        A repair writes its own trace into the directory it read from, which
        is right for a real run and wrong for a test: pointing these at the
        committed trajectories left a test artefact in the deliverable.
        """
        import shutil

        subject = tmp_path / "C03.xlsx"
        shutil.copy("corpus/C03.xlsx", subject)
        traces = tmp_path / "traces"
        shutil.copytree("trajectories/solution", traces)
        return subject, traces

    def test_it_asks_before_writing_anything(self, capsys, monkeypatch, tmp_path):
        """The ground rule: consequential actions are gated behind a person."""
        subject, traces = self._isolated(tmp_path)
        asked = []
        monkeypatch.setattr("builtins.input", lambda prompt: asked.append(prompt) or "n")

        code, out, _ = run(
            capsys, "report", str(subject), "--traces", str(traces), "--repair"
        )
        assert code == 0
        assert len(asked) == 2
        assert "no file was written" in out

    def test_declining_leaves_the_input_untouched(self, capsys, monkeypatch, tmp_path):
        import hashlib

        subject, traces = self._isolated(tmp_path)
        before = hashlib.sha256(subject.read_bytes()).hexdigest()
        monkeypatch.setattr("builtins.input", lambda _p: "n")

        run(capsys, "report", str(subject), "--traces", str(traces), "--repair")
        assert hashlib.sha256(subject.read_bytes()).hexdigest() == before

    def test_approving_writes_a_copy_and_says_where(self, capsys, monkeypatch, tmp_path):
        subject, traces = self._isolated(tmp_path)
        target = tmp_path / "fixed.xlsx"
        monkeypatch.setattr("builtins.input", lambda _p: "y")

        _, out, _ = run(
            capsys, "report", str(subject), "--traces", str(traces),
            "--repair", "--repair-to", str(target),
        )
        assert target.exists()
        assert str(target) in out
        assert "was not modified" in out

    def test_without_the_flag_nothing_is_asked_and_nothing_written(
        self, capsys, monkeypatch, tmp_path
    ):
        """Repair is opt in. A plain report must never touch a file."""
        subject, traces = self._isolated(tmp_path)

        def refuse(_prompt):
            raise AssertionError("a plain report must not prompt")

        monkeypatch.setattr("builtins.input", refuse)
        run(capsys, "report", str(subject), "--traces", str(traces))
        assert not (tmp_path / "C03.repaired.xlsx").exists()
