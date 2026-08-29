"""End to end pipeline tests.

Driven by a scripted client, so the wiring is checked without spending a
model call. The live run is T17's, recorded under trajectories/solution.
"""

import json
from pathlib import Path

import pytest

from materia.audit import Audit, audit, outputs_for, write_result
from materia.corpus.layout import DECLARED_OUTPUTS
from materia.llm import AgentResponse, ToolCall, Usage
from materia.preflight import PreflightRejected

CORPUS = Path("corpus")
EBITDA = DECLARED_OUTPUTS[0]


class AlwaysIntentional:
    """A model that declines everything. The quiet end of the range."""

    provider, model = "scripted", "scripted-1"

    def __init__(self):
        self.calls = 0

    def complete(self, system, messages, tools=None):
        self.calls += 1
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
                        "reasoning": "It is deliberate.",
                    },
                ),
            ),
            stop_reason="tool_calls",
            usage=Usage(800, 40),
            model="scripted-1",
            provider="scripted",
        )


class MeasuresThenReports:
    """A model that runs the tool and then reports what it measured.

    It reads the cell out of the user message, the way a real one does, so the
    test does not depend on which candidate came first.
    """

    provider, model = "scripted", "scripted-1"

    def __init__(self, formula="=G9"):
        self.formula = formula
        self.cell = None
        self.measured = None
        self._turn = 0

    def complete(self, system, messages, tools=None):
        self._turn += 1
        if self._turn == 1:
            first = messages[0].content
            line = next(l for l in first.splitlines() if l.startswith("Cell: "))
            self.cell = line.removeprefix("Cell: ").strip()
            return AgentResponse(
                text=None,
                tool_calls=(
                    ToolCall(
                        "t1",
                        "recompute_with_patch",
                        {"cell": self.cell, "proposed_formula": self.formula},
                    ),
                ),
                stop_reason="tool_calls",
                usage=Usage(800, 40),
            )
        self.measured = json.loads(messages[-1].content)
        return AgentResponse(
            text=None,
            tool_calls=(
                ToolCall(
                    "v1",
                    "submit_verdict",
                    {
                        "verdict": "ERROR",
                        "confidence": "high",
                        "proposed_formula": self.formula,
                        "evidence": ["its neighbours all use the same shape"],
                        "reasoning": "It breaks the row pattern.",
                        "measured_deltas": self.measured,
                    },
                ),
            ),
            stop_reason="tool_calls",
            usage=Usage(900, 60),
        )


class TestTheWholePipeline:
    def test_it_runs_every_stage(self, tmp_path):
        client = AlwaysIntentional()
        result = audit(
            CORPUS / "C03.xlsx",
            client=client,
            trace_directory=tmp_path,
            max_candidates=3,
        )
        assert isinstance(result, Audit)
        assert result.preflight.formula_count > 400
        assert len(result.candidates) > 15
        assert len(result.verdicts) == 3
        assert client.calls == 3

    def test_a_cell_flagged_twice_costs_one_call(self, tmp_path):
        client = AlwaysIntentional()
        result = audit(CORPUS / "C03.xlsx", client=client, trace_directory=tmp_path)
        assert client.calls == len(result.candidates)

    def test_declining_everything_produces_no_findings(self, tmp_path):
        result = audit(
            CORPUS / "C03.xlsx",
            client=AlwaysIntentional(),
            trace_directory=tmp_path,
            max_candidates=4,
        )
        assert result.result.findings == ()
        assert len(result.result.intentional) == 4
        assert "No material findings." in result.render()

    def test_a_measured_error_becomes_a_finding(self, tmp_path):
        """The path the whole design exists for, exercised without a model."""
        client = MeasuresThenReports()
        result = audit(
            CORPUS / "C03.xlsx",
            client=client,
            trace_directory=tmp_path,
            max_candidates=1,
        )
        assert len(result.result.findings) == 1
        finding = result.result.findings[0]
        assert finding.deltas == client.measured
        assert result.result.violations == ()

    def test_the_funnel_narrows(self, tmp_path):
        result = audit(
            CORPUS / "C03.xlsx",
            client=MeasuresThenReports(),
            trace_directory=tmp_path,
            max_candidates=1,
        )
        funnel = result.funnel
        assert funnel.formulas > funnel.candidates >= funnel.survived >= funnel.findings

    def test_a_dismissed_candidate_did_not_survive_hypothesis_testing(self, tmp_path):
        """INTENTIONAL was dismissed and INCONCLUSIVE established nothing.
        Counting either as surviving would make the funnel narrow less than
        the work actually did."""
        result = audit(
            CORPUS / "C03.xlsx",
            client=AlwaysIntentional(),
            trace_directory=tmp_path,
            max_candidates=4,
        )
        assert result.funnel.candidates > 4
        assert result.funnel.survived == 0
        assert len(result.result.intentional) == 4

    def test_it_records_which_provider_produced_the_run(self, tmp_path):
        result = audit(
            CORPUS / "C03.xlsx",
            client=AlwaysIntentional(),
            trace_directory=tmp_path,
            max_candidates=1,
        )
        assert result.provider == "scripted"


class TestRefusals:
    def test_a_workbook_preflight_rejects_never_reaches_a_model(self, tmp_path, workbooks):
        """No model call is worth spending on a file we cannot evaluate."""
        client = AlwaysIntentional()
        with pytest.raises(PreflightRejected):
            audit(workbooks["vba"], outputs=[EBITDA], client=client, trace_directory=tmp_path)
        assert client.calls == 0

    def test_a_workbook_with_no_declared_outputs_is_refused(self, tmp_path, workbooks):
        """Guessing which cells a decision rests on is the judgement this tool
        must not make on its own."""
        with pytest.raises(ValueError, match="declared output cells are unknown"):
            audit(workbooks["clean"], client=AlwaysIntentional(), trace_directory=tmp_path)

    def test_outputs_can_be_supplied_for_a_workbook_outside_the_corpus(
        self, tmp_path, workbooks
    ):
        result = audit(
            workbooks["three_statement_mini"],
            outputs=["Model!B6", "Valuation!B3"],
            client=AlwaysIntentional(),
            trace_directory=tmp_path,
        )
        assert result.workbook.endswith(".xlsx")

    def test_outputs_for_reads_the_manifest(self):
        assert outputs_for(CORPUS / "C03.xlsx") == DECLARED_OUTPUTS


class TestTheResultSet:
    def test_it_is_written_as_json_the_evaluator_can_read(self, tmp_path):
        result = audit(
            CORPUS / "C03.xlsx",
            client=MeasuresThenReports(),
            trace_directory=tmp_path,
            max_candidates=1,
        )
        path = write_result(result, tmp_path / "results")
        data = json.loads(path.read_text())

        assert data["workbook"] == "C03.xlsx"
        assert data["findings"][0]["address"] == result.result.findings[0].address
        assert data["findings"][0]["impact"]

    def test_it_records_the_provider_next_to_the_result(self, tmp_path):
        """A dev loop run must never be mistaken for a scored one."""
        result = audit(
            CORPUS / "C03.xlsx",
            client=AlwaysIntentional(),
            trace_directory=tmp_path,
            max_candidates=1,
        )
        write_result(result, tmp_path / "results")
        record = json.loads((tmp_path / "results" / "provider.json").read_text())
        assert record["provider"] == "scripted"
        assert record["scored"] is False
