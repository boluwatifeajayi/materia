"""Anthropic adapter.

The scored runs use this one. docs/EVALUATION.md requires the solution and the
baseline to run on the same model, so the headline table isolates the
workflow's contribution rather than a difference in raw model capability.
"""

from __future__ import annotations

import os

from materia.llm.types import (
    AgentResponse,
    Message,
    ModelNotAvailable,
    ProviderError,
    ToolCall,
    ToolDefinition,
    Usage,
)

DEFAULT_MODEL = "claude-sonnet-5"
MAX_TOKENS = 4096


class AnthropicClient:
    provider = "anthropic"

    def __init__(self, model: str = DEFAULT_MODEL, api_key: str | None = None) -> None:
        import anthropic

        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise ProviderError("ANTHROPIC_API_KEY is not set")
        self.model = model
        self._client = anthropic.Anthropic(api_key=key)

    # --- translation ---

    @staticmethod
    def _tools(tools: list[ToolDefinition] | None) -> list[dict] | None:
        if not tools:
            return None
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.parameters,
            }
            for tool in tools
        ]

    @staticmethod
    def _messages(messages: list[Message]) -> list[dict]:
        """Anthropic takes content blocks, and consecutive tool results have to
        share one user turn."""
        payload: list[dict] = []
        for message in messages:
            if message.role == "tool":
                block = {
                    "type": "tool_result",
                    "tool_use_id": message.tool_call_id,
                    "content": message.content or "",
                }
                if payload and payload[-1]["role"] == "user" and isinstance(
                    payload[-1]["content"], list
                ):
                    payload[-1]["content"].append(block)
                else:
                    payload.append({"role": "user", "content": [block]})
                continue

            if message.tool_calls:
                blocks: list[dict] = []
                if message.content:
                    blocks.append({"type": "text", "text": message.content})
                blocks.extend(
                    {
                        "type": "tool_use",
                        "id": call.id,
                        "name": call.name,
                        "input": call.arguments,
                    }
                    for call in message.tool_calls
                )
                payload.append({"role": "assistant", "content": blocks})
                continue

            payload.append({"role": message.role, "content": message.content or ""})
        return payload

    # --- the interface ---

    def complete(
        self,
        system: str,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
    ) -> AgentResponse:
        request = {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            "system": system,
            "messages": self._messages(messages),
            "temperature": 0,
        }
        translated = self._tools(tools)
        if translated:
            request["tools"] = translated

        try:
            response = self._client.messages.create(**request)
        except Exception as error:  # noqa: BLE001 - normalised below
            raise _translate_error(error, self.model) from error

        text_parts = [
            block.text for block in response.content if getattr(block, "type", "") == "text"
        ]
        calls = tuple(
            ToolCall(id=block.id, name=block.name, arguments=dict(block.input or {}))
            for block in response.content
            if getattr(block, "type", "") == "tool_use"
        )
        return AgentResponse(
            text="\n".join(text_parts) if text_parts else None,
            tool_calls=calls,
            stop_reason=response.stop_reason or "end_turn",
            usage=Usage(
                input_tokens=getattr(response.usage, "input_tokens", 0) or 0,
                output_tokens=getattr(response.usage, "output_tokens", 0) or 0,
            ),
            model=response.model or self.model,
            provider=self.provider,
        )


def _translate_error(error: Exception, model: str) -> ProviderError:
    text = str(error).lower()
    if "not_found" in text or "model" in text and "does not exist" in text:
        return ModelNotAvailable(
            f"Anthropic will not serve {model!r}: {error}. "
            "Do not guess another model id: a run scored against a model "
            "nobody chose is not a result."
        )
    return ProviderError(f"Anthropic request failed: {error}")
