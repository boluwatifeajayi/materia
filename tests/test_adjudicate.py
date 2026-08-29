"""Adjudicator tests.

No API calls. The loop is driven by a scripted client, so every branch is
reachable and the tests say what the loop does rather than what a model
happened to do on one day.

The live behaviour is exercised separately, in the T15 run recorded under
trajectories/solution.
"""

import json
from pathlib import Path

import pytest

from materia.adjudicate import (
    MAX_TURNS,
    VERDICT_TOOL,
    SchemaViolation,
    Verdict,
    adjudicate,
    adjudicate_one,
    build_user_message,
    normalise_verdict,
    parse_verdict,
)
from materia.corpus.layout import DECLARED_OUTPUTS
from materia.detect import Candidate, detect, load
from materia.graph import DependencyGraph
from materia.llm import AgentResponse, ToolCall, Usage
from materia.prompts.adjudicator import SYSTEM_PROMPT, USER_TEMPLATE
from materia.tools import Toolbox
from materia.trace import read

CORPUS = Path("corpus")
EBITDA = DECLARED_OUTPUTS[0]


class ScriptedClient:
    """Replays a fixed list of responses and records what it was sent."""

    provider = "scripted"
    model = "scripted-1"

    def __init__(self, *responses: AgentResponse) -> None:
        self._responses = list(responses)
        self.requests: list[tuple[str, list, list]] = []

    def complete(self, system, messages, tools=None):
        self.requests.append((system, list(messages), list(tools or [])))
        if not self._responses:
            return verdict_response("INCONCLUSIVE", reasoning="ran out of script")
        return self._responses.pop(0)


def verdict_response(verdict, **fields) -> AgentResponse:
    arguments = {
        "verdict": verdict,
        "confidence": fields.pop("confidence", "high"),
        "evidence": fields.pop("evidence", ["Revenue!F5 uses =E9"]),
        "reasoning": fields.pop("reasoning", "It matches the peer pattern."),
        **fields,
    }
    return AgentResponse(
        text=None,
        tool_calls=(ToolCall("v1", VERDICT_TOOL.name, arguments),),
        stop_reason="tool_calls",
        usage=Usage(900, 60),
        model="scripted-1",
        provider="scripted",
    )


def tool_response(name, arguments) -> AgentResponse:
    return AgentResponse(
        text=None,
        tool_calls=(ToolCall("t1", name, arguments),),
        stop_reason="tool_calls",
        usage=Usage(800, 40),
        model="scripted-1",
        provider="scripted",
    )


@pytest.fixture(scope="module")
def setup():
    tools = Toolbox(CORPUS / "C03.xlsx", DECLARED_OUTPUTS)
    graph = DependencyGraph.of(tools.model)
    candidates = {}
    for candidate in detect(load(CORPUS / "C03.xlsx")):
        candidates.setdefault(candidate.address, candidate)
    return tools, graph, candidates


def run_one(setup, tmp_path, *responses, address="Revenue!H5") -> Verdict:
    tools, graph, candidates = setup
    client = ScriptedClient(*responses)
    return adjudicate_one(candidates[address], client, tools, graph, "C03", tmp_path)


class TestThePromptComesFromTheDoc:
    def test_the_system_prompt_matches_the_document(self):
        """docs/AGENT_INSTRUCTIONS.md is a required deliverable. A deliverable
        describing a prompt nobody ran would be worse than none."""
        doc = Path("docs/AGENT_INSTRUCTIONS.md").read_text()
        section = doc.split("## 1. Adjudicator agent")[1].split("\n---")[0]
        blocks = section.split("```")
        assert SYSTEM_PROMPT.strip() == blocks[1].strip()
        assert USER_TEMPLATE.strip() == blocks[3].strip()

    def test_the_prompt_never_mentions_the_gate_s_verdict(self):
        """IMMATERIAL belongs to the gate. A prompt that offered it would let
        the model decide consequence, which is the split the design rests on."""
        assert "IMMATERIAL" not in VERDICT_TOOL.parameters["properties"]["verdict"]["enum"]

    def test_intentional_is_presented_as_a_success(self):
        assert "INTENTIONAL is a correct and valuable answer" in SYSTEM_PROMPT


class TestTheUserMessage:
    def test_it_carries_the_evidence_the_rules_demand(self, setup):
        """Rule 2 tells the model to return INCONCLUSIVE without a peer
        pattern, so the message has to supply one."""
        tools, graph, candidates = setup
        message = build_user_message(candidates["Revenue!H5"], "C03.xlsx", tools, graph)
        assert "Peer group" in message
        assert "Revenue!F5" in message
        assert "Detector that fired: D1" in message
        assert EBITDA in message

    def test_a_cell_comment_is_included_when_there_is_one(self):
        tools = Toolbox(CORPUS / "C10.xlsx", DECLARED_OUTPUTS)
        graph = DependencyGraph.of(tools.model)
        candidate = next(
            c for c in detect(load(CORPUS / "C10.xlsx")) if c.address == "Costs!I12"
        )
        message = build_user_message(candidate, "C10.xlsx", tools, graph)
        assert "board" in message.lower()

    def test_a_long_dependency_path_is_shortened(self, setup):
        tools, graph, candidates = setup
        message = build_user_message(candidates["Revenue!H5"], "C03.xlsx", tools, graph)
        assert "more hops" in message

    def test_no_placeholder_is_left_unfilled(self, setup):
        tools, graph, candidates = setup
        for address in list(candidates)[:5]:
            message = build_user_message(candidates[address], "C03.xlsx", tools, graph)
            assert "{" not in message


class TestTheLoop:
    def test_a_verdict_tool_call_ends_the_turn(self, setup, tmp_path):
        result = run_one(setup, tmp_path, verdict_response("INTENTIONAL"))
        assert result.verdict == "INTENTIONAL"
        assert result.turns == 1
        assert result.error is None

    def test_the_verdict_tool_is_offered_alongside_the_evidence_tools(self, setup, tmp_path):
        tools, graph, candidates = setup
        client = ScriptedClient(verdict_response("INCONCLUSIVE"))
        adjudicate_one(candidates["Revenue!H5"], client, tools, graph, "C03", tmp_path)
        offered = [tool.name for tool in client.requests[0][2]]
        assert offered == ["recompute_with_patch", "inspect_range", "submit_verdict"]

    def test_a_tool_call_is_run_and_its_result_fed_back(self, setup, tmp_path):
        tools, graph, candidates = setup
        client = ScriptedClient(
            tool_response("recompute_with_patch", {"cell": "Revenue!H5", "proposed_formula": "=G9"}),
            verdict_response("ERROR", proposed_formula="=G9"),
        )
        result = adjudicate_one(candidates["Revenue!H5"], client, tools, graph, "C03", tmp_path)

        assert result.tool_calls == 1
        assert result.turns == 2
        # the second request carried the tool result back
        _, messages, _ = client.requests[1]
        assert messages[-1].role == "tool"
        assert EBITDA in messages[-1].content

    def test_it_stops_after_the_turn_cap(self, setup, tmp_path):
        """A candidate that has not resolved is not going to, and every extra
        turn costs a call."""
        looping = [
            tool_response("inspect_range", {"sheet": "Revenue", "range": "C5:F5"})
            for _ in range(MAX_TURNS + 4)
        ]
        result = run_one(setup, tmp_path, *looping)
        assert result.turns == MAX_TURNS
        assert result.verdict == "INCONCLUSIVE"
        assert result.error

    def test_the_model_is_warned_before_the_cap_cuts_it_off(self, setup, tmp_path):
        """A model that runs out of turns mid gather records an INCONCLUSIVE
        that only means it ran out of room. Telling it the turn is the last one
        gets a verdict on the evidence it actually has."""
        tools, graph, candidates = setup
        looping = [
            tool_response("inspect_range", {"sheet": "Revenue", "range": "C5:F5"})
            for _ in range(MAX_TURNS - 1)
        ]
        client = ScriptedClient(*looping, verdict_response("INTENTIONAL"))
        result = adjudicate_one(
            candidates["Revenue!H5"], client, tools, graph, "C03", tmp_path
        )

        last_request = client.requests[-1][1]
        assert "last turn" in last_request[-1].content
        assert result.verdict == "INTENTIONAL"
        assert result.error is None

    def test_a_bad_verdict_gets_one_correction(self, setup, tmp_path):
        result = run_one(
            setup,
            tmp_path,
            verdict_response("IMMATERIAL"),
            verdict_response("ERROR", proposed_formula="=G9"),
        )
        assert result.verdict == "ERROR"
        assert result.turns == 2

    def test_a_model_that_never_produces_a_verdict_is_inconclusive(self, setup, tmp_path):
        """Not a crash, and not a finding. Nothing was established."""
        result = run_one(setup, tmp_path, *[verdict_response("NONSENSE") for _ in range(MAX_TURNS)])
        assert result.verdict == "INCONCLUSIVE"
        assert result.confidence == "low"
        assert result.error

    def test_a_verdict_written_as_prose_is_still_read(self, setup, tmp_path):
        """Some models answer in text whatever the tools say."""
        prose = AgentResponse(
            text='```json\n{"verdict": "INTENTIONAL", "confidence": "medium", '
            '"evidence": ["Costs!I12 carries a comment"], "reasoning": "Deliberate."}\n```',
            stop_reason="stop",
            usage=Usage(100, 20),
        )
        result = run_one(setup, tmp_path, prose)
        assert result.verdict == "INTENTIONAL"
        assert result.confidence == "medium"


class TestProseCorrection:
    """Some models answer in text. The loop corrects once and then stops."""

    @staticmethod
    def _prose(text) -> AgentResponse:
        return AgentResponse(text=text, stop_reason="stop", usage=Usage(100, 20))

    def test_an_unreadable_prose_reply_gets_one_correction(self, setup, tmp_path):
        result = run_one(
            setup,
            tmp_path,
            self._prose("I had a look and it seems fine to me."),
            self._prose(
                '{"verdict": "INTENTIONAL", "confidence": "high", '
                '"evidence": ["Revenue!C5 reads an assumption"], "reasoning": "First period."}'
            ),
        )
        assert result.verdict == "INTENTIONAL"
        assert result.turns == 2
        assert result.error is None

    def test_the_correction_tells_the_model_what_was_wrong(self, setup, tmp_path):
        tools, graph, candidates = setup
        client = ScriptedClient(
            self._prose("no idea"),
            verdict_response("INCONCLUSIVE"),
        )
        adjudicate_one(candidates["Revenue!H5"], client, tools, graph, "C03", tmp_path)
        _, messages, _ = client.requests[1]
        assert "not a valid verdict" in messages[-1].content

    def test_prose_that_never_becomes_a_verdict_gives_up(self, setup, tmp_path):
        """Every retry costs a call, so the loop does not keep asking."""
        result = run_one(
            setup, tmp_path, *[self._prose("still no idea") for _ in range(MAX_TURNS + 2)]
        )
        assert result.verdict == "INCONCLUSIVE"
        assert result.turns <= MAX_TURNS
        assert result.error


class TestTheVerdictRecord:
    def test_it_knows_whether_it_is_a_finding(self, setup, tmp_path):
        assert run_one(setup, tmp_path, verdict_response("ERROR", proposed_formula="=G9")).is_error
        assert not run_one(setup, tmp_path, verdict_response("INTENTIONAL")).is_error

    def test_it_serialises_for_the_results_file(self, setup, tmp_path):
        result = run_one(setup, tmp_path, verdict_response("ERROR", proposed_formula="=G9"))
        data = result.as_dict()
        json.dumps(data)
        assert data["verdict"] == "ERROR"
        assert data["trace_path"].endswith(".jsonl")
        assert set(data) >= {"address", "detector", "evidence", "measured_deltas", "tokens"}


class TestTheTrace:
    def test_every_step_is_recorded_in_order(self, setup, tmp_path):
        tools, graph, candidates = setup
        client = ScriptedClient(
            tool_response("recompute_with_patch", {"cell": "Revenue!H5", "proposed_formula": "=G9"}),
            verdict_response("ERROR", proposed_formula="=G9"),
        )
        result = adjudicate_one(candidates["Revenue!H5"], client, tools, graph, "C03", tmp_path)

        records = read(result.trace_path)
        assert [r.type for r in records] == [
            "run_start",
            "model_message",
            "tool_call",
            "tool_result",
            "model_message",
            "tool_call",
            "verdict",
            "run_end",
        ]

    def test_the_tool_result_holds_the_measured_deltas(self, setup, tmp_path):
        """The reporter checks a reported figure against this record."""
        tools, graph, candidates = setup
        client = ScriptedClient(
            tool_response("recompute_with_patch", {"cell": "Revenue!H5", "proposed_formula": "=G9"}),
            verdict_response("ERROR", proposed_formula="=G9"),
        )
        result = adjudicate_one(candidates["Revenue!H5"], client, tools, graph, "C03", tmp_path)
        [record] = [r for r in read(result.trace_path) if r.type == "tool_result"]
        assert EBITDA in record.content["result"]

    def test_the_run_start_names_the_candidate_and_the_model(self, setup, tmp_path):
        result = run_one(setup, tmp_path, verdict_response("INTENTIONAL"))
        start = read(result.trace_path)[0]
        assert start.content["cell"] == "Revenue!H5"
        assert start.content["detector"] == "D1"
        assert start.content["provider"] == "scripted"

    def test_tokens_are_accumulated_across_turns(self, setup, tmp_path):
        result = run_one(
            setup,
            tmp_path,
            tool_response("inspect_range", {"sheet": "Revenue", "range": "C5:F5"}),
            verdict_response("INTENTIONAL"),
        )
        assert result.tokens == {"in": 1700, "out": 100}


class TestVerdictValidation:
    def test_immaterial_is_refused_by_name(self):
        with pytest.raises(SchemaViolation, match="materiality gate"):
            normalise_verdict({"verdict": "IMMATERIAL"})

    def test_an_unknown_confidence_falls_back_to_low(self):
        result = normalise_verdict({"verdict": "ERROR", "confidence": "certain"})
        assert result["confidence"] == "low"

    def test_evidence_given_as_a_string_becomes_a_list(self):
        result = normalise_verdict({"verdict": "ERROR", "evidence": "one thing"})
        assert result["evidence"] == ("one thing",)

    def test_deltas_that_are_not_an_object_are_dropped(self):
        result = normalise_verdict({"verdict": "ERROR", "measured_deltas": "big"})
        assert result["measured_deltas"] == {}

    @pytest.mark.parametrize(
        "text,expected",
        [
            (None, "no text"),
            ("   ", "no text"),
            ("I think it is fine", "no JSON object"),
            ('{"verdict": ', "no JSON object"),
            ("[1, 2, 3]", "no JSON object"),
        ],
    )
    def test_unreadable_replies_say_why(self, text, expected):
        with pytest.raises(SchemaViolation, match=expected):
            parse_verdict(text)

    def test_broken_json_inside_braces_is_reported_as_such(self):
        with pytest.raises(SchemaViolation, match="not valid JSON"):
            parse_verdict('{"verdict": "ERROR",,}')


class TestAdjudicatingMany:
    def test_a_cell_flagged_twice_is_asked_about_once(self, setup, tmp_path):
        """Two detectors on one cell is one question for the model. Asking
        twice would double the cost to reach the same answer."""
        tools, graph, candidates = setup
        duplicated = [
            Candidate("D2", "Revenue!H5", "x" * 30),
            Candidate("D4", "Revenue!H5", "y" * 30),
            Candidate("D1", "Revenue!C3", "z" * 30),
        ]
        client = ScriptedClient(*[verdict_response("INCONCLUSIVE") for _ in range(5)])
        results = adjudicate(duplicated, client, tools, graph, "C03", tmp_path)
        assert len(results) == 2
        assert len(client.requests) == 2


class TestOneBadCandidateDoesNotEndTheRun:
    """Observed live: on the seventeenth candidate the model emitted corrupt
    JSON inside a tool call, the provider rejected the request, and the whole
    run died taking sixteen earned verdicts with it."""

    @staticmethod
    def _raising(error):
        class Failing:
            provider, model = "scripted", "scripted-1"

            def complete(self, *_, **__):
                raise error

        return Failing()

    def test_a_malformed_reply_becomes_an_inconclusive_verdict(self, setup, tmp_path):
        from materia.llm import ProviderError

        tools, graph, candidates = setup
        result = adjudicate_one(
            candidates["Revenue!H5"],
            self._raising(ProviderError("Failed to parse tool call arguments as JSON")),
            tools, graph, "C03", tmp_path,
        )
        assert result.verdict == "INCONCLUSIVE"
        assert "parse tool call" in result.error

    def test_the_run_carries_on_to_the_next_candidate(self, setup, tmp_path):
        tools, graph, candidates = setup
        chosen = list(candidates.values())[:3]

        class FailsOnTheSecond:
            provider, model = "scripted", "scripted-1"

            def __init__(self):
                self.seen = 0

            def complete(self, *_, **__):
                from materia.llm import ProviderError

                self.seen += 1
                if self.seen == 2:
                    raise ProviderError("Failed to parse tool call arguments as JSON")
                return verdict_response("INTENTIONAL")

        results = adjudicate(chosen, FailsOnTheSecond(), tools, graph, "C03", tmp_path)
        assert len(results) == 3
        assert [r.verdict for r in results] == ["INTENTIONAL", "INCONCLUSIVE", "INTENTIONAL"]

    def test_a_rate_limit_stops_the_run_instead(self, setup, tmp_path):
        """Carrying on would hammer a provider that has already said no.
        CLAUDE.md section 6 says back off."""
        from materia.llm import RateLimited

        tools, graph, candidates = setup
        with pytest.raises(RateLimited):
            adjudicate_one(
                candidates["Revenue!H5"],
                self._raising(RateLimited("rate limit reached")),
                tools, graph, "C03", tmp_path,
            )

    def test_an_unavailable_model_stops_the_run_too(self, setup, tmp_path):
        from materia.llm import ModelNotAvailable

        tools, graph, candidates = setup
        with pytest.raises(ModelNotAvailable):
            adjudicate_one(
                candidates["Revenue!H5"],
                self._raising(ModelNotAvailable("no such model")),
                tools, graph, "C03", tmp_path,
            )

    def test_the_failure_is_recorded_in_the_trajectory(self, setup, tmp_path):
        from materia.llm import ProviderError

        tools, graph, candidates = setup
        result = adjudicate_one(
            candidates["Revenue!H5"],
            self._raising(ProviderError("Failed to parse tool call arguments as JSON")),
            tools, graph, "C03", tmp_path,
        )
        records = read(result.trace_path)
        assert any("error" in r.content for r in records if r.type == "model_message")
        assert records[-1].content["status"] == "schema_violation"
