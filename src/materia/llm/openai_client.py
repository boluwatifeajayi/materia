"""OpenAI adapter, on the Responses API.

The scored runs use this one. docs/EVALUATION.md requires the solution and the
baseline to run on the same model, so the headline table isolates the
workflow's contribution rather than a difference in raw model capability.

**Why this is not simply the Groq adapter pointed elsewhere.** That was the
plan, and Groq does speak the OpenAI chat wire format, so it nearly was. But
`gpt-5.6-terra` refuses function tools on `/v1/chat/completions`:

    Function tools with reasoning_effort are not supported for gpt-5.6-terra
    in /v1/chat/completions. To use function tools, use /v1/responses or set
    reasoning_effort to 'none'.

Both ways out work. Setting `reasoning_effort='none'` keeps the chat endpoint
and the shared translation, and gives up the reasoning this tier was chosen
for: the adjudicator does multi step tool use, forms and retries hypotheses,
and returns a structured verdict, which is the whole reason it is not on a
mini tier. So this adapter uses `/v1/responses` and keeps the reasoning.

The cost is that the request and reply shapes differ from the chat API, so
`complete` is written here rather than inherited. Everything else, the client
construction, timeouts, key handling and error classes, still comes from
`openai_compatible.py`.
"""

from __future__ import annotations

import json

from materia.llm.openai_compatible import (
    ModelNotAvailable,
    OpenAICompatibleClient,
    ProviderError,
    RateLimited,
    TokenPacer,
    estimate_tokens,
    parse_arguments,
)
from materia.llm.types import AgentResponse, Message, ToolCall, ToolDefinition, Usage

# The balanced production tool use tier. Not the mini or nano tier: the
# adjudicator does multi step tool use, forms and retries hypotheses, and has
# to return a structured verdict, which is not what the small tiers are for.
DEFAULT_MODEL = "gpt-5.6-terra"


class OpenAIClient(OpenAICompatibleClient):
    provider = "openai"
    BASE_URL = None  # the SDK's own default, api.openai.com
    DEFAULT_MODEL = DEFAULT_MODEL
    API_KEY_VARIABLE = "OPENAI_API_KEY"

    # --- translation, Responses API shapes ---

    @staticmethod
    def _tools(tools: list[ToolDefinition] | None) -> list[dict] | None:
        """Flat, not nested under a `function` key as the chat API wants."""
        if not tools:
            return None
        return [
            {
                "type": "function",
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            }
            for tool in tools
        ]

    @staticmethod
    def _input(messages: list[Message]) -> list[dict]:
        """Turns become input items. A tool call and its result are two items,
        linked by `call_id`, rather than a message carrying a list of calls."""
        items: list[dict] = []
        for message in messages:
            if message.role == "tool":
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": message.tool_call_id,
                        "output": message.content or "",
                    }
                )
                continue

            if message.tool_calls:
                if message.content:
                    items.append({"role": "assistant", "content": message.content})
                items.extend(
                    {
                        "type": "function_call",
                        "call_id": call.id,
                        "name": call.name,
                        "arguments": json.dumps(call.arguments),
                    }
                    for call in message.tool_calls
                )
                continue

            items.append({"role": message.role, "content": message.content or ""})
        return items

    def _translate_error(self, error: Exception) -> ProviderError:
        text = str(error).lower()
        if any(
            phrase in text
            for phrase in ("does not exist", "model_not_found", "invalid model", "deprecated")
        ):
            return ModelNotAvailable(
                f"OpenAI will not serve {self.model!r}: {error}. "
                "Do not guess another model id: a run scored against a model "
                "nobody chose is not a result."
            )
        if "rate_limit" in text or "rate limit" in text or "429" in text:
            return RateLimited(f"OpenAI rate limit reached: {error}")
        if "insufficient_quota" in text or "billing" in text:
            return ProviderError(f"OpenAI refused the request for billing reasons: {error}")
        return ProviderError(f"OpenAI request failed: {error}")

    # --- the interface ---

    def complete(
        self,
        system: str,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
    ) -> AgentResponse:
        request: dict = {
            "model": self.model,
            "instructions": system,
            "input": self._input(messages),
        }
        translated = self._tools(tools)
        if translated:
            request["tools"] = translated

        if self.pacer is not None:
            self.pacer.wait_for(estimate_tokens(request))

        try:
            response = self._client.responses.create(**request)
        except Exception as error:  # noqa: BLE001 - normalised below
            raise self._translate_error(error) from error

        calls = tuple(
            ToolCall(
                id=item.call_id,
                name=item.name,
                arguments=parse_arguments(item.arguments),
            )
            for item in response.output
            if getattr(item, "type", "") == "function_call"
        )

        usage = response.usage
        total = (getattr(usage, "input_tokens", 0) or 0) + (
            getattr(usage, "output_tokens", 0) or 0
        )
        if self.pacer is not None:
            self.pacer.record(total)

        text = (response.output_text or "").strip() or None
        return AgentResponse(
            text=text,
            tool_calls=calls,
            # The chat API's finish_reason has no direct equivalent, so it is
            # derived from what came back rather than invented.
            stop_reason="tool_calls" if calls else (response.status or "completed"),
            usage=Usage(
                input_tokens=getattr(usage, "input_tokens", 0) or 0,
                output_tokens=getattr(usage, "output_tokens", 0) or 0,
            ),
            model=getattr(response, "model", "") or self.model,
            provider=self.provider,
        )


__all__ = ["DEFAULT_MODEL", "OpenAIClient", "TokenPacer"]
