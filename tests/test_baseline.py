"""Baseline harness tests.

No API calls. The agent is driven by a scripted client so every branch is
reachable, including the ones a real run only hits occasionally.

What matters most here is that the comparison is fair. A baseline built to
lose proves nothing, so the prompt is checked against the doc and the caps are
checked against the solution's measured average rather than being picked.
"""

import json
import sys
from pathlib import Path

import pytest

from materia.baseline import (
    FINDINGS_NAME,
    MAX_TOOL_NAME_CORRECTIONS,
    TOOLS,
    WORKBOOK_NAME,
    Workspace,
    _is_unoffered_tool,
    run_baseline,
)
from materia.corpus.layout import DECLARED_OUTPUTS
from materia.llm import AgentResponse, ProviderError, RateLimited, ToolCall, Usage
from materia.prompts.baseline import SYSTEM_PROMPT
from materia.trace import read

WORKBOOK = "corpus/C03.xlsx"


class Scripted:
    """Replays responses and records what it was sent."""

    provider, model = "scripted", "scripted-1"

    def __init__(self, *responses):
        self._responses = list(responses)
        self.requests = []

    def complete(self, system, messages, tools=None):
        self.requests.append((system, list(messages), list(tools or [])))
        if not self._responses:
            return AgentResponse(text="done", stop_reason="stop", usage=Usage(10, 5))
        reply = self._responses.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def calls(*pairs) -> AgentResponse:
    return AgentResponse(
        text=None,
        tool_calls=tuple(ToolCall(f"c{i}", name, args) for i, (name, args) in enumerate(pairs)),
        stop_reason="tool_calls",
        usage=Usage(500, 60),
    )


def done(text="I have written findings.json.") -> AgentResponse:
    return AgentResponse(text=text, stop_reason="stop", usage=Usage(400, 40))


class TestThePromptComesFromTheDoc:
    def test_it_matches_the_document(self):
        doc = Path("docs/AGENT_INSTRUCTIONS.md").read_text()
        section = doc.split("## 3. Baseline agent")[1].split("\n### Why this baseline")[0]
        assert SYSTEM_PROMPT.strip() == section.split("```")[1].strip()

    def test_it_is_not_sandbagged(self):
        """It names all five error families, declares the outputs and says
        precision counts. A baseline built to lose proves nothing.

        Matched against the prompt with its line wrapping flattened, since the
        phrases straddle line breaks.
        """
        flat = " ".join(SYSTEM_PROMPT.split())
        for family in (
            "a formula replaced by a hardcoded value",
            "a copied formula referencing the wrong cell",
            "an aggregation range that misses rows",
            "an off-by-one period reference",
            "a flipped operator",
        ):
            assert family in flat, family
        assert "{declared_outputs}" in SYSTEM_PROMPT
        assert "Precision counts" in flat

    def test_it_allows_for_there_being_nothing_wrong(self):
        """Two of the twelve workbooks are clean. A prompt that assumed an
        error would push the agent to manufacture one."""
        assert "There may also be no errors at all." in " ".join(SYSTEM_PROMPT.split())

    def test_the_declared_outputs_are_filled_in(self, tmp_path):
        client = Scripted(done())
        run_baseline(WORKBOOK, DECLARED_OUTPUTS, client, tmp_path, workspace_directory=tmp_path / "ws")
        system = client.requests[0][0]
        assert "{declared_outputs}" not in system
        for output in DECLARED_OUTPUTS:
            assert output in system


class TestTheCapsAreDerivedNotChosen:
    def test_config_matches_the_measured_solution_average(self):
        """docs/EVALUATION.md section 4 requires the same run budget on both
        sides, so the number has to come from measurement."""
        import yaml

        config = yaml.safe_load(Path("config.yaml").read_text())
        per_candidate = config["solution"]["calls_per_candidate"]
        assert per_candidate == 3.0

        # 22.2 candidates per workbook on average across the corpus
        expected = round(22.2 * per_candidate)
        assert abs(config["baseline"]["max_turns"] - expected) <= 1

    def test_the_token_budget_matches_too(self):
        import yaml

        config = yaml.safe_load(Path("config.yaml").read_text())
        expected = 22.2 * config["solution"]["tokens_per_candidate"]
        assert abs(config["baseline"]["max_tokens"] - expected) < 5_000


class TestTheWorkspace:
    def test_it_holds_the_workbook_and_nothing_else(self, tmp_path):
        workspace = Workspace(WORKBOOK, tmp_path / "ws")
        assert [p.name for p in workspace.root.iterdir()] == [WORKBOOK_NAME]

    def test_the_source_workbook_is_never_exposed(self, tmp_path):
        """The agent gets a copy. There is no path from the workspace to ours."""
        workspace = Workspace(WORKBOOK, tmp_path / "ws")
        assert workspace.root != Path(WORKBOOK).parent

    def test_bash_runs_in_the_workspace(self, tmp_path):
        workspace = Workspace(WORKBOOK, tmp_path / "ws")
        result = workspace.bash("ls")
        assert result["exit_code"] == 0
        assert WORKBOOK_NAME in result["stdout"]

    def test_openpyxl_is_available_as_the_prompt_promises(self, tmp_path):
        workspace = Workspace(WORKBOOK, tmp_path / "ws")
        result = workspace.bash("python3 -c 'import openpyxl; print(openpyxl.__version__)'")
        assert result["exit_code"] == 0
        assert result["stdout"].strip()

    def test_a_path_outside_the_workspace_is_refused(self, tmp_path):
        workspace = Workspace(WORKBOOK, tmp_path / "ws")
        assert "outside the working directory" in workspace.read_file("../../../etc/passwd")["error"]
        assert "outside the working directory" in workspace.write_file("../escape.txt", "x")["error"]

    def test_a_command_that_loops_is_cut_off(self, tmp_path, monkeypatch):
        import materia.baseline as baseline

        monkeypatch.setattr(baseline, "COMMAND_TIMEOUT_SECONDS", 1)
        workspace = Workspace(WORKBOOK, tmp_path / "ws")
        assert "did not finish" in workspace.bash("sleep 5")["error"]

    def test_long_output_is_truncated_before_it_reaches_the_model(self, tmp_path):
        """One cat of a binary would otherwise spend the whole token budget."""
        workspace = Workspace(WORKBOOK, tmp_path / "ws")
        result = workspace.bash("python3 -c \"print('x' * 100000)\"")
        assert "truncated" in result["stdout"]
        assert len(result["stdout"]) < 10_000

    def test_reading_a_binary_says_so_rather_than_failing(self, tmp_path):
        workspace = Workspace(WORKBOOK, tmp_path / "ws")
        assert "not a text file" in workspace.read_file(WORKBOOK_NAME)["error"]

    def test_a_missing_file(self, tmp_path):
        workspace = Workspace(WORKBOOK, tmp_path / "ws")
        assert "does not exist" in workspace.read_file("nope.txt")["error"]

    def test_write_then_read(self, tmp_path):
        workspace = Workspace(WORKBOOK, tmp_path / "ws")
        workspace.write_file("notes/a.txt", "hello")
        assert workspace.read_file("notes/a.txt")["content"] == "hello"

    def test_an_unknown_tool_is_answered_not_raised(self, tmp_path):
        workspace = Workspace(WORKBOOK, tmp_path / "ws")
        result = workspace.run(ToolCall("1", "python", {"code": "x"}))
        assert "no tool named" in result["error"]
        assert result["tools"] == ["bash", "read_file", "write_file"]

    def test_wrong_arguments_are_answered_not_raised(self, tmp_path):
        workspace = Workspace(WORKBOOK, tmp_path / "ws")
        assert "wrong arguments" in workspace.run(ToolCall("1", "bash", {}))["error"]


class TestReadingWhatTheAgentWrote:
    def test_a_well_formed_findings_file(self, tmp_path):
        workspace = Workspace(WORKBOOK, tmp_path / "ws")
        workspace.write_file(FINDINGS_NAME, json.dumps(
            {"findings": [{"sheet": "Revenue", "cell": "H5", "confidence": "high"}]}))
        findings, raw = workspace.findings()
        assert len(findings) == 1
        assert raw is None

    def test_a_bare_list_is_accepted(self, tmp_path):
        workspace = Workspace(WORKBOOK, tmp_path / "ws")
        workspace.write_file(FINDINGS_NAME, json.dumps([{"cell": "H5"}]))
        assert len(workspace.findings()[0]) == 1

    def test_a_malformed_file_is_kept_rather_than_repaired(self, tmp_path):
        """A baseline that could not write valid JSON is a result about the
        baseline, not a problem to paper over."""
        workspace = Workspace(WORKBOOK, tmp_path / "ws")
        workspace.write_file(FINDINGS_NAME, "{not json")
        findings, raw = workspace.findings()
        assert findings == []
        assert raw == "{not json"

    def test_valid_json_that_is_not_a_findings_list(self, tmp_path):
        """The agent wrote something well formed and wrong. Kept raw, because
        what it wrote instead is a result about the baseline."""
        workspace = Workspace(WORKBOOK, tmp_path / "ws")
        workspace.write_file(FINDINGS_NAME, json.dumps({"findings": "none found"}))
        findings, raw = workspace.findings()
        assert findings == []
        assert raw is not None

    def test_no_file_at_all_is_no_findings(self, tmp_path):
        workspace = Workspace(WORKBOOK, tmp_path / "ws")
        assert workspace.findings() == ([], None)


class TestTheLoop:
    def test_it_runs_tools_and_feeds_results_back(self, tmp_path):
        client = Scripted(calls(("bash", {"command": "ls"})), done())
        result = run_baseline(WORKBOOK, DECLARED_OUTPUTS, client, tmp_path,
                              workspace_directory=tmp_path / "ws")
        assert result.tool_calls == 1
        assert result.turns == 2
        _, messages, _ = client.requests[1]
        assert messages[-1].role == "tool"
        assert WORKBOOK_NAME in messages[-1].content

    def test_it_stops_at_the_turn_cap(self, tmp_path):
        looping = [calls(("bash", {"command": "ls"})) for _ in range(10)]
        result = run_baseline(WORKBOOK, DECLARED_OUTPUTS, Scripted(*looping), tmp_path,
                              max_turns=4, workspace_directory=tmp_path / "ws")
        assert result.turns == 4
        assert "turn cap" in result.stopped

    def test_it_stops_at_the_token_budget(self, tmp_path):
        """Equal budgets are the whole basis of the comparison, so the cap has
        to actually bind."""
        looping = [calls(("bash", {"command": "ls"})) for _ in range(10)]
        result = run_baseline(WORKBOOK, DECLARED_OUTPUTS, Scripted(*looping), tmp_path,
                              max_turns=50, max_tokens=1200,
                              workspace_directory=tmp_path / "ws")
        assert "token budget" in result.stopped
        assert result.tokens["in"] + result.tokens["out"] >= 1200

    def test_a_reply_with_no_tool_call_ends_the_run(self, tmp_path):
        result = run_baseline(WORKBOOK, DECLARED_OUTPUTS, Scripted(done()), tmp_path,
                              workspace_directory=tmp_path / "ws")
        assert result.turns == 1
        assert result.stopped is None

    def test_findings_written_by_the_agent_are_collected(self, tmp_path):
        workspace_directory = tmp_path / "ws"
        payload = json.dumps({"findings": [{"sheet": "Revenue", "cell": "H5",
                                            "confidence": "high", "impact": {"P&L!AA15": 1.0}}]})
        client = Scripted(calls(("write_file", {"path": FINDINGS_NAME, "content": payload})), done())
        result = run_baseline(WORKBOOK, DECLARED_OUTPUTS, client, tmp_path,
                              workspace_directory=workspace_directory)
        assert len(result.findings) == 1
        assert result.findings[0]["cell"] == "H5"


class TestUnofferedToolNames:
    """openai/gpt-oss-120b reaches for a built in `python` tool that was never
    offered. The provider rejects the whole request, so the reply never
    arrives and there is nothing to answer."""

    UNOFFERED = ProviderError(
        "Groq request failed: 400 tool_use_failed Model called python tool "
        "which was not enabled for this request"
    )

    def test_it_is_recognised(self):
        assert _is_unoffered_tool(self.UNOFFERED)
        assert not _is_unoffered_tool(ProviderError("connection reset"))

    def test_the_run_continues_rather_than_ending(self, tmp_path):
        """The baseline gets one run per workbook, unlike the adjudicator which
        gets one per candidate. Losing the run loses the workbook."""
        client = Scripted(self.UNOFFERED, calls(("bash", {"command": "ls"})), done())
        result = run_baseline(WORKBOOK, DECLARED_OUTPUTS, client, tmp_path,
                              workspace_directory=tmp_path / "ws")
        assert result.stopped is None
        assert result.tool_calls == 1

    def test_the_agent_is_told_what_it_actually_has(self, tmp_path):
        client = Scripted(self.UNOFFERED, done())
        run_baseline(WORKBOOK, DECLARED_OUTPUTS, client, tmp_path,
                     workspace_directory=tmp_path / "ws")
        _, messages, _ = client.requests[1]
        assert "does not exist" in messages[-1].content
        assert "bash" in messages[-1].content

    def test_it_gives_up_after_a_few_attempts(self, tmp_path):
        """A model that cannot get this right twice will not on the fifth."""
        client = Scripted(*[self.UNOFFERED] * (MAX_TOOL_NAME_CORRECTIONS + 2))
        result = run_baseline(WORKBOOK, DECLARED_OUTPUTS, client, tmp_path,
                              workspace_directory=tmp_path / "ws")
        assert result.stopped is not None
        assert "tool_use_failed" in result.stopped

    def test_a_real_provider_failure_still_stops_the_run(self, tmp_path):
        client = Scripted(ProviderError("connection reset"))
        result = run_baseline(WORKBOOK, DECLARED_OUTPUTS, client, tmp_path,
                              workspace_directory=tmp_path / "ws")
        assert "connection reset" in result.stopped

    def test_a_rate_limit_ends_the_run_and_says_so(self, tmp_path):
        """It used to escape, which threw away the trace and any findings file
        the agent had already written. The baseline gets one run per workbook,
        so that is the whole workbook lost to somebody else's quota."""
        from materia.llm import RateLimited

        result = run_baseline(WORKBOOK, DECLARED_OUTPUTS, Scripted(RateLimited("quota")),
                              tmp_path, workspace_directory=tmp_path / "ws")
        assert result.failed is True
        assert "quota" in result.stopped
        assert Path(result.trace_path).exists()

    def test_a_run_that_used_its_budget_is_not_marked_failed(self, tmp_path):
        """Both stop early. Only one of them is our problem."""
        client = Scripted(*[calls(("bash", {"command": "ls"}))] * 3)
        result = run_baseline(WORKBOOK, DECLARED_OUTPUTS, client, tmp_path,
                              max_turns=2, workspace_directory=tmp_path / "ws")
        assert result.failed is False
        assert "turn cap" in result.stopped

    def test_what_the_agent_already_wrote_survives_a_rate_limit(self, tmp_path):
        client = Scripted(
            calls(("write_file", {"path": FINDINGS_NAME,
                                  "content": json.dumps({"findings": [{"cell": "H5"}]})})),
            RateLimited("quota"),
        )
        result = run_baseline(WORKBOOK, DECLARED_OUTPUTS, client, tmp_path,
                              workspace_directory=tmp_path / "ws")
        assert result.failed is True
        assert [f["cell"] for f in result.findings] == ["H5"]


class TestTheTrace:
    def test_it_uses_the_same_schema_as_the_solution(self, tmp_path):
        client = Scripted(calls(("bash", {"command": "ls"})), done())
        result = run_baseline(WORKBOOK, DECLARED_OUTPUTS, client, tmp_path,
                              workspace_directory=tmp_path / "ws")
        types = [r.type for r in read(result.trace_path)]
        assert types == ["run_start", "model_message", "tool_call", "tool_result",
                         "model_message", "verdict", "run_end"]

    def test_every_tool_result_joins_its_call(self, tmp_path):
        """The same call_id joining verified on the openai adapter."""
        client = Scripted(
            calls(("bash", {"command": "ls"}), ("read_file", {"path": "nope.txt"})), done()
        )
        result = run_baseline(WORKBOOK, DECLARED_OUTPUTS, client, tmp_path,
                              workspace_directory=tmp_path / "ws")
        records = read(result.trace_path)
        call_ids = {r.content["id"] for r in records if r.type == "tool_call"}
        result_ids = {r.content["id"] for r in records if r.type == "tool_result"}
        assert call_ids == result_ids
        assert len(call_ids) == 2

    def test_the_run_start_records_the_budget_it_was_given(self, tmp_path):
        """So a reader can confirm both sides got the same room."""
        result = run_baseline(WORKBOOK, DECLARED_OUTPUTS, Scripted(done()), tmp_path,
                              max_turns=67, max_tokens=211_000,
                              workspace_directory=tmp_path / "ws")
        start = read(result.trace_path)[0]
        assert start.content["max_turns"] == 67
        assert start.content["max_tokens"] == 211_000
        assert start.content["declared_outputs"] == DECLARED_OUTPUTS

    def test_the_findings_land_in_the_trace_too(self, tmp_path):
        payload = json.dumps({"findings": [{"cell": "H5"}]})
        client = Scripted(calls(("write_file", {"path": FINDINGS_NAME, "content": payload})), done())
        result = run_baseline(WORKBOOK, DECLARED_OUTPUTS, client, tmp_path,
                              workspace_directory=tmp_path / "ws")
        verdict = next(r for r in read(result.trace_path) if r.type == "verdict")
        assert verdict.content["count"] == 1
        assert verdict.content["malformed_findings_file"] is False

    def test_a_malformed_findings_file_is_flagged_in_the_trace(self, tmp_path):
        client = Scripted(calls(("write_file", {"path": FINDINGS_NAME, "content": "{oops"})), done())
        result = run_baseline(WORKBOOK, DECLARED_OUTPUTS, client, tmp_path,
                              workspace_directory=tmp_path / "ws")
        verdict = next(r for r in read(result.trace_path) if r.type == "verdict")
        assert verdict.content["malformed_findings_file"] is True


class TestTheResultSet:
    def test_it_serialises_for_the_evaluator(self, tmp_path):
        result = run_baseline(WORKBOOK, DECLARED_OUTPUTS, Scripted(done()), tmp_path,
                              workspace_directory=tmp_path / "ws")
        data = result.as_dict()
        json.dumps(data)
        assert data["workbook"] == "C03.xlsx"
        assert set(data) >= {"findings", "turns", "tokens", "trace_path", "stopped"}


class TestTheInterpreterThePromptPromises:
    """The prompt tells the agent Python is available with openpyxl installed.

    The first proof run showed that is not free: the shell had no `python` at
    all and the agent spent a turn discovering it. Turns are the baseline's
    whole budget, so a turn lost to our sandbox is a turn taken off the
    comparison. On a fresh clone the worse failure is available too, where an
    ambient `python3` exists but has no openpyxl in it.
    """

    def test_python_resolves_and_has_openpyxl(self, tmp_path):
        workspace = Workspace(WORKBOOK, tmp_path / "ws")
        result = workspace.bash('python -c "import openpyxl; print(openpyxl.__version__)"')
        assert result["exit_code"] == 0, result
        assert result["stdout"].strip()

    def test_python3_resolves_to_the_same_one(self, tmp_path):
        workspace = Workspace(WORKBOOK, tmp_path / "ws")
        result = workspace.bash('python3 -c "import sys; print(sys.executable)"')
        assert result["exit_code"] == 0
        # Same interpreter, reached by a different name in the same directory.
        assert Path(result["stdout"].strip()).parent == Path(sys.executable).parent

    def test_the_prompt_does_not_promise_a_version_we_do_not_pin(self, tmp_path):
        """It used to say 3.11. The virtualenv is whichever of 3.11 or 3.12 the
        reproducer built, so naming one was a claim the harness cannot keep."""
        assert "3.11" not in SYSTEM_PROMPT

    def test_the_workspace_still_shows_only_the_workbook(self, tmp_path):
        """PATH is an environment change, not a file. Putting the interpreter
        where the agent can find it must not put anything in its directory."""
        workspace = Workspace(WORKBOOK, tmp_path / "ws")
        assert [p.name for p in workspace.root.iterdir()] == [WORKBOOK_NAME]


class TestWhereTheTraceLands:
    def test_the_provider_is_in_the_file_name(self, tmp_path):
        """Traces are appended to. Two runs of one workbook on two providers
        used to land in one file, which reads as a single incoherent run."""
        result = run_baseline(WORKBOOK, DECLARED_OUTPUTS, Scripted(done()), tmp_path,
                              workspace_directory=tmp_path / "ws")
        assert Path(result.trace_path).name == "C03_baseline_scripted.jsonl"

    def test_two_providers_do_not_share_a_file(self, tmp_path):
        first = run_baseline(WORKBOOK, DECLARED_OUTPUTS, Scripted(done()), tmp_path,
                             workspace_directory=tmp_path / "a")
        other = Scripted(done())
        other.provider = "other"
        second = run_baseline(WORKBOOK, DECLARED_OUTPUTS, other, tmp_path,
                              workspace_directory=tmp_path / "b")
        assert first.trace_path != second.trace_path
        for path in (first.trace_path, second.trace_path):
            assert len([r for r in read(Path(path)) if r.type == "run_start"]) == 1
