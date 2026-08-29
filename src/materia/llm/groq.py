"""Groq adapter, via the OpenAI compatible API.

Dev loop only. Groq's free tier serves open models, which is fine for
exercising the tool call loop and catching bugs in the adjudication logic, and
is not a substitute data point for the headline comparison. Numbers from a
Groq run are never reported. See docs/ARCHITECTURE.md section 9.
"""

from __future__ import annotations

import json
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

BASE_URL = "https://api.groq.com/openai/v1"
# Groq decommissioned the Llama chat models this project was specified
# against. This is what the account is actually served, confirmed by querying
# the provider rather than guessed. Still an open weights model, so the
# reasoning in docs/ARCHITECTURE.md section 9 is unchanged.
DEFAULT_MODEL = "openai/gpt-oss-120b"


class GroqClient:
    provider = "groq"

    def __init__(self, model: str = DEFAULT_MODEL, api_key: str | None = None) -> None:
        from openai import OpenAI

        key = api_key or os.environ.get("GROQ_API_KEY")
        if not key:
            raise ProviderError("GROQ_API_KEY is not set")
        self.model = model
        self._client = OpenAI(api_key=key, base_url=BASE_URL)

    # --- translation ---

    @staticmethod
    def _tools(tools: list[ToolDefinition] | None) -> list[dict] | None:
        if not tools:
            return None
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in tools
        ]

    @staticmethod
    def _messages(system: str, messages: list[Message]) -> list[dict]:
        payload: list[dict] = [{"role": "system", "content": system}]
        for message in messages:
            if message.role == "tool":
                payload.append(
                    {
                        "role": "tool",
                        "tool_call_id": message.tool_call_id,
                        "content": message.content or "",
                    }
                )
            elif message.tool_calls:
                payload.append(
                    {
                        "role": "assistant",
                        "content": message.content or None,
                        "tool_calls": [
                            {
                                "id": call.id,
                                "type": "function",
                                "function": {
                                    "name": call.name,
                                    "arguments": json.dumps(call.arguments),
                                },
                            }
                            for call in message.tool_calls
                        ],
                    }
                )
            else:
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
            "messages": self._messages(system, messages),
            "temperature": 0,
        }
        translated = self._tools(tools)
        if translated:
            request["tools"] = translated

        try:
            completion = self._client.chat.completions.create(**request)
        except Exception as error:  # noqa: BLE001 - normalised below
            raise _translate_error(error, self.model) from error

        choice = completion.choices[0]
        calls = tuple(
            ToolCall(
                id=call.id,
                name=call.function.name,
                arguments=_parse_arguments(call.function.arguments),
            )
            for call in (choice.message.tool_calls or [])
        )
        usage = completion.usage
        return AgentResponse(
            text=choice.message.content,
            tool_calls=calls,
            stop_reason=choice.finish_reason or "end_turn",
            usage=Usage(
                input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                output_tokens=getattr(usage, "completion_tokens", 0) or 0,
            ),
            model=completion.model or self.model,
            provider=self.provider,
        )


def _parse_arguments(raw: str | None) -> dict:
    """Tool arguments arrive as a JSON string and are not always valid."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"__unparsed__": raw}
    return parsed if isinstance(parsed, dict) else {"__value__": parsed}


def _translate_error(error: Exception, model: str) -> ProviderError:
    text = str(error).lower()
    if "does not exist" in text or "model_not_found" in text or "decommissioned" in text:
        return ModelNotAvailable(f"Groq will not serve {model!r}: {error}")
    return ProviderError(f"Groq request failed: {error}")
