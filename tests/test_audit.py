"""End to end pipeline tests.

Driven by a scripted client, so the wiring is checked without spending a
model call. The live run is T17's, recorded under trajectories/solution.
"""

import json
from pathlib import Path

import pytest

from materia.audit import (
    Audit,
    audit,
    from_trajectories,
    outputs_for,
    write_result,
)
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

    def test_a_bounded_run_records_how_many_it_tested(self, tmp_path):
        result = audit(
            CORPUS / "C03.xlsx",
            client=AlwaysIntentional(),
            trace_directory=tmp_path,
            max_candidates=3,
        )
        assert result.funnel.adjudicated == 3
        assert result.funnel.complete is False
        assert "were not examined" in result.render()

    def test_an_unbounded_run_reads_as_complete(self, tmp_path):
        result = audit(
            CORPUS / "C03.xlsx", client=AlwaysIntentional(), trace_directory=tmp_path
        )
        assert result.funnel.complete is True

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


class TestARunCutShortKeepsWhatItEarned:
    """Observed live: the Groq daily token quota ran out part way through a
    seventeen candidate run and the exception discarded eight completed
    verdicts along with it."""

    @staticmethod
    def _stops_after(count):
        from materia.llm import RateLimited

        class Limited:
            provider, model = "scripted", "scripted-1"

            def __init__(self):
                self.seen = 0

            def complete(self, *_, **__):
                self.seen += 1
                if self.seen > count:
                    raise RateLimited("tokens per day limit reached")
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

        return Limited()

    def test_the_verdicts_already_earned_survive(self, tmp_path):
        result = audit(
            CORPUS / "C03.xlsx",
            client=self._stops_after(3),
            trace_directory=tmp_path,
            max_candidates=10,
        )
        assert len(result.verdicts) == 3
        assert len(result.result.intentional) == 3

    def test_the_report_says_it_stopped_and_why(self, tmp_path):
        result = audit(
            CORPUS / "C03.xlsx",
            client=self._stops_after(3),
            trace_directory=tmp_path,
            max_candidates=10,
        )
        rendered = result.render()
        assert "stopped early" in rendered
        assert "tokens per day" in rendered

    def test_the_funnel_does_not_imply_the_rest_were_cleared(self, tmp_path):
        result = audit(
            CORPUS / "C03.xlsx",
            client=self._stops_after(3),
            trace_directory=tmp_path,
            max_candidates=10,
        )
        assert result.funnel.adjudicated == 3
        assert "were not examined" in result.render()

    def test_the_result_set_records_that_it_was_cut_short(self, tmp_path):
        result = audit(
            CORPUS / "C03.xlsx",
            client=self._stops_after(2),
            trace_directory=tmp_path,
            max_candidates=10,
        )
        assert "tokens per day" in result.as_dict()["stopped"]

    def test_a_complete_run_records_nothing(self, tmp_path):
        result = audit(
            CORPUS / "C03.xlsx",
            client=AlwaysIntentional(),
            trace_directory=tmp_path,
            max_candidates=2,
        )
        assert result.stopped is None
        assert "stopped early" not in result.render()


class TestRenderingFromTrajectories:
    """Rendering is deterministic, so a report can be produced again from the
    record without paying for the run twice."""

    def test_it_rebuilds_the_report_from_disk(self):
        from materia.audit import from_trajectories

        result = from_trajectories("corpus/C03.xlsx", "trajectories/solution")
        assert len(result.verdicts) == 6
        assert len(result.result.findings) == 2
        assert result.provider == "groq"

    def test_a_trajectory_with_no_verdict_is_skipped(self, tmp_path):
        """A run that died mid candidate leaves one. It is not a verdict and
        must not be counted as one."""
        import shutil

        from materia.audit import from_trajectories
        from materia.trace import Trace

        traces = tmp_path / "traces"
        for source in Path("trajectories/solution").glob("*.jsonl"):
            traces.mkdir(exist_ok=True)
            shutil.copy(source, traces / source.name)

        with Trace(traces / "C03_adjudicator_Costs_Z12_D1.jsonl", "r", "adjudicator") as trace:
            trace.run_start(workbook="C03", cell="Costs!Z12", detector="D1")

        result = from_trajectories("corpus/C03.xlsx", traces)
        assert len(result.verdicts) == 6
        assert "Costs!Z12" not in {v.address for v in result.verdicts}

    def test_an_empty_directory_produces_no_verdicts(self, tmp_path):
        from materia.audit import from_trajectories

        assert from_trajectories("corpus/C03.xlsx", tmp_path).verdicts == ()


class TestRebuildingFromASweep:
    """A sweep puts every workbook's trajectories in one directory.

    `from_trajectories` globbed all of them, so rebuilding one workbook picked
    up the whole corpus's verdicts and reported them as that workbook's. The
    `report` command reads the same way, so a re-rendered report after a sweep
    would have mixed twelve workbooks into one.
    """

    DIRECTORY = "trajectories/solution_scored"

    def test_it_takes_only_the_workbook_it_was_asked_for(self):
        rebuilt = from_trajectories("corpus/C10.xlsx", self.DIRECTORY)
        assert rebuilt.verdicts, "no verdicts rebuilt at all"
        for verdict in rebuilt.verdicts:
            assert "C10" in verdict.trace_path

    def test_two_workbooks_do_not_share_verdicts(self):
        first = {v.address for v in from_trajectories("corpus/C09.xlsx", self.DIRECTORY).verdicts}
        second = {v.address for v in from_trajectories("corpus/C10.xlsx", self.DIRECTORY).verdicts}
        assert first != second

    def test_the_rebuilt_result_carries_what_the_run_spent(self):
        """Otherwise results/ reports a cost of zero for a run that cost money."""
        rebuilt = from_trajectories("corpus/C10.xlsx", self.DIRECTORY)
        assert rebuilt.as_dict()["tokens"]["in"] > 0
        assert rebuilt.as_dict()["tokens"]["out"] > 0

    def test_it_matches_what_the_run_itself_wrote(self):
        import json

        rebuilt = from_trajectories("corpus/C06.xlsx", self.DIRECTORY).as_dict()
        written = json.loads(Path("results/solution/C06.json").read_text())
        assert rebuilt["candidates"] == written["candidates"]
        assert [f["address"] for f in rebuilt["findings"]] == [
            f["address"] for f in written["findings"]
        ]
