"""Repair mode tests.

Writing to somebody's financial model is consequential, so the tests that
matter are the ones about what is not written: the input file, and anything a
person declined.
"""

import hashlib
import shutil
from pathlib import Path

import openpyxl
import pytest

from materia.audit import from_trajectories
from materia.repair import default_target, repair
from materia.trace import read

CORPUS = Path("corpus")


def digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def findings():
    """The two verified C03 findings, from the committed trajectories."""
    return from_trajectories(CORPUS / "C03.xlsx", "trajectories/solution").result.findings


@pytest.fixture
def workbook(tmp_path):
    target = tmp_path / "C03.xlsx"
    shutil.copy(CORPUS / "C03.xlsx", target)
    return target


def approve_all(_finding) -> bool:
    return True


def decline_all(_finding) -> bool:
    return False


class TestTheInputIsNeverWritten:
    """The invariant in docs/ARCHITECTURE.md, checked on the bytes."""

    def test_after_approving_everything(self, workbook, findings, tmp_path):
        before = digest(workbook)
        repair(workbook, findings, ask=approve_all, trace_directory=tmp_path)
        assert digest(workbook) == before

    def test_after_declining_everything(self, workbook, findings, tmp_path):
        before = digest(workbook)
        repair(workbook, findings, ask=decline_all, trace_directory=tmp_path)
        assert digest(workbook) == before

    def test_after_a_mixture(self, workbook, findings, tmp_path):
        before = digest(workbook)
        answers = iter([True, False])
        repair(workbook, findings, ask=lambda _f: next(answers), trace_directory=tmp_path)
        assert digest(workbook) == before

    def test_writing_over_the_input_is_refused(self, workbook, findings, tmp_path):
        """The one mistake that would make every other guarantee pointless."""
        with pytest.raises(ValueError, match="never over the original"):
            repair(workbook, findings, target=workbook, ask=approve_all,
                   trace_directory=tmp_path)
        assert workbook.exists()

    def test_an_interrupted_run_leaves_nothing_behind(self, workbook, findings, tmp_path):
        """Answers are collected before anything is written, so a run that
        stops half way does not leave a partly repaired file."""
        before = digest(workbook)

        def stop_on_the_second(finding):
            if finding is findings[1]:
                raise KeyboardInterrupt
            return True

        with pytest.raises(KeyboardInterrupt):
            repair(workbook, findings, ask=stop_on_the_second, trace_directory=tmp_path)

        assert digest(workbook) == before
        assert not default_target(workbook).exists()


class TestWhatGetsWritten:
    def test_an_approved_change_lands_in_the_copy(self, workbook, findings, tmp_path):
        result = repair(workbook, findings, ask=approve_all, trace_directory=tmp_path)

        book = openpyxl.load_workbook(result.written)
        for finding in findings:
            sheet, coordinate = finding.address.split("!", 1)
            assert book[sheet][coordinate].value == finding.proposed_formula
        book.close()

    def test_a_declined_change_does_not(self, workbook, findings, tmp_path):
        answers = iter([True, False])
        result = repair(workbook, findings, ask=lambda _f: next(answers),
                        trace_directory=tmp_path)

        book = openpyxl.load_workbook(result.written)
        approved, declined = findings[0], findings[1]
        sheet, coordinate = approved.address.split("!", 1)
        assert book[sheet][coordinate].value == approved.proposed_formula

        sheet, coordinate = declined.address.split("!", 1)
        assert book[sheet][coordinate].value != declined.proposed_formula
        book.close()

    def test_declining_everything_writes_no_file_at_all(self, workbook, findings, tmp_path):
        """Not an empty copy. Nothing was agreed to, so nothing is produced."""
        result = repair(workbook, findings, ask=decline_all, trace_directory=tmp_path)
        assert result.written is None
        assert not default_target(workbook).exists()

    def test_the_copy_goes_beside_the_original_by_default(self, workbook, findings, tmp_path):
        result = repair(workbook, findings, ask=approve_all, trace_directory=tmp_path)
        assert result.written == workbook.with_name("C03.repaired.xlsx")

    def test_a_target_can_be_named(self, workbook, findings, tmp_path):
        target = tmp_path / "somewhere" / "fixed.xlsx"
        result = repair(workbook, findings, target=target, ask=approve_all,
                        trace_directory=tmp_path)
        assert result.written == target
        assert target.exists()

    def test_the_repaired_copy_still_passes_preflight(self, workbook, findings, tmp_path):
        """A repair that produced a file the tool cannot read would be worse
        than no repair."""
        from materia.preflight import preflight

        result = repair(workbook, findings, ask=approve_all, trace_directory=tmp_path)
        assert preflight(result.written).formula_count > 400

    def test_the_repair_moves_the_outputs_as_promised(self, workbook, findings, tmp_path):
        """The whole claim. The measured impact was 8704573 on EBITDA, so the
        repaired workbook has to differ from the original by that."""
        from materia.corpus.layout import DECLARED_OUTPUTS
        from materia.recompute import Model

        ebitda = DECLARED_OUTPUTS[0]
        before = Model.load(workbook).value(ebitda)
        result = repair(workbook, [findings[0]], ask=approve_all, trace_directory=tmp_path)
        after = Model.load(result.written).value(ebitda)

        assert after - before == pytest.approx(findings[0].deltas[ebitda], abs=1.0)


class TestHumanCheckpoints:
    """A decline is a decision about the model and belongs in the record."""

    def test_every_answer_is_recorded(self, workbook, findings, tmp_path):
        answers = iter([True, False])
        result = repair(workbook, findings, ask=lambda _f: next(answers),
                        trace_directory=tmp_path)

        checkpoints = [r for r in read(result.trace_path) if r.type == "human_checkpoint"]
        assert len(checkpoints) == 2
        assert [c.content["decision"] for c in checkpoints] == ["approved", "declined"]

    def test_a_checkpoint_names_the_cell_and_what_was_offered(self, workbook, findings, tmp_path):
        result = repair(workbook, findings, ask=decline_all, trace_directory=tmp_path)
        checkpoint = next(r for r in read(result.trace_path) if r.type == "human_checkpoint")

        assert checkpoint.content["kind"] == "repair_approval"
        assert checkpoint.content["cell"] == findings[0].address
        assert checkpoint.content["proposed_formula"] == findings[0].proposed_formula
        assert checkpoint.content["impact"]

    def test_the_trace_records_where_the_copy_went(self, workbook, findings, tmp_path):
        result = repair(workbook, findings, ask=approve_all, trace_directory=tmp_path)
        end = read(result.trace_path)[-1]
        assert end.content["written"] == str(result.written)
        assert end.content["approved"] == 2

    def test_a_run_that_wrote_nothing_says_so_in_the_trace(self, workbook, findings, tmp_path):
        result = repair(workbook, findings, ask=decline_all, trace_directory=tmp_path)
        end = read(result.trace_path)[-1]
        assert end.content["written"] is None
        assert end.content["approved"] == 0


class TestNothingToDo:
    def test_no_findings_writes_no_file(self, workbook, tmp_path):
        result = repair(workbook, [], ask=approve_all, trace_directory=tmp_path)
        assert result.written is None
        assert "Nothing to repair." in result.render()

    def test_a_finding_with_no_proposed_formula_is_not_applied(self, workbook, findings, tmp_path):
        """There is nothing to write. It is still asked about and recorded."""
        from dataclasses import replace

        result = repair(
            workbook, [replace(findings[0], proposed_formula=None)],
            ask=approve_all, trace_directory=tmp_path,
        )
        assert result.written is None
        assert result.decisions[0].approved is True


class TestTheSummary:
    def test_it_lists_every_decision(self, workbook, findings, tmp_path):
        answers = iter([True, False])
        rendered = repair(workbook, findings, ask=lambda _f: next(answers),
                          trace_directory=tmp_path).render()
        assert "approved" in rendered
        assert "declined" in rendered

    def test_it_separates_what_was_approved_from_what_was_declined(
        self, workbook, findings, tmp_path
    ):
        answers = iter([True, False])
        result = repair(workbook, findings, ask=lambda _f: next(answers),
                        trace_directory=tmp_path)
        assert [d.address for d in result.approved] == [findings[0].address]
        assert [d.address for d in result.declined] == [findings[1].address]

    def test_it_says_the_original_was_not_modified(self, workbook, findings, tmp_path):
        rendered = repair(workbook, findings, ask=approve_all,
                          trace_directory=tmp_path).render()
        assert "was not modified" in rendered

    def test_it_says_when_nothing_was_approved(self, workbook, findings, tmp_path):
        rendered = repair(workbook, findings, ask=decline_all,
                          trace_directory=tmp_path).render()
        assert "no file was written" in rendered

    def test_no_em_dashes(self, workbook, findings, tmp_path):
        rendered = repair(workbook, findings, ask=approve_all,
                          trace_directory=tmp_path).render()
        assert "—" not in rendered


class TestThePrompt:
    def test_anything_but_yes_is_no(self, monkeypatch, findings):
        """An unattended run, or somebody pressing return to get through it,
        must not end up writing changes nobody agreed to."""
        from materia.repair import prompt_for

        for answer in ("", "n", "no", "maybe", "later", " "):
            monkeypatch.setattr("builtins.input", lambda _p, a=answer: a)
            assert prompt_for(findings[0]) is False

    def test_yes_is_yes(self, monkeypatch, findings):
        from materia.repair import prompt_for

        for answer in ("y", "Y", "yes", " YES "):
            monkeypatch.setattr("builtins.input", lambda _p, a=answer: a)
            assert prompt_for(findings[0]) is True

    def test_it_shows_the_change_and_the_impact(self, monkeypatch, capsys, findings):
        from materia.repair import prompt_for

        monkeypatch.setattr("builtins.input", lambda _p: "n")
        prompt_for(findings[0])
        out = capsys.readouterr().out
        assert findings[0].address in out
        assert findings[0].proposed_formula in out
        assert "moves" in out
