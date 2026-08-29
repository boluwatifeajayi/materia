"""Trace capture tests.

A trace is a required deliverable and micro1 buys agent traces, so these
files are read by people who know what good looks like. Two properties carry
the weight: the record ordering reflects what actually happened, and a tool
result is stored as data so a reported figure can be checked against it.
"""

import json

import pytest

from materia.llm import AgentResponse, ToolCall, Usage
from materia.trace import (
    RECORD_TYPES,
    Timer,
    Trace,
    new_run_id,
    read,
    tool_results,
    total_tokens,
)

CALL = ToolCall("c1", "recompute_with_patch", {"cell": "P&L!AA15"})
REPLY = AgentResponse(
    text="Testing the hypothesis.",
    tool_calls=(CALL,),
    stop_reason="tool_use",
    usage=Usage(900, 60),
    model="openai/gpt-oss-120b",
    provider="groq",
)


@pytest.fixture
def trace_path(tmp_path):
    return tmp_path / "run.jsonl"


def a_full_run(path) -> str:
    """One of every record type, in the order a real run produces them."""
    run_id = new_run_id("sol", "C03")
    with Trace(path, run_id, "adjudicator") as trace:
        trace.run_start(workbook="C03", cell="P&L!AA15", detector="D3")
        trace.model_message(REPLY, latency_ms=812)
        trace.tool_call(CALL)
        trace.tool_result("c1", "recompute_with_patch", {"P&L!AA15": 1130459.0}, latency_ms=41)
        trace.verdict({"verdict": "ERROR", "confidence": "high"})
        trace.human_checkpoint("repair_approval", "declined", finding="P&L!AA15")
        trace.run_end("ok")
    return run_id


class TestTheFileIsWellFormed:
    def test_every_line_is_valid_json(self, trace_path):
        a_full_run(trace_path)
        for line in trace_path.read_text().splitlines():
            json.loads(line)

    def test_every_record_carries_the_documented_fields(self, trace_path):
        a_full_run(trace_path)
        for line in trace_path.read_text().splitlines():
            record = json.loads(line)
            assert set(record) == {
                "ts",
                "run_id",
                "agent",
                "step",
                "type",
                "content",
                "tokens",
                "latency_ms",
            }

    def test_timestamps_are_utc_with_milliseconds(self, trace_path):
        a_full_run(trace_path)
        for record in read(trace_path):
            assert record.ts.endswith("Z")
            assert "." in record.ts

    def test_the_run_id_is_shared_by_every_record(self, trace_path):
        run_id = a_full_run(trace_path)
        assert {record.run_id for record in read(trace_path)} == {run_id}


class TestOrdering:
    def test_steps_are_sequential_from_one(self, trace_path):
        a_full_run(trace_path)
        assert [record.step for record in read(trace_path)] == list(range(1, 8))

    def test_the_order_is_the_order_things_happened(self, trace_path):
        a_full_run(trace_path)
        assert [record.type for record in read(trace_path)] == [
            "run_start",
            "model_message",
            "tool_call",
            "tool_result",
            "verdict",
            "human_checkpoint",
            "run_end",
        ]

    def test_every_documented_record_type_appears(self, trace_path):
        a_full_run(trace_path)
        assert {record.type for record in read(trace_path)} == set(RECORD_TYPES)

    def test_an_unknown_record_type_is_refused(self, trace_path):
        with Trace(trace_path, "r", "a") as trace:
            with pytest.raises(ValueError, match="unknown record type"):
                trace.record("guesswork", {})


class TestContent:
    def test_a_model_message_records_what_it_cost(self, trace_path):
        a_full_run(trace_path)
        message = next(r for r in read(trace_path) if r.type == "model_message")
        assert message.tokens == {"in": 900, "out": 60}
        assert message.latency_ms == 812
        assert message.content["provider"] == "groq"
        assert message.content["stop_reason"] == "tool_use"

    def test_a_model_message_records_the_tools_it_asked_for(self, trace_path):
        a_full_run(trace_path)
        message = next(r for r in read(trace_path) if r.type == "model_message")
        assert message.content["tool_calls"][0]["name"] == "recompute_with_patch"

    def test_a_tool_result_stores_data_not_prose(self, trace_path):
        """The reporter checks a reported delta against this record. If it were
        stored as text there would be nothing to check against."""
        a_full_run(trace_path)
        result = next(tool_results(read(trace_path)))
        assert result.content["result"] == {"P&L!AA15": 1130459.0}
        assert result.content["id"] == "c1"

    def test_tool_results_can_be_filtered_by_tool(self, trace_path):
        a_full_run(trace_path)
        records = read(trace_path)
        assert len(list(tool_results(records, "recompute_with_patch"))) == 1
        assert len(list(tool_results(records, "inspect_range"))) == 0

    def test_a_failed_tool_call_records_the_error(self, trace_path):
        with Trace(trace_path, "r", "a") as trace:
            trace.tool_result("c1", "recompute_with_patch", None, error="circular reference")
        assert read(trace_path)[0].content["error"] == "circular reference"

    def test_a_human_checkpoint_records_the_decision(self, trace_path):
        """A decline is as much a part of the record as an approval."""
        a_full_run(trace_path)
        checkpoint = next(r for r in read(trace_path) if r.type == "human_checkpoint")
        assert checkpoint.content["decision"] == "declined"
        assert checkpoint.content["kind"] == "repair_approval"

    def test_tokens_are_totalled_across_a_run(self, trace_path):
        a_full_run(trace_path)
        assert total_tokens(read(trace_path)) == {"in": 900, "out": 60}


class TestWrittenAsItGoes:
    """Nothing is reconstructed after the fact."""

    def test_records_are_readable_before_the_run_finishes(self, trace_path):
        trace = Trace(trace_path, "r", "a")
        trace.run_start(workbook="C03")
        trace.tool_call(CALL)
        assert len(read(trace_path)) == 2  # while the trace is still open
        trace.close()

    def test_a_crash_still_leaves_a_readable_trace(self, trace_path):
        """The case where a trace is most useful is the one where the run
        did not finish."""
        with pytest.raises(RuntimeError):
            with Trace(trace_path, "r", "a") as trace:
                trace.run_start(workbook="C03")
                raise RuntimeError("the model went away")

        records = read(trace_path)
        assert records[-1].type == "run_end"
        assert records[-1].content["status"] == "failed"
        assert "the model went away" in records[-1].content["error"]

    def test_the_step_count_is_readable_mid_run(self, trace_path):
        """The agent loop uses it to enforce a turn cap, so it has to be right
        while the run is still going rather than only at the end."""
        trace = Trace(trace_path, "r", "a")
        assert trace.steps == 0
        trace.run_start(workbook="C03")
        trace.tool_call(CALL)
        assert trace.steps == 2
        trace.close()

    def test_a_second_run_replaces_the_first_rather_than_joining_it(self, trace_path):
        """This used to append. A rerun of one workbook then produced a file
        with two run_starts, two run_ends and step numbers restarting halfway
        down, which the index read as a single run of double the length.
        Nothing reads a trace expecting more than one run in it."""
        a_full_run(trace_path)
        a_full_run(trace_path)
        records = read(trace_path)
        assert len(records) == 7
        assert len([r for r in records if r.type == "run_start"]) == 1
        assert [r.step for r in records] == sorted(r.step for r in records)


class TestHelpers:
    def test_a_run_id_names_the_workbook(self):
        run_id = new_run_id("sol", "C03")
        assert run_id.startswith("sol-C03-")
        assert new_run_id("sol", "C03") != run_id  # and is unique per run

    def test_the_timer_measures_in_milliseconds(self):
        with Timer() as timer:
            sum(range(200_000))
        assert timer.elapsed_ms >= 0

    def test_blank_lines_are_tolerated_when_reading(self, trace_path):
        a_full_run(trace_path)
        trace_path.write_text(trace_path.read_text() + "\n\n")
        assert len(read(trace_path)) == 7
