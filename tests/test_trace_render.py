"""Trajectory rendering tests.

micro1 buys agent traces, so these files are read closely. Two things matter:
a reader can follow the run without knowing this codebase, and the index tells
the truth about what is and is not there.
"""

from pathlib import Path

import pytest

from materia.trace_render import (
    FEATURED,
    build_index,
    render,
    write_featured,
    write_index,
)

CLEAN_WIN = "trajectories/solution/C03_adjudicator_P&L_AA15_D2.jsonl"
THE_CHECK = "trajectories/solution/C03_adjudicator_Revenue_H5_D1.jsonl"


class TestRendering:
    @staticmethod
    @pytest.fixture(scope="class")
    def rendered():
        return render(CLEAN_WIN)

    def test_it_names_the_run_the_cell_and_the_model(self, rendered):
        assert "agent `adjudicator`" in rendered
        assert "`P&L!AA15`" in rendered
        assert "openai/gpt-oss-120b" in rendered

    def test_it_shows_the_detector_reason(self, rendered):
        """What the model was told, before anything it decided."""
        assert "Normalises to" in rendered

    def test_it_shows_every_step_in_order(self, rendered):
        steps = [
            int(line.split()[2].rstrip(","))
            for line in rendered.splitlines()
            if line.startswith("### Step ")
        ]
        assert steps == sorted(steps)
        assert len(steps) > 5

    def test_tool_results_are_shown_as_data(self, rendered):
        """The figure in the report has to be findable here."""
        assert "recompute_with_patch` returned" in rendered
        assert "1550882" in rendered

    def test_the_verdict_is_shown_with_its_evidence(self, rendered):
        assert "**ERROR**" in rendered
        assert "Proposed formula: `=SUM(C15:Z15)`" in rendered
        assert "Evidence given" in rendered

    def test_it_points_back_at_the_raw_file(self, rendered):
        assert CLEAN_WIN in rendered

    def test_a_preamble_is_placed_before_the_run(self, rendered):
        with_preamble = render(CLEAN_WIN, "Watch step four.")
        assert "## What to watch for" in with_preamble
        assert with_preamble.index("Watch step four.") < with_preamble.index("## The run")

    def test_no_em_dashes_or_other_non_ascii(self, rendered):
        assert not [c for c in rendered if ord(c) > 127]

    def test_an_empty_trajectory_is_refused(self, tmp_path):
        empty = tmp_path / "empty.jsonl"
        empty.write_text("")
        with pytest.raises(ValueError, match="no records"):
            render(empty)

    def test_a_missing_file_is_refused(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            render(tmp_path / "nope.jsonl")


class TestTheCheckFiringIsReadable:
    """The trajectory that matters most has to make its own case."""

    @staticmethod
    @pytest.fixture(scope="class")
    def rendered():
        item = next(f for f in FEATURED if f.slug == "the-check-firing")
        return render(THE_CHECK, item.preamble)

    def test_both_numbers_are_visible(self, rendered):
        """A reader has to be able to see the discrepancy without being told
        it is there."""
        assert "8704573" in rendered
        assert "-6102169" in rendered

    def test_the_preamble_says_where_to_look(self, rendered):
        assert "step 4" in rendered
        assert "step 7" in rendered

    def test_it_does_not_claim_this_was_intended(self, rendered):
        assert "nobody planned it" in rendered
        assert "tested by reality" in rendered


class TestTheFeaturedList:
    def test_the_two_that_exist_are_marked_available(self):
        available = {f.slug for f in FEATURED if f.available}
        assert available == {"clean-win", "the-check-firing"}

    def test_every_entry_carries_a_preamble(self):
        """Including the missing ones. A gap with no explanation is worse than
        a gap."""
        for item in FEATURED:
            assert len(item.preamble) > 100, item.slug

    def test_the_missing_ones_say_why(self):
        for item in FEATURED:
            if item.available:
                continue
            assert any(
                phrase in item.preamble
                for phrase in ("has not been run", "has not been built", "does not exist",
                               "has not happened", "No adjudication")
            ), item.slug

    def test_none_of_them_claims_a_run_that_did_not_happen(self):
        for item in FEATURED:
            if not item.available:
                assert item.path is None, item.slug


class TestTheIndex:
    @staticmethod
    @pytest.fixture(scope="class")
    def index():
        return build_index("trajectories")

    def test_it_lists_the_available_featured_trajectories_first(self, index):
        assert index.index("## Start here") < index.index("## Every trajectory")
        assert "1. The clean win" in index
        assert "5. The cross check catching an invented figure" in index

    def test_it_names_what_is_missing_rather_than_omitting_it(self, index):
        """A missing trajectory and a trajectory nobody looked for read the
        same way unless one of them says so."""
        assert "## Not present, and why" in index
        assert "2. Declining to flag a deliberate break" in index
        assert "4. The baseline reporting errors in a clean workbook" in index

    def test_every_trajectory_on_disk_has_a_row(self, index):
        traces = list(Path("trajectories").rglob("*.jsonl"))
        rows = [line for line in index.splitlines() if line.startswith("| `sol-")
                or line.startswith("| `repair-")]
        assert len(rows) == len(traces)

    def test_a_row_says_which_run_it_came_from(self, index):
        """Two runs adjudicated P&L!AA15 and disagreed. Without the file column
        a reader cannot tell which row is which."""
        assert "solution/C03_adjudicator_P&L_AA15_D2.jsonl" in index
        assert "solution_full/C03_adjudicator_P&L_AA15_D2.jsonl" in index

    def test_no_em_dashes(self, index):
        assert "—" not in index


class TestWriting:
    def test_it_writes_a_file_per_available_trajectory(self, tmp_path):
        import shutil

        shutil.copytree("trajectories/solution", tmp_path / "solution")
        written = write_featured(tmp_path)
        assert {p.name for p in written} == {"1-clean-win.md", "5-the-check-firing.md"}

    def test_the_rendered_file_carries_its_preamble(self):
        text = Path("trajectories/featured/5-the-check-firing.md").read_text()
        assert "## What to watch for" in text
        assert "8704573" in text

    def test_the_index_is_written_where_the_doc_says(self, tmp_path):
        import shutil

        shutil.copytree("trajectories/solution", tmp_path / "solution")
        assert write_index(tmp_path) == tmp_path / "index.md"


class TestEveryRecordShape:
    """The renderer has a branch per record type, and a run that goes wrong
    produces shapes a clean run never does. Those are the runs a reader most
    needs to be able to follow.

    Built here rather than taken from a run: this is testing the renderer, not
    standing in for a featured trajectory.
    """

    @staticmethod
    @pytest.fixture(scope="class")
    def rendered(tmp_path_factory):
        from materia.llm import AgentResponse, ToolCall, Usage
        from materia.trace import Trace

        path = tmp_path_factory.mktemp("shapes") / "everything.jsonl"
        call = ToolCall("c1", "recompute_with_patch", {"cell": "Revenue!H5"})

        with Trace(path, "sol-C03-test", "adjudicator") as trace:
            trace.run_start(workbook="C03", cell="Revenue!H5", detector="D1")
            trace.model_message(
                AgentResponse(
                    text="Let me look at the neighbours first.",
                    tool_calls=(call,),
                    stop_reason="tool_calls",
                    usage=Usage(900, 60),
                    model="m",
                    provider="groq",
                ),
                latency_ms=700,
            )
            trace.tool_call(call)
            trace.tool_result("c1", "recompute_with_patch", None, error="circular reference")
            trace.model_message(AgentResponse(text=None, stop_reason="stop"))
            trace.record("model_message", {"error": "Groq request failed: 400"})
            trace.verdict(
                {"verdict": "INCONCLUSIVE", "confidence": "low", "reasoning": "unclear",
                 "status": "schema_violation"}
            )
            trace.human_checkpoint(
                "repair_approval", "declined", cell="Revenue!H5", proposed_formula="=G9"
            )
            trace.record("run_end", {"status": "ok", "turns": 4})
        return render(path)

    def test_model_text_is_shown(self, rendered):
        assert "Let me look at the neighbours first." in rendered

    def test_a_reply_with_nothing_in_it_says_so(self, rendered):
        assert "_No text and no tool call._" in rendered

    def test_a_provider_refusal_is_shown_as_such(self, rendered):
        """My own resilience fix records these. A reader seeing a gap would
        assume the model went quiet."""
        assert "The provider refused this request" in rendered

    def test_a_failed_tool_call_is_shown(self, rendered):
        assert "The tool reported an error: circular reference" in rendered

    def test_a_verdict_status_is_shown(self, rendered):
        assert "Status: schema_violation" in rendered

    def test_a_human_checkpoint_is_shown_with_what_was_offered(self, rendered):
        """The brief requires consequential actions to be gated. A decline is
        part of that record."""
        assert "**declined** at a `repair_approval` checkpoint" in rendered
        assert "Change offered: `=G9`" in rendered

    def test_a_record_shape_the_renderer_does_not_know_is_still_printed(self):
        """The forward guard. If a record type is added without a rendering
        branch, it has to appear as raw content rather than vanish, because a
        trajectory that silently omits a step is worse than an ugly one.
        """
        from materia.trace import Record
        from materia.trace_render import _render_record

        rendered = _render_record(
            Record(
                ts="2026-01-01T00:00:00.000Z", run_id="r", agent="adjudicator",
                step=9, type="something_new", content={"unexpected": "shape"},
            )
        )
        assert "something new" in rendered
        assert "unexpected" in rendered

    def test_an_unknown_record_type_is_still_printed(self, tmp_path):
        """A record shape the renderer does not know about must not vanish."""
        from materia.trace import RECORD_TYPES, Trace

        path = tmp_path / "future.jsonl"
        with Trace(path, "r", "adjudicator") as trace:
            trace.run_start(workbook="C03", cell="A1", detector="D1")
            object.__setattr__(trace, "_step", trace.steps)
            trace._handle.write(
                '{"ts": "2026-01-01T00:00:00.000Z", "run_id": "r", "agent": "a", '
                '"step": 2, "type": "run_end", "content": {"unexpected": "shape"}, '
                '"tokens": {"in": 0, "out": 0}, "latency_ms": 0}\n'
            )
            trace._handle.flush()
        assert "unexpected" in render(path)
        assert "run_end" in RECORD_TYPES
