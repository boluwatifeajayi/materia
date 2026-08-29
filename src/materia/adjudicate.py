"""The adjudicator loop.

One model call per candidate, two tools, three verdicts. The model decides
whether a cell is wrong. It does not decide whether being wrong matters: that
is the materiality gate's job, and keeping them apart is what lets the four
report buckets stay mutually exclusive. See docs/ARCHITECTURE.md sections 5
and 7.

The loop's shape is the design claim. The model states a hypothesis,
deterministic code tests it, and the tool result decides whether the finding
survives. Every number the model produces is checked against a tool result in
the trace before it can reach a report.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from materia.detect import Candidate
from materia.graph import DependencyGraph
from materia.llm import (
    AgentResponse,
    LLMClient,
    Message,
    ModelNotAvailable,
    ProviderError,
    ToolCall,
    ToolDefinition,
)
from materia.llm.groq import RateLimited
from materia.prompts.adjudicator import (
    CONFIDENCES,
    SYSTEM_PROMPT,
    USER_TEMPLATE,
    VERDICTS,
)
from materia.tools import Toolbox
from materia.trace import Timer, Trace, new_run_id

# A candidate that has not resolved in this many model calls is not going to.
MAX_TURNS = 6

VERDICT_TOOL = ToolDefinition(
    name="submit_verdict",
    description=(
        "Return your verdict. This is how you answer: do not write the answer "
        "as prose. Call this once, when you have finished gathering evidence."
    ),
    parameters={
        "type": "object",
        "properties": {
            "verdict": {
                "type": "string",
                "enum": list(VERDICTS),
                "description": (
                    "ERROR if the cell is wrong, INTENTIONAL if it differs from "
                    "its peers on purpose, INCONCLUSIVE if the evidence does not "
                    "support a confident answer."
                ),
            },
            "confidence": {"type": "string", "enum": list(CONFIDENCES)},
            "proposed_formula": {
                "type": "string",
                "description": "The formula that should be there. Omit unless the verdict is ERROR.",
            },
            "evidence": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Specific observations, each naming a cell reference.",
            },
            "reasoning": {"type": "string", "description": "Two sentences maximum."},
            "measured_deltas": {
                "type": "object",
                "description": (
                    "The figures recompute_with_patch returned, exactly as they "
                    "came back. Leave empty if you did not call it."
                ),
                "additionalProperties": {"type": "number"},
            },
        },
        "required": ["verdict", "confidence", "evidence", "reasoning"],
    },
)


class SchemaViolation(ValueError):
    """The model returned something that is not a verdict."""


@dataclass(frozen=True)
class Verdict:
    """One adjudicated candidate."""

    address: str
    detector: str
    verdict: str
    confidence: str
    proposed_formula: str | None
    evidence: tuple[str, ...]
    reasoning: str
    measured_deltas: dict[str, Any] = field(default_factory=dict)
    turns: int = 0
    tool_calls: int = 0
    tokens: dict[str, int] = field(default_factory=lambda: {"in": 0, "out": 0})
    trace_path: str | None = None
    error: str | None = None

    @property
    def is_error(self) -> bool:
        return self.verdict == "ERROR"

    def as_dict(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "detector": self.detector,
            "verdict": self.verdict,
            "confidence": self.confidence,
            "proposed_formula": self.proposed_formula,
            "evidence": list(self.evidence),
            "reasoning": self.reasoning,
            "measured_deltas": self.measured_deltas,
            "turns": self.turns,
            "tool_calls": self.tool_calls,
            "tokens": self.tokens,
            "trace_path": self.trace_path,
            "error": self.error,
        }


def normalise_verdict(data: dict[str, Any]) -> dict[str, Any]:
    """Check and tidy a verdict, wherever it came from.

    Validated here rather than trusted. The provider enforces the argument
    schema of `submit_verdict`, but nothing stops a model putting IMMATERIAL in
    a string field, and that verdict belongs to the gate.
    """
    verdict = str(data.get("verdict", "")).strip().upper()
    if verdict not in VERDICTS:
        raise SchemaViolation(
            f"{verdict!r} is not one of {VERDICTS}. IMMATERIAL is assigned by "
            "the materiality gate, not by the adjudicator."
        )

    confidence = str(data.get("confidence", "")).strip().lower()
    if confidence not in CONFIDENCES:
        confidence = "low"

    evidence = data.get("evidence") or []
    if isinstance(evidence, str):
        evidence = [evidence]

    deltas = data.get("measured_deltas") or {}
    if not isinstance(deltas, dict):
        deltas = {}

    return {
        "verdict": verdict,
        "confidence": confidence,
        "proposed_formula": data.get("proposed_formula") or None,
        "evidence": tuple(str(item) for item in evidence),
        "reasoning": str(data.get("reasoning") or "").strip(),
        "measured_deltas": deltas,
    }


def parse_verdict(text: str | None) -> dict[str, Any]:
    """Read a verdict out of the model's reply, or say why it is not one.

    Validated here rather than trusted. A schema the model is asked to follow
    is a request; this is the constraint.
    """
    if not text or not text.strip():
        raise SchemaViolation("the model returned no text")

    body = text.strip()
    if body.startswith("```"):
        body = body.split("\n", 1)[-1].rsplit("```", 1)[0]

    start, end = body.find("{"), body.rfind("}")
    if start == -1 or end == -1:
        raise SchemaViolation(f"no JSON object in the reply: {text[:120]!r}")

    try:
        data = json.loads(body[start : end + 1])
    except json.JSONDecodeError as error:
        raise SchemaViolation(f"the reply is not valid JSON: {error}") from error

    # No isinstance check: the slice runs from the first "{" to the last "}",
    # so json either reads it as an object or refuses it above.
    return normalise_verdict(data)


def build_user_message(
    candidate: Candidate,
    workbook_name: str,
    tools: Toolbox,
    graph: DependencyGraph,
) -> str:
    """Fill the template from docs/AGENT_INSTRUCTIONS.md section 1."""
    sheet, coordinate = candidate.address.split("!", 1)
    facts = tools.cells.get(sheet, {}).get(candidate.address)

    peers = candidate.peers or ()
    peer_table = (
        "\n".join(f"  {peer.address:16} {peer.formula:44} {peer.r1c1}" for peer in peers)
        or "  none supplied by the detector"
    )

    paths = graph.paths_to_outputs(candidate.address, tools.outputs)
    path_text = (
        "\n".join(
            f"  {output}: {_render_path(steps)}" for output, steps in sorted(paths.items())
        )
        or "  this cell does not reach any declared output"
    )

    outputs = "\n".join(
        f"  {output}: {tools.model.value(output)}" for output in tools.outputs
    )

    return USER_TEMPLATE.format(
        workbook_name=workbook_name,
        sheet=sheet,
        cell=coordinate,
        formula=(candidate.formula or (facts.formula if facts else None) or "(a value, not a formula)"),
        r1c1=candidate.r1c1 or "(not a formula)",
        comment_or_none=(facts.comment if facts and facts.comment else "none"),
        detector_id=candidate.detector,
        detector_reason=candidate.reason,
        peer_axis=candidate.peer_axis or "none",
        n_peers=len(peers),
        peer_table=peer_table,
        paths=path_text,
        outputs=outputs,
    )


def _render_path(steps: list[str], keep: int = 3) -> str:
    """A long path, shortened in the middle.

    The point of showing the path is that the cell reaches an output and how
    far away it is. Twenty two hops spelled out costs tokens without telling
    the reader anything the hop count does not.
    """
    if len(steps) <= keep * 2 + 1:
        return " -> ".join(steps)
    head = " -> ".join(steps[:keep])
    tail = " -> ".join(steps[-keep:])
    return f"{head} -> ... {len(steps) - keep * 2} more hops ... -> {tail}"


def adjudicate_one(
    candidate: Candidate,
    client: LLMClient,
    tools: Toolbox,
    graph: DependencyGraph,
    workbook_name: str,
    trace_directory: str | Path,
    run_prefix: str = "sol",
    max_turns: int = MAX_TURNS,
) -> Verdict:
    """Adjudicate one candidate, tracing every step as it happens."""
    trace_directory = Path(trace_directory)
    safe_cell = candidate.address.replace("!", "_").replace("$", "")
    path = trace_directory / f"{workbook_name}_adjudicator_{safe_cell}_{candidate.detector}.jsonl"
    run_id = new_run_id(run_prefix, workbook_name)

    messages = [
        Message(
            role="user",
            content=build_user_message(candidate, workbook_name, tools, graph),
        )
    ]

    tokens = {"in": 0, "out": 0}
    tool_call_count = 0
    turns = 0
    failure: str | None = None
    parsed: dict[str, Any] | None = None

    with Trace(path, run_id, "adjudicator") as trace:
        trace.run_start(
            workbook=workbook_name,
            cell=candidate.address,
            detector=candidate.detector,
            detector_reason=candidate.reason,
            provider=client.provider,
            model=client.model,
        )

        while turns < max_turns:
            turns += 1
            try:
                with Timer() as timer:
                    response: AgentResponse = client.complete(
                        SYSTEM_PROMPT, messages, tools.definitions + [VERDICT_TOOL]
                    )
            except (RateLimited, ModelNotAvailable):
                # Not this candidate's fault and not survivable by retrying.
                # CLAUDE.md section 6 says back off rather than hammer, so the
                # run stops here and says why.
                raise
            except ProviderError as error:
                # One malformed reply must not cost every verdict already
                # earned. Observed: the model emitted corrupt JSON in a tool
                # call, the provider rejected the request, and a run of
                # seventeen candidates died on the last one.
                failure = str(error)
                trace.record("model_message", {"error": failure})
                break
            trace.model_message(response, latency_ms=timer.elapsed_ms)
            tokens["in"] += response.usage.input_tokens
            tokens["out"] += response.usage.output_tokens

            submitted = next(
                (call for call in response.tool_calls if call.name == VERDICT_TOOL.name),
                None,
            )
            if submitted is not None:
                trace.tool_call(submitted)
                try:
                    parsed = normalise_verdict(submitted.arguments)
                    failure = None
                    break
                except SchemaViolation as violation:
                    failure = str(violation)
                    messages.append(
                        Message(
                            role="assistant",
                            content=response.text,
                            tool_calls=response.tool_calls,
                        )
                    )
                    messages.append(
                        Message(
                            role="tool",
                            tool_call_id=submitted.id,
                            content=json.dumps({"error": failure}),
                        )
                    )
                    continue

            if response.wants_tools:
                messages.append(
                    Message(
                        role="assistant",
                        content=response.text,
                        tool_calls=response.tool_calls,
                    )
                )
                for call in response.tool_calls:
                    tool_call_count += 1
                    messages.append(_run_tool(call, tools, trace))

                # One turn left. Say so, rather than letting the cap cut the
                # model off mid gather and record an INCONCLUSIVE that only
                # means it ran out of room. A verdict on the evidence it has
                # is a better answer than no verdict at all.
                if turns == max_turns - 1:
                    messages.append(
                        Message(
                            role="user",
                            content=(
                                "This is your last turn. Call submit_verdict now "
                                "with the evidence you have. If it is not enough "
                                "to be confident, return INCONCLUSIVE and say what "
                                "would settle it."
                            ),
                        )
                    )
                continue

            try:
                parsed = parse_verdict(response.text)
            except SchemaViolation as violation:
                failure = str(violation)
                # One correction, then give up. A model that cannot produce the
                # schema twice is not going to on the third attempt, and every
                # retry costs a call.
                if turns >= max_turns - 1:
                    break
                messages.append(Message(role="assistant", content=response.text or ""))
                messages.append(
                    Message(
                        role="user",
                        content=(
                            f"That was not a valid verdict: {violation}. Reply with "
                            "a single JSON object matching the output schema."
                        ),
                    )
                )
                continue

            failure = None
            break

        if parsed is None:
            parsed = {
                "verdict": "INCONCLUSIVE",
                "confidence": "low",
                "proposed_formula": None,
                "evidence": (),
                "reasoning": failure or "the model did not return a verdict",
                "measured_deltas": {},
            }
            failure = failure or "no verdict returned"

        trace.verdict({**parsed, "evidence": list(parsed["evidence"])})
        trace.run_end("ok" if failure is None else "schema_violation", turns=turns)

    return Verdict(
        address=candidate.address,
        detector=candidate.detector,
        turns=turns,
        tool_calls=tool_call_count,
        tokens=tokens,
        trace_path=str(path),
        error=failure,
        **parsed,
    )


def _run_tool(call: ToolCall, tools: Toolbox, trace: Trace) -> Message:
    trace.tool_call(call)
    with Timer() as timer:
        result = tools.run(call)
    trace.tool_result(
        call.id,
        call.name,
        result,
        latency_ms=timer.elapsed_ms,
        error=result.get("error") if isinstance(result, dict) else None,
    )
    return Message(role="tool", tool_call_id=call.id, content=json.dumps(result))


def adjudicate(
    candidates: list[Candidate],
    client: LLMClient,
    tools: Toolbox,
    graph: DependencyGraph,
    workbook_name: str,
    trace_directory: str | Path,
    run_prefix: str = "sol",
) -> list[Verdict]:
    """Adjudicate every candidate, one call per candidate.

    Candidates are deduplicated by cell first. A cell flagged by two detectors
    is one question for the model, and asking twice would double the cost to
    reach the same answer.
    """
    seen: dict[str, Candidate] = {}
    for candidate in candidates:
        seen.setdefault(candidate.address, candidate)

    return [
        adjudicate_one(
            candidate, client, tools, graph, workbook_name, trace_directory, run_prefix
        )
        for candidate in seen.values()
    ]
