"""Report and cross check tests.

The class that matters is TestTheCrossCheck. It is the proof of the design
claim: the model cannot state an impact it has not measured, enforced in code
rather than requested in a prompt.

It is checked twice. Once against a fabricated verdict built here, and once
against a real one, because on the first candidate of the first live run the
model called the tool, received 8704573.0, and reported -6102169. That
trajectory is committed and the test reads it.
"""

import json
from pathlib import Path

import pytest

from materia.adjudicate import Verdict
from materia.corpus.layout import DECLARED_OUTPUTS
from materia.detect import detect, load
from materia.graph import DependencyGraph
from materia.report import (
    Funnel,
    cross_check,
    plain,
    render,
    render_card,
)
from materia.tools import Toolbox
from materia.trace import Trace, read

CORPUS = Path("corpus")
TRAJECTORIES = Path("trajectories/solution")
EBITDA, ENTERPRISE_VALUE = DECLARED_OUTPUTS

REAL_FABRICATION = TRAJECTORIES / "C03_adjudicator_Revenue_H5_D1.jsonl"


@pytest.fixture(scope="module")
def setup():
    tools = Toolbox(CORPUS / "C03.xlsx", DECLARED_OUTPUTS)
    graph = DependencyGraph.of(tools.model)
    candidates = {}
    for candidate in detect(load(CORPUS / "C03.xlsx")):
        candidates.setdefault(candidate.address, candidate)
    return tools, graph, candidates


def a_trace(tmp_path, cell, formula, result, name="run.jsonl"):
    """A trajectory where the model really did call the tool."""
    path = tmp_path / name
    with Trace(path, "r1", "adjudicator") as trace:
        trace.run_start(workbook="C03", cell=cell, detector="D1")
        call = type("Call", (), {"id": "c1", "name": "recompute_with_patch",
                                 "arguments": {"cell": cell, "proposed_formula": formula}})()
        trace.tool_call(call)
        trace.tool_result("c1", "recompute_with_patch", result)
    return str(path)


def a_verdict(cell, formula, claimed, trace_path, verdict="ERROR"):
    return Verdict(
        address=cell,
        detector="D1",
        verdict=verdict,
        confidence="high",
        proposed_formula=formula,
        evidence=("Revenue!F5 uses =E9",),
        reasoning="It does not match its peers.",
        measured_deltas=claimed,
        trace_path=trace_path,
    )


class TestTheCrossCheck:
    """The model cannot state an impact it has not measured."""

    def test_a_fabricated_delta_with_no_tool_call_is_dropped(self, tmp_path):
        """The proof of the design claim.

        This verdict asserts an impact of eight million. Its trajectory shows
        the model never ran the tool. Nothing verifies the number, so the
        finding never reaches the user.
        """
        path = tmp_path / "no_tool.jsonl"
        with Trace(path, "r1", "adjudicator") as trace:
            trace.run_start(workbook="C03", cell="Revenue!H5", detector="D1")

        verdict = a_verdict("Revenue!H5", "=G9", {EBITDA: 8_000_000.0}, str(path))
        result = cross_check([verdict])

        assert result.findings == ()
        assert result.dropped == 1
        assert "unverifiable impact" in str(result.violations[0])

    def test_a_delta_backed_by_a_tool_result_survives(self, tmp_path):
        path = a_trace(tmp_path, "Revenue!H5", "=G9", {EBITDA: 8_704_573.0})
        verdict = a_verdict("Revenue!H5", "=G9", {EBITDA: 8_704_573.0}, path)

        result = cross_check([verdict])
        assert len(result.findings) == 1
        assert result.findings[0].deltas == {EBITDA: 8_704_573.0}
        assert result.violations == ()

    def test_a_tool_result_for_a_different_hypothesis_does_not_count(self, tmp_path):
        """Matched on the call that produced it, not on the numbers. A model
        cannot pass by quoting a figure from some other patch it tried."""
        path = a_trace(tmp_path, "Costs!C5", "=Assumptions!$B$12", {EBITDA: 500.0})
        verdict = a_verdict("Revenue!H5", "=G9", {EBITDA: 500.0}, path)
        assert cross_check([verdict]).findings == ()

    def test_a_verdict_with_no_trajectory_at_all_is_dropped(self):
        verdict = a_verdict("Revenue!H5", "=G9", {EBITDA: 8_000_000.0}, None)
        assert cross_check([verdict]).findings == ()

    def test_a_failed_tool_call_does_not_verify_anything(self, tmp_path):
        path = tmp_path / "failed.jsonl"
        with Trace(path, "r1", "adjudicator") as trace:
            trace.run_start(workbook="C03", cell="Revenue!H5", detector="D1")
            call = type("Call", (), {"id": "c1", "name": "recompute_with_patch",
                                     "arguments": {"cell": "Revenue!H5", "proposed_formula": "=G9"}})()
            trace.tool_call(call)
            trace.tool_result("c1", "recompute_with_patch", {"error": "circular reference"})

        verdict = a_verdict("Revenue!H5", "=G9", {EBITDA: 8_000_000.0}, str(path))
        assert cross_check([verdict]).findings == ()

    def test_mismatched_figures_are_replaced_rather_than_the_finding_lost(self, tmp_path):
        """When the tool result exists and the model reported something else,
        the finding survives with the measured figure.

        Dropping it would lose a real error to a reporting mistake. The number
        the user sees is measured either way, which is the actual guarantee.
        """
        path = a_trace(tmp_path, "Revenue!H5", "=G9", {EBITDA: 8_704_573.0})
        verdict = a_verdict("Revenue!H5", "=G9", {EBITDA: -6_102_169.0}, path)

        result = cross_check([verdict])
        assert len(result.findings) == 1
        assert result.findings[0].deltas == {EBITDA: 8_704_573.0}
        assert result.findings[0].corrected is True
        assert "did not match the trajectory" in str(result.violations[0])


class TestAgainstTheRealFabrication:
    """The same check, against the trajectory it was written for.

    On the first candidate of the first live run the model called the tool,
    received {"P&L!AA15": 8704573.0, "Valuation!B7": 92752830.0}, and reported
    {"P&L!AA15": -6102169, "Valuation!B7": -50782614}. Opposite signs.
    """

    @pytest.mark.skipif(not REAL_FABRICATION.exists(), reason="the T15 trajectory is not present")
    def test_the_trajectory_still_shows_the_fabrication(self):
        records = read(REAL_FABRICATION)
        measured = next(r for r in records if r.type == "tool_result").content["result"]
        claimed = next(r for r in records if r.type == "verdict").content["measured_deltas"]
        assert measured[EBITDA] == 8704573.0
        assert claimed[EBITDA] != measured[EBITDA]

    @pytest.mark.skipif(not REAL_FABRICATION.exists(), reason="the T15 trajectory is not present")
    def test_the_cross_check_catches_it_and_reports_the_measured_figure(self, setup):
        tools, _, _ = setup
        records = read(REAL_FABRICATION)
        start = records[0]
        entry = next(r for r in records if r.type == "verdict")
        verdict = Verdict(
            address=start.content["cell"],
            detector=start.content["detector"],
            verdict=entry.content["verdict"],
            confidence=entry.content["confidence"],
            proposed_formula=entry.content.get("proposed_formula"),
            evidence=tuple(entry.content.get("evidence") or ()),
            reasoning=entry.content.get("reasoning", ""),
            measured_deltas=entry.content["measured_deltas"],
            trace_path=str(REAL_FABRICATION),
        )

        result = cross_check([verdict], tools.model)
        assert result.violations, "the fabrication was not caught"
        assert result.findings[0].deltas[EBITDA] == 8704573.0
        assert result.findings[0].corrected is True


class TestBuckets:
    def test_intentional_and_inconclusive_are_kept_apart(self, tmp_path):
        path = a_trace(tmp_path, "Revenue!H5", "=G9", {EBITDA: 1.0})
        verdicts = [
            a_verdict("Revenue!C5", None, {}, path, verdict="INTENTIONAL"),
            a_verdict("Costs!C5", None, {}, path, verdict="INCONCLUSIVE"),
            a_verdict("Revenue!H5", "=G9", {EBITDA: 1.0}, path),
        ]
        result = cross_check(verdicts)
        assert len(result.intentional) == 1
        assert len(result.inconclusive) == 1
        assert len(result.findings) == 1

    def test_an_intentional_verdict_needs_no_tool_result(self, tmp_path):
        """Declining to flag is a success state, not a claim about impact."""
        path = tmp_path / "quiet.jsonl"
        with Trace(path, "r1", "adjudicator") as trace:
            trace.run_start(workbook="C10", cell="Costs!I12", detector="D1")
        verdict = a_verdict("Costs!I12", None, {}, str(path), verdict="INTENTIONAL")
        assert len(cross_check([verdict]).intentional) == 1


class TestOrdering:
    def test_findings_are_ordered_by_measured_impact(self, tmp_path, setup):
        tools, _, _ = setup
        small = a_trace(tmp_path, "P&L!AA15", "=SUM(C15:Z15)", {EBITDA: 1_550_882.0}, "small.jsonl")
        large = a_trace(tmp_path, "Revenue!H5", "=G9", {EBITDA: 8_704_573.0}, "large.jsonl")
        result = cross_check(
            [
                a_verdict("P&L!AA15", "=SUM(C15:Z15)", {EBITDA: 1_550_882.0}, small),
                a_verdict("Revenue!H5", "=G9", {EBITDA: 8_704_573.0}, large),
            ],
            tools.model,
        )
        assert [f.address for f in result.findings] == ["Revenue!H5", "P&L!AA15"]


class TestRendering:
    @pytest.fixture
    def rendered(self, tmp_path, setup):
        tools, graph, candidates = setup
        path = a_trace(tmp_path, "Revenue!H5", "=G9", {EBITDA: 8_704_573.0, ENTERPRISE_VALUE: 92_752_830.0})
        result = cross_check(
            [a_verdict("Revenue!H5", "=G9", {EBITDA: 8_704_573.0, ENTERPRISE_VALUE: 92_752_830.0}, path)],
            tools.model,
            graph,
            candidates,
        )
        return render("C03.xlsx", result, Funnel(738, 22, 6, 1))

    def test_the_funnel_is_the_four_numbers_from_the_readme(self, rendered):
        assert "738  formulas parsed" in rendered
        assert "22  structural anomalies detected" in rendered
        assert "6  survived hypothesis testing" in rendered
        assert "1  material findings" in rendered

    def test_it_leads_with_the_consequence_not_the_cell(self, rendered):
        """The reader cares that enterprise value is wrong. The cell address
        is how they check it."""
        headline = [line for line in rendered.splitlines() if line.startswith("[1]")][0]
        assert ENTERPRISE_VALUE in headline
        assert "Revenue!H5" not in headline

    def test_a_card_carries_everything_a_reader_needs(self, rendered):
        for expected in ("Cell", "Should be", "Confidence", "Its neighbours", "Measured impact"):
            assert expected in rendered

    def test_suppression_is_stated_rather_than_silent(self, rendered):
        """Suppression the user cannot see is indistinguishable from a bug."""
        assert "WHAT WAS SET ASIDE" in rendered

    def test_there_are_no_em_dashes(self, rendered):
        assert "—" not in rendered

    def test_no_non_ascii_survives_from_model_text(self):
        """The model writes unicode dashes. CLAUDE.md section 5 bans them from
        anything a person reads, and a quoted sentence is still read."""
        assert plain("columns O‑Z") == "columns O-Z"
        assert plain("a thought — an aside") == "a thought, an aside"

    def test_a_report_with_no_findings_says_so(self, tmp_path):
        result = cross_check([])
        assert "No material findings." in render("C09.xlsx", result, Funnel(739, 21, 0, 0))

    def test_a_dropped_finding_is_counted_in_the_open(self, tmp_path):
        path = tmp_path / "no_tool.jsonl"
        with Trace(path, "r1", "adjudicator") as trace:
            trace.run_start(workbook="C03", cell="Revenue!H5", detector="D1")
        result = cross_check([a_verdict("Revenue!H5", "=G9", {EBITDA: 8e6}, str(path))])
        rendered = render("C03.xlsx", result, Funnel(738, 22, 1, 0))
        assert "could not be traced to a measurement" in rendered
        assert "Schema violations" in rendered

    def test_a_finding_that_is_itself_an_output_does_not_print_a_zero_hop_path(
        self, tmp_path, setup
    ):
        tools, graph, candidates = setup
        path = a_trace(tmp_path, EBITDA, "=SUM(C15:Z15)", {EBITDA: 1_550_882.0})
        result = cross_check(
            [a_verdict(EBITDA, "=SUM(C15:Z15)", {EBITDA: 1_550_882.0}, path)],
            tools.model,
            graph,
            candidates,
        )
        rendered = render_card(result.findings[0], 1)
        assert "in 0 steps" not in rendered
        assert f"This cell is {EBITDA}." in rendered


class TestMatchingIsExact:
    def test_a_result_for_a_different_formula_on_the_same_cell_does_not_count(self, tmp_path):
        """The model measured =G8 and proposed =G9. It has not measured =G9,
        and attaching the other number to this claim would be exactly the
        thing the check exists to stop."""
        path = a_trace(tmp_path, "Revenue!H5", "=G8", {EBITDA: 42.0})
        verdict = a_verdict("Revenue!H5", "=G9", {EBITDA: 42.0}, path)
        assert cross_check([verdict]).findings == ()
        assert "unverifiable impact" in str(cross_check([verdict]).violations[0])

    def test_whitespace_around_the_formula_does_not_break_the_match(self, tmp_path):
        path = a_trace(tmp_path, "Revenue!H5", "=G9", {EBITDA: 42.0})
        verdict = a_verdict("Revenue!H5", "  =G9  ", {EBITDA: 42.0}, path)
        assert len(cross_check([verdict]).findings) == 1

    def test_dollar_signs_in_the_address_do_not_break_the_match(self, tmp_path):
        path = a_trace(tmp_path, "Revenue!$H$5", "=G9", {EBITDA: 42.0})
        verdict = a_verdict("Revenue!H5", "=G9", {EBITDA: 42.0}, path)
        assert len(cross_check([verdict]).findings) == 1

    def test_a_verdict_claiming_nothing_is_accepted_as_measured(self, tmp_path):
        """No claim to contradict. The measured figures are used."""
        path = a_trace(tmp_path, "Revenue!H5", "=G9", {EBITDA: 42.0})
        result = cross_check([a_verdict("Revenue!H5", "=G9", {}, path)])
        assert result.findings[0].deltas == {EBITDA: 42.0}
        assert result.violations == ()

    def test_a_claim_about_an_output_the_tool_never_returned(self, tmp_path):
        path = a_trace(tmp_path, "Revenue!H5", "=G9", {EBITDA: 42.0})
        verdict = a_verdict("Revenue!H5", "=G9", {"Sheet!ZZ1": 42.0}, path)
        assert cross_check([verdict]).findings[0].corrected is True

    def test_a_claim_that_is_not_a_number(self, tmp_path):
        path = a_trace(tmp_path, "Revenue!H5", "=G9", {EBITDA: 42.0})
        verdict = a_verdict("Revenue!H5", "=G9", {EBITDA: "a lot"}, path)
        assert cross_check([verdict]).findings[0].corrected is True

    def test_an_output_that_broke_has_no_share_to_report(self, tmp_path, setup):
        tools, _, _ = setup
        path = a_trace(tmp_path, "Revenue!H5", "=G9", {EBITDA: "the output becomes #VALUE!"})
        result = cross_check([a_verdict("Revenue!H5", "=G9", {}, path)], tools.model)
        assert result.findings[0].relative[EBITDA] is None
        assert EBITDA not in result.findings[0].deltas


class TestTheSuppressedCount:
    """The gate lands in T21. The report already has a place to show it,
    because a count the user cannot see is indistinguishable from a bug."""

    def test_the_funnel_shows_suppressed_when_there_is_one(self):
        assert "suppressed as immaterial" in Funnel(738, 22, 6, 2, suppressed=4).render("C03.xlsx")

    def test_the_funnel_leaves_it_out_when_there_is_none(self):
        assert "suppressed" not in Funnel(738, 22, 6, 2).render("C03.xlsx")

    def test_the_report_states_it_alongside_the_other_buckets(self, tmp_path):
        path = a_trace(tmp_path, "Revenue!H5", "=G9", {EBITDA: 42.0})
        result = cross_check([a_verdict("Revenue!C5", None, {}, path, verdict="INTENTIONAL")])
        rendered = render("C03.xlsx", result, Funnel(738, 22, 6, 0, suppressed=3))
        assert "real but below the materiality threshold" in rendered

    def test_the_deliberate_ones_are_listed_with_their_reasons(self, tmp_path):
        """A reader has to be able to disagree with the agent, which means
        seeing why it stayed quiet."""
        path = a_trace(tmp_path, "Revenue!H5", "=G9", {EBITDA: 42.0})
        result = cross_check([a_verdict("Revenue!C5", None, {}, path, verdict="INTENTIONAL")])
        rendered = render("C03.xlsx", result, Funnel(738, 22, 6, 0))
        assert "Judged deliberate" in rendered
        assert "Revenue!C5" in rendered
        assert "does not match its peers" in rendered

    def test_a_corrected_figure_is_flagged_on_the_card(self, tmp_path, setup):
        tools, graph, candidates = setup
        path = a_trace(tmp_path, "Revenue!H5", "=G9", {EBITDA: 8_704_573.0})
        result = cross_check(
            [a_verdict("Revenue!H5", "=G9", {EBITDA: -6_102_169.0}, path)],
            tools.model, graph, candidates,
        )
        card = render_card(result.findings[0], 1)
        assert "reported different figures" in card
