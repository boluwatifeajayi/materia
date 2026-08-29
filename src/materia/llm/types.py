"""The one shape every provider is normalised into.

The adjudicator and the report writer talk to this and never to a provider
SDK. See docs/ARCHITECTURE.md section 9. The point is not multi provider
robustness as a feature: it is that development iteration can be fast and free
while the number that ships comes from one accountable model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ToolDefinition:
    """A tool, described once, translated per provider."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    """A model's request to run a tool."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Message:
    """One turn. `tool` messages carry a result back to the model."""

    role: str  # "user", "assistant" or "tool"
    content: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class AgentResponse:
    """What came back, in one shape regardless of who produced it."""

    text: str | None
    tool_calls: tuple[ToolCall, ...] = ()
    stop_reason: str = "end_turn"
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    provider: str = ""

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


class LLMClient(Protocol):
    """Every adapter presents this and nothing else."""

    provider: str
    model: str

    def complete(
        self,
        system: str,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
    ) -> AgentResponse: ...


class ProviderError(RuntimeError):
    """A provider refused a request in a way worth stopping for."""


class ModelNotAvailable(ProviderError):
    """The configured model id is not one the provider will serve.

    Its own class because guessing another model string is the wrong response.
    A run scored against a model nobody chose is not a result.
    """
