"""Report writer agent tests.

No API calls. The writer has no tools and touches no number, so what matters
is that the prompt matches the doc, that it is handed only verified figures,
and that a figure it produces which was not in the brief is caught.
"""

from pathlib import Path

import pytest

from materia.audit import from_trajectories
from materia.llm import AgentResponse, Usage
from materia.prompts.reporter import SYSTEM_PROMPT
from materia.trace import read
from materia.write_report import (
    build_brief,
    figures_in,
    unsupported_figures,
    write_report,
)


class Writes:
    provider, model = "scripted", "scripted-1"

    def __init__(self, prose):
        self.prose = prose
        self.system = None
        self.brief = None

    def complete(self, system, messages, tools=None):
        self.system, self.brief = system, messages[0].content
        assert not tools, "the report writer has no tools"
        return AgentResponse(
            text=self.prose, stop_reason="stop", usage=Usage(900, 300),
            model="scripted-1", provider="scripted",
        )


@pytest.fixture(scope="module")
def audited():
    return from_trajectories("corpus/C03.xlsx", "trajectories/solution")


class TestThePromptComesFromTheDoc:
    def test_it_matches_the_document(self):
        doc = Path("docs/AGENT_INSTRUCTIONS.md").read_text()
        section = doc.split("## 2. Report writer agent")[1].split("\n---")[0]
        assert SYSTEM_PROMPT.strip() == section.split("```")[1].strip()

    def test_it_tells_the_writer_not_to_touch_the_figures(self):
        assert "do not recompute, adjust, or reinterpret" in SYSTEM_PROMPT

    def test_it_forbids_a_preamble_and_a_sign_off(self):
        assert "No preamble" in SYSTEM_PROMPT
        assert "offers of further help" in SYSTEM_PROMPT


class TestTheBrief:
    def test_it_carries_the_measured_figures(self, audited):
        brief = build_brief("C03.xlsx", audited.result, audited.funnel)
        assert "8,704,573" in brief
        assert "92,752,830" in brief

    def test_it_never_carries_the_fabricated_ones(self, audited):
        """The writer cannot repeat a number it was never shown."""
        brief = build_brief("C03.xlsx", audited.result, audited.funnel)
        assert "6,102,169" not in brief
        assert "50,782,614" not in brief

    def test_findings_arrive_already_ordered_by_impact(self, audited):
        brief = build_brief("C03.xlsx", audited.result, audited.funnel)
        assert brief.index("1. Revenue!H5") < brief.index("2. P&L!AA15")

    def test_it_states_what_was_set_aside(self, audited):
        brief = build_brief("C03.xlsx", audited.result, audited.funnel)
        assert "judged deliberate" in brief
        assert "Judged deliberate and not reported" in brief

    def test_it_says_the_figures_are_already_measured(self, audited):
        brief = build_brief("C03.xlsx", audited.result, audited.funnel)
        assert "measured by a deterministic engine" in brief

    def test_a_workbook_with_no_findings_says_none(self, audited):
        from materia.report import CrossCheck, Funnel

        empty = CrossCheck(findings=(), violations=(), intentional=(), inconclusive=())
        brief = build_brief("C09.xlsx", empty, Funnel(739, 21, 0, 0))
        assert "none" in brief


class TestFigureChecking:
    """The adjudicator's rule, applied to prose."""

    def test_it_finds_numbers_however_they_are_punctuated(self):
        assert figures_in("8,704,573 and 92752830") == {"8704573", "92752830"}

    def test_a_number_at_the_end_of_a_sentence_keeps_no_full_stop(self):
        assert figures_in("understated by 8704573.") == {"8704573"}

    def test_a_figure_not_in_the_brief_is_caught(self):
        assert unsupported_figures("overstated by 9,999,999", "impact 8704573") == {"9999999"}

    def test_a_figure_from_the_brief_is_accepted(self):
        assert unsupported_figures("overstated by 8,704,573", "impact 8704573") == set()

    def test_small_numbers_are_left_alone(self):
        """Ordinals and counts, not impact figures."""
        assert unsupported_figures("2 findings, the 1st is worst", "impact 8704573") == set()


class TestWriting:
    def test_it_returns_the_prose_and_traces_the_call(self, audited, tmp_path):
        client = Writes("EBITDA is understated by 8,704,573.")
        prose, invented, trace = write_report(
            "C03.xlsx", audited.result, audited.funnel, client, tmp_path
        )
        assert prose.startswith("EBITDA is understated")
        assert invented == set()

        records = read(trace)
        assert [r.type for r in records] == ["run_start", "model_message", "run_end"]
        assert records[-1].content["status"] == "ok"

    def test_it_is_given_the_documented_prompt(self, audited, tmp_path):
        client = Writes("x")
        write_report("C03.xlsx", audited.result, audited.funnel, client, tmp_path)
        assert client.system == SYSTEM_PROMPT

    def test_an_invented_figure_is_caught_and_recorded(self, audited, tmp_path):
        """The same guarantee as the adjudicator's. A number with no source is
        a number somebody made up, wherever in the pipeline it appears."""
        client = Writes("Enterprise value is overstated by 12,345,678.")
        _, invented, trace = write_report(
            "C03.xlsx", audited.result, audited.funnel, client, tmp_path
        )
        assert invented == {"12345678"}
        assert read(trace)[-1].content["status"] == "schema_violation"

    def test_the_writer_is_offered_no_tools(self, audited, tmp_path):
        """Asserted inside the scripted client. It has nothing to measure with
        and nothing to measure."""
        write_report("C03.xlsx", audited.result, audited.funnel, Writes("x"), tmp_path)

    def test_unicode_dashes_are_stripped_from_the_prose(self, audited, tmp_path):
        prose, _, _ = write_report(
            "C03.xlsx", audited.result, audited.funnel,
            Writes("a thought — an aside"), tmp_path,
        )
        assert "—" not in prose
