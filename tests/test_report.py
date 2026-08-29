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
        is how they check it, not why they opened the report."""
        after_findings = rendered.split("FINDINGS", 1)[1]
        headline = next(
            line for line in after_findings.splitlines() if line.strip().startswith("1  ")
        )
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

    def test_it_handles_what_model_prose_actually_contains(self):
        """Taken from a real report writer reply: arrows, ellipses and curly
        quotes, none of which render predictably in a terminal."""
        assert plain("Revenue!H5 \u2192 \u2026 \u2192 Valuation!B7") == (
            "Revenue!H5 -> ... -> Valuation!B7"
        )
        assert plain("the \u201cactuals\u201d row") == 'the "actuals" row'
        assert plain("O\u2011Z") == "O-Z"

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

    def test_a_finding_that_is_itself_an_output_prints_no_path(self, tmp_path, setup):
        """A path from a cell to itself is not information."""
        tools, graph, candidates = setup
        path = a_trace(tmp_path, EBITDA, "=SUM(C15:Z15)", {EBITDA: 1_550_882.0})
        result = cross_check(
            [a_verdict(EBITDA, "=SUM(C15:Z15)", {EBITDA: 1_550_882.0}, path)],
            tools.model,
            graph,
            candidates,
        )
        rendered = render_card(result.findings[0], 1)
        assert "0 steps" not in rendered
        assert "How it reaches" not in rendered


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


class TestABoundedRunSaysSo:
    """A run that was cut short must not read as a clean bill of health."""

    def test_the_funnel_names_how_many_were_tested(self):
        rendered = Funnel(738, 22, 1, 1, adjudicated=17).render("C03.xlsx")
        assert "22  structural anomalies detected" in rendered
        assert "17  tested, this run was limited" in rendered

    def test_it_says_the_rest_were_not_cleared(self):
        rendered = Funnel(738, 22, 1, 1, adjudicated=17).render("C03.xlsx")
        assert "5 candidates were not examined" in rendered
        assert "not cleared" in rendered

    def test_a_complete_run_says_nothing_extra(self):
        rendered = Funnel(738, 22, 1, 1, adjudicated=22).render("C03.xlsx")
        assert "were not examined" not in rendered
        assert "this run was limited" not in rendered

    def test_a_run_with_no_bound_recorded_reads_as_complete(self):
        assert Funnel(738, 22, 1, 1).complete is True


class TestPresentation:
    """The style rules in CLAUDE.md section 5 are judged, so they are checked.

    The submission is scored partly on whether the output reads as clearly AI
    generated. That is not something to hope about.
    """

    @pytest.fixture
    def real_report(self):
        """The C03 report, rebuilt from the trajectories on disk."""
        from materia.audit import from_trajectories

        return from_trajectories("corpus/C03.xlsx", "trajectories/solution").render()

    def test_no_em_dashes(self, real_report):
        assert "—" not in real_report
        assert "―" not in real_report

    def test_no_characters_outside_ascii(self, real_report):
        """Model prose arrives with unicode dashes and quotes. A terminal at a
        demo font size renders them inconsistently."""
        offenders = sorted({c for c in real_report if ord(c) > 127})
        assert not offenders, offenders

    def test_no_emoji(self, real_report):
        assert not any(0x1F300 <= ord(c) <= 0x1FAFF for c in real_report)

    def test_every_line_fits_a_terminal(self, real_report):
        """Anything wider wraps, and the wrap lands in the middle of a number."""
        from materia.report import WIDTH

        too_wide = [line for line in real_report.splitlines() if len(line) > WIDTH]
        assert not too_wide, too_wide[:3]

    def test_no_preamble_and_no_sign_off(self, real_report):
        lines = [line for line in real_report.splitlines() if line.strip()]
        assert lines[0].startswith("MODEL HEALTH")
        for phrase in ("I have", "I've", "Let me", "Here is", "Hope this", "feel free"):
            assert phrase not in real_report

    def test_it_reads_as_a_tool_not_a_chatbot(self, real_report):
        for phrase in ("Great", "Certainly", "Sure,", "As an AI", "I'd be happy"):
            assert phrase not in real_report


class TestTheRealC03Report:
    """Rendered from the committed trajectories, so this checks the artefact a
    reader actually gets rather than a constructed one."""

    @pytest.fixture
    def audit_result(self):
        from materia.audit import from_trajectories

        return from_trajectories("corpus/C03.xlsx", "trajectories/solution")

    def test_it_finds_both_seeded_mutations(self, audit_result):
        import json

        manifest = json.loads(Path("corpus/manifest.json").read_text())
        entry = next(e for e in manifest["workbooks"] if e["id"] == "C03")
        seeded = {m["address"] for m in entry["mutations"]}
        reported = {f.address for f in audit_result.result.findings}
        assert seeded == reported

    def test_it_reports_nothing_that_was_not_seeded(self, audit_result):
        """Six candidates went in, four of them legitimate. None of the four
        became a finding."""
        assert len(audit_result.result.findings) == 2
        assert len(audit_result.result.intentional) == 4

    def test_the_fabricated_figures_never_appear(self, audit_result):
        """The model reported -6102169. The engine returned 8704573. Only one
        of those may reach a reader."""
        rendered = audit_result.render()
        assert "6,102,169" not in rendered
        assert "8,704,573" in rendered

    def test_the_funnel_narrows_to_the_findings(self, audit_result):
        funnel = audit_result.funnel
        assert funnel.formulas == 738
        assert funnel.candidates == 22
        assert funnel.findings == 2

    def test_the_report_says_how_many_were_not_examined(self, audit_result):
        assert "16 candidates were not examined" in audit_result.render()


class TestAnErrorWithNoProposedFormula:
    """The hole next to the exact formula match.

    Matching is on cell plus the exact formula the verdict proposes. But an
    ERROR verdict is allowed to arrive with no proposed formula, and then
    there is no formula to match on. Falling back to cell alone would attach
    whichever hypothesis the model happened to try first to a claim that is
    not about it.
    """

    def test_a_delta_from_an_earlier_hypothesis_is_not_borrowed(self, tmp_path):
        from materia.trace import Trace

        path = tmp_path / "two_tries.jsonl"
        with Trace(path, "r1", "adjudicator") as trace:
            trace.run_start(workbook="C03", cell="Revenue!H5", detector="D1")
            for identifier, formula, delta in (
                ("c1", "=G8", 111.0),
                ("c2", "=G9", 222.0),
            ):
                call = type("Call", (), {
                    "id": identifier, "name": "recompute_with_patch",
                    "arguments": {"cell": "Revenue!H5", "proposed_formula": formula},
                })()
                trace.tool_call(call)
                trace.tool_result(identifier, "recompute_with_patch", {EBITDA: delta})

        verdict = Verdict(
            address="Revenue!H5", detector="D1", verdict="ERROR", confidence="high",
            proposed_formula=None, evidence=("its peers use =E9",),
            reasoning="It breaks the row.", measured_deltas={}, trace_path=str(path),
        )
        result = cross_check([verdict])

        assert result.findings == (), "a verdict with no hypothesis cannot be verified"
        assert "unverifiable impact" in str(result.violations[0])

    def test_the_reason_says_no_formula_was_proposed(self, tmp_path):
        from materia.trace import Trace

        path = tmp_path / "none.jsonl"
        with Trace(path, "r1", "adjudicator") as trace:
            trace.run_start(workbook="C03", cell="Revenue!H5", detector="D1")

        verdict = Verdict(
            address="Revenue!H5", detector="D1", verdict="ERROR", confidence="high",
            proposed_formula=None, evidence=(), reasoning="", measured_deltas={},
            trace_path=str(path),
        )
        assert "no proposed formula" in str(cross_check([verdict]).violations[0])
