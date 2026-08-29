"""The baseline agent harness.

A general purpose coding agent, given the workbook and a shell, free to write
its own analysis code. docs/EVALUATION.md section 4 explains why this and not
a weaker comparison: flattening to CSV destroys the formulas before the model
sees them, which guarantees a win and proves nothing.

It is not sandbagged. It gets the error families by name, the declared output
cells, the same output schema, and the same run budget as the solution, which
`config.yaml` derives from the solution's measured per workbook average rather
than picking. What it does not get is our tooling. It is free to build
equivalents, and with a shell and openpyxl it can.

Running model written shell commands
------------------------------------

That is the point of the baseline, so it cannot be avoided, but it is
contained:

- Every command runs with its working directory set to a throwaway temp
  directory holding a copy of one workbook. Nothing else of ours is in it.
- The source workbook is copied in. The original is never exposed.
- Each command has a wall clock timeout, so a loop cannot hang a run.
- Output is truncated before it goes back to the model, so one `cat` of a
  binary cannot blow the token budget.

This is a local evaluation harness pointed at a synthetic corpus we generated.
It is not a sandbox for untrusted code in any stronger sense, and it should not
be pointed at an untrusted workbook or given a key with side effects.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from materia.llm import (
    AgentResponse,
    LLMClient,
    Message,
    ModelNotAvailable,
    ProviderError,
    ToolCall,
    ToolDefinition,
)
from materia.llm.openai_compatible import RateLimited
from materia.prompts.baseline import SYSTEM_PROMPT
from materia.trace import Timer, Trace, new_run_id

WORKBOOK_NAME = "model.xlsx"
FINDINGS_NAME = "findings.json"

# A command that has not finished by now is looping, not working.
COMMAND_TIMEOUT_SECONDS = 60

# One `cat` of a binary would otherwise spend the whole token budget in a
# single tool result.
MAX_OUTPUT_CHARS = 4_000

# A provider rejects the whole request when a model asks for a tool that was
# never offered, so the reply never reaches us and there is nothing to answer.
# The agent is told what it actually has and the turn is retried. Capped,
# because a model that cannot get this right twice will not get it right on
# the fifth attempt.
MAX_TOOL_NAME_CORRECTIONS = 3

TOOLS = [
    ToolDefinition(
        name="bash",
        description=(
            "Run a shell command in the working directory. Python 3 is available "
            "with openpyxl installed. Output is truncated if it is very long."
        ),
        parameters={
            "type": "object",
            "properties": {"command": {"type": "string", "description": "The command to run."}},
            "required": ["command"],
        },
    ),
    ToolDefinition(
        name="read_file",
        description="Read a text file from the working directory.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Path relative to the working directory."}},
            "required": ["path"],
        },
    ),
    ToolDefinition(
        name="write_file",
        description="Write a text file in the working directory, replacing it if it exists.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to the working directory."},
                "content": {"type": "string", "description": "The whole file contents."},
            },
            "required": ["path", "content"],
        },
    ),
]


@dataclass(frozen=True)
class BaselineResult:
    """What the baseline agent produced for one workbook."""

    workbook: str
    findings: tuple[dict[str, Any], ...]
    turns: int
    tool_calls: int
    tokens: dict[str, int]
    trace_path: str
    stopped: str | None = None
    raw_findings: str | None = None

    # A run cut off by the provider and a run that used its whole budget both
    # stop early, and they mean opposite things. One is a result about the
    # baseline, the other is a result about our afternoon.
    failed: bool = False

    def as_dict(self) -> dict:
        return {
            "workbook": self.workbook,
            "findings": [dict(f) for f in self.findings],
            "turns": self.turns,
            "tool_calls": self.tool_calls,
            "tokens": self.tokens,
            "trace_path": self.trace_path,
            "stopped": self.stopped,
            "failed": self.failed,
        }


def _interpreter_on_path() -> dict[str, str]:
    """Make the prompt's promise true.

    The prompt tells the agent Python is available with openpyxl installed.
    Left to the ambient shell that is a guess: `python` is often not on PATH
    at all, and an ambient `python3` is whichever one the machine happens to
    have, which on a fresh clone is not the virtualenv holding openpyxl. The
    first proof run lost a turn to `python: command not found`, and turns are
    the baseline's budget.

    So the interpreter running Materia is put first on PATH. It is the one
    with openpyxl in it, by construction.
    """
    env = dict(os.environ)
    interpreter = Path(sys.executable).parent
    env["PATH"] = os.pathsep.join([str(interpreter), env.get("PATH", "")])
    return env


class Workspace:
    """A throwaway directory with one workbook in it and nothing else."""

    def __init__(self, workbook: Path, directory: Path | None = None) -> None:
        self.source = Path(workbook)
        self.root = Path(directory) if directory else Path(tempfile.mkdtemp(prefix="materia-baseline-"))
        self.root.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.source, self.root / WORKBOOK_NAME)
        self.env = _interpreter_on_path()

    def _resolve(self, path: str) -> Path:
        """Keep a path inside the workspace.

        A model that asks for ../../etc/passwd gets told no rather than served.
        """
        target = (self.root / path).resolve()
        if not str(target).startswith(str(self.root.resolve())):
            raise ValueError(f"{path} is outside the working directory")
        return target

    def bash(self, command: str) -> dict[str, Any]:
        try:
            finished = subprocess.run(
                command,
                shell=True,
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=COMMAND_TIMEOUT_SECONDS,
                env=self.env,
            )
        except subprocess.TimeoutExpired:
            return {"error": f"the command did not finish within {COMMAND_TIMEOUT_SECONDS} seconds"}

        return {
            "exit_code": finished.returncode,
            "stdout": _truncate(finished.stdout),
            "stderr": _truncate(finished.stderr),
        }

    def read_file(self, path: str) -> dict[str, Any]:
        try:
            target = self._resolve(path)
        except ValueError as error:
            return {"error": str(error)}
        if not target.exists():
            return {"error": f"{path} does not exist"}
        try:
            return {"content": _truncate(target.read_text())}
        except UnicodeDecodeError:
            return {"error": f"{path} is not a text file"}

    def write_file(self, path: str, content: str) -> dict[str, Any]:
        try:
            target = self._resolve(path)
        except ValueError as error:
            return {"error": str(error)}
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return {"written": path, "bytes": len(content)}

    def run(self, call: ToolCall) -> dict[str, Any]:
        handlers = {"bash": self.bash, "read_file": self.read_file, "write_file": self.write_file}
        handler = handlers.get(call.name)
        if handler is None:
            return {"error": f"no tool named {call.name!r}", "tools": sorted(handlers)}
        try:
            return handler(**call.arguments)
        except TypeError as error:
            return {"error": f"wrong arguments for {call.name}: {error}"}

    def findings(self) -> tuple[list[dict], str | None]:
        """Read what the agent wrote, without repairing it.

        A malformed findings file is a result about the baseline, not a
        problem to paper over, so the raw text is kept alongside.
        """
        path = self.root / FINDINGS_NAME
        if not path.exists():
            return [], None
        raw = path.read_text()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return [], raw
        found = data.get("findings") if isinstance(data, dict) else data
        if not isinstance(found, list):
            return [], raw
        return [item for item in found if isinstance(item, dict)], None


def _is_unoffered_tool(error: Exception) -> bool:
    """Did the provider refuse because the model named a tool we never gave it?"""
    text = str(error).lower()
    return "tool_use_failed" in text and (
        "not enabled for this request" in text or "was not in request.tools" in text
    )


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + f"\n... truncated, {len(text) - MAX_OUTPUT_CHARS} more characters"


def run_baseline(
    workbook: str | Path,
    outputs: list[str],
    client: LLMClient,
    trace_directory: str | Path = "trajectories/baseline",
    max_turns: int = 67,
    max_tokens: int = 211_000,
    workspace_directory: str | Path | None = None,
) -> BaselineResult:
    """Give the agent the workbook and a shell, and see what it reports.

    Caps come from `config.yaml` and are the solution's measured per workbook
    average, so neither side gets more room than the other.
    """
    workbook = Path(workbook)
    workspace = Workspace(workbook, workspace_directory)
    # The provider is in the name because traces are appended to, and two runs
    # of the same workbook under different providers landing in one file would
    # read as a single incoherent run. It also keeps the rule from
    # docs/ARCHITECTURE.md section 9 visible on disk: a dev loop trace and a
    # scored one must never be mistakable for each other.
    trace_path = Path(trace_directory) / f"{workbook.stem}_baseline_{client.provider}.jsonl"

    system = SYSTEM_PROMPT.replace(
        "{declared_outputs}", "\n".join(f"  {output}" for output in outputs)
    )
    messages = [
        Message(
            role="user",
            content=(
                f"Audit ./{WORKBOOK_NAME} and write your findings to "
                f"./{FINDINGS_NAME}. Tell me when you are done."
            ),
        )
    ]

    tokens = {"in": 0, "out": 0}
    tool_calls = turns = corrections = 0
    stopped: str | None = None
    failed = False

    with Trace(trace_path, new_run_id("base", workbook.stem), "baseline") as trace:
        trace.run_start(
            workbook=workbook.name,
            declared_outputs=outputs,
            provider=client.provider,
            model=client.model,
            max_turns=max_turns,
            max_tokens=max_tokens,
            workspace=str(workspace.root),
        )

        while turns < max_turns:
            if tokens["in"] + tokens["out"] >= max_tokens:
                stopped = f"token budget of {max_tokens:,} reached"
                break

            turns += 1
            try:
                with Timer() as timer:
                    response: AgentResponse = client.complete(system, messages, TOOLS)
            except (RateLimited, ModelNotAvailable) as error:
                # The baseline gets one run per workbook. Letting this escape
                # threw away the trace and the findings file the agent may
                # already have written, which is the same loss the adjudicator
                # was fixed for. The run ends, says why, and keeps what exists.
                turns -= 1
                stopped, failed = str(error), True
                trace.record("model_message", {"error": str(error)})
                break
            except ProviderError as error:
                trace.record("model_message", {"error": str(error)})

                # openai/gpt-oss-120b reaches for a built in `python` tool that
                # was never offered. One malformed call must not end a whole
                # workbook: the baseline gets one run per workbook, unlike the
                # adjudicator which gets one per candidate.
                if _is_unoffered_tool(error) and corrections < MAX_TOOL_NAME_CORRECTIONS:
                    corrections += 1
                    messages.append(
                        Message(
                            role="user",
                            content=(
                                "That tool does not exist. The only tools you have "
                                f"are {', '.join(tool.name for tool in TOOLS)}. "
                                "Run Python with the bash tool, for example "
                                "bash({\"command\": \"python3 -c '...'\"}), or write "
                                "a script with write_file and run it with bash."
                            ),
                        )
                    )
                    continue

                stopped, failed = str(error), True
                break

            trace.model_message(response, latency_ms=timer.elapsed_ms)
            tokens["in"] += response.usage.input_tokens
            tokens["out"] += response.usage.output_tokens

            if not response.wants_tools:
                break

            messages.append(
                Message(role="assistant", content=response.text, tool_calls=response.tool_calls)
            )
            for call in response.tool_calls:
                tool_calls += 1
                trace.tool_call(call)
                with Timer() as timer:
                    result = workspace.run(call)
                trace.tool_result(
                    call.id, call.name, result, latency_ms=timer.elapsed_ms,
                    error=result.get("error"),
                )
                messages.append(
                    Message(role="tool", tool_call_id=call.id, content=json.dumps(result))
                )
        else:
            stopped = f"turn cap of {max_turns} reached"

        findings, raw = workspace.findings()
        trace.record(
            "verdict",
            {
                "findings": findings,
                "count": len(findings),
                "malformed_findings_file": raw is not None,
            },
        )
        trace.run_end(
            "ok" if stopped is None else "stopped",
            turns=turns,
            reason=stopped,
            failed=failed,
            tool_name_corrections=corrections,
        )

    return BaselineResult(
        workbook=workbook.name,
        findings=tuple(findings),
        turns=turns,
        tool_calls=tool_calls,
        tokens=tokens,
        trace_path=str(trace_path),
        stopped=stopped,
        raw_findings=raw,
        failed=failed,
    )
