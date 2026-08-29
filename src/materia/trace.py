"""Trajectory capture.

Every model message, tool call, tool result, verdict and human checkpoint is
written to JSONL as the run proceeds. Nothing is reconstructed afterwards:
a trace assembled from memory once the answer is known is a story about the
run, not a record of it.

The schema is in docs/TRAJECTORIES.md. The reason it matters is the invariant
in docs/ARCHITECTURE.md: every number in a report has to trace back to a
`tool_result` record here. That is what lets a reader confirm no impact figure
was invented, and the reporter enforces it in code.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

RECORD_TYPES = (
    "run_start",
    "model_message",
    "tool_call",
    "tool_result",
    "verdict",
    "human_checkpoint",
    "run_end",
)


def new_run_id(prefix: str, workbook: str) -> str:
    """A short, readable identifier, as in docs/TRAJECTORIES.md."""
    return f"{prefix}-{workbook}-{uuid.uuid4().hex[:4]}"


def _timestamp() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


@dataclass(frozen=True)
class Record:
    """One line of a trace."""

    ts: str
    run_id: str
    agent: str
    step: int
    type: str
    content: dict[str, Any] = field(default_factory=dict)
    tokens: dict[str, int] = field(default_factory=lambda: {"in": 0, "out": 0})
    latency_ms: int = 0

    def as_json(self) -> str:
        return json.dumps(
            {
                "ts": self.ts,
                "run_id": self.run_id,
                "agent": self.agent,
                "step": self.step,
                "type": self.type,
                "content": self.content,
                "tokens": self.tokens,
                "latency_ms": self.latency_ms,
            },
            sort_keys=True,
        )


class Trace:
    """A trace file, written a line at a time.

    Every write is flushed. A run that dies part way through still leaves a
    readable record of how far it got, which is the case where a trace is most
    useful.
    """

    def __init__(self, path: str | Path, run_id: str, agent: str) -> None:
        self.path = Path(path)
        self.run_id = run_id
        self.agent = agent
        self._step = 0
        self.path.parent.mkdir(parents=True, exist_ok=True)

        # One file, one run. Appending merged a rerun into the file already
        # there, and the result read as a single incoherent run: two
        # run_starts, two run_ends, step numbers restarting halfway down.
        # Nothing reads a trace expecting more than one run in it.
        self._handle = self.path.open("w", encoding="utf-8")

    # --- lifecycle ---

    def __enter__(self) -> "Trace":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc is not None:
            self.record("run_end", {"status": "failed", "error": str(exc)})
        self.close()

    def close(self) -> None:
        if not self._handle.closed:
            self._handle.close()

    @property
    def steps(self) -> int:
        return self._step

    # --- writing ---

    def record(
        self,
        type_: str,
        content: dict[str, Any],
        tokens: dict[str, int] | None = None,
        latency_ms: int = 0,
    ) -> Record:
        if type_ not in RECORD_TYPES:
            raise ValueError(f"unknown record type {type_!r}, expected one of {RECORD_TYPES}")

        self._step += 1
        entry = Record(
            ts=_timestamp(),
            run_id=self.run_id,
            agent=self.agent,
            step=self._step,
            type=type_,
            content=content,
            tokens=tokens or {"in": 0, "out": 0},
            latency_ms=latency_ms,
        )
        self._handle.write(entry.as_json() + "\n")
        self._handle.flush()
        os.fsync(self._handle.fileno())
        return entry

    def run_start(self, **content: Any) -> Record:
        return self.record("run_start", dict(content))

    def model_message(self, response, latency_ms: int = 0) -> Record:
        """A reply from the model, with what it cost."""
        return self.record(
            "model_message",
            {
                "text": response.text,
                "stop_reason": response.stop_reason,
                "model": response.model,
                "provider": response.provider,
                "tool_calls": [
                    {"id": call.id, "name": call.name, "arguments": call.arguments}
                    for call in response.tool_calls
                ],
            },
            tokens={
                "in": response.usage.input_tokens,
                "out": response.usage.output_tokens,
            },
            latency_ms=latency_ms,
        )

    def tool_call(self, call) -> Record:
        return self.record(
            "tool_call",
            {"id": call.id, "name": call.name, "arguments": call.arguments},
        )

    def tool_result(
        self, call_id: str, name: str, result: Any, latency_ms: int = 0, error: str | None = None
    ) -> Record:
        """What a tool returned.

        This is the record the reporter checks a reported impact figure
        against, so the result is stored as data rather than as prose.
        """
        content: dict[str, Any] = {"id": call_id, "name": name, "result": result}
        if error is not None:
            content["error"] = error
        return self.record("tool_result", content, latency_ms=latency_ms)

    def verdict(self, verdict: dict[str, Any]) -> Record:
        return self.record("verdict", dict(verdict))

    def human_checkpoint(self, kind: str, decision: str, **detail: Any) -> Record:
        """A point where a person was asked and what they said.

        The brief requires consequential actions to be gated behind human
        approval, and a decline is as much a part of the record as an approval.
        """
        return self.record(
            "human_checkpoint", {"kind": kind, "decision": decision, **detail}
        )

    def run_end(self, status: str = "ok", **content: Any) -> Record:
        return self.record("run_end", {"status": status, **content})


class Timer:
    """Wall clock around one call, in milliseconds."""

    def __enter__(self) -> "Timer":
        self._start = time.perf_counter()
        self.elapsed_ms = 0
        return self

    def __exit__(self, *_) -> None:
        self.elapsed_ms = int((time.perf_counter() - self._start) * 1000)


# --- reading ---------------------------------------------------------------


def read(path: str | Path) -> list[Record]:
    """Read a trace back. Used by the renderer and by the reporter's checks."""
    records = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        data = json.loads(line)
        records.append(
            Record(
                ts=data["ts"],
                run_id=data["run_id"],
                agent=data["agent"],
                step=data["step"],
                type=data["type"],
                content=data.get("content", {}),
                tokens=data.get("tokens", {"in": 0, "out": 0}),
                latency_ms=data.get("latency_ms", 0),
            )
        )
    return records


def tool_results(records: list[Record], name: str | None = None) -> Iterator[Record]:
    """Every tool result, optionally for one tool."""
    for entry in records:
        if entry.type == "tool_result" and (name is None or entry.content.get("name") == name):
            yield entry


def total_tokens(records: list[Record]) -> dict[str, int]:
    return {
        "in": sum(entry.tokens.get("in", 0) for entry in records),
        "out": sum(entry.tokens.get("out", 0) for entry in records),
    }
