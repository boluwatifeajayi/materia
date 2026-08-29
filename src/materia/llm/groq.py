"""Groq adapter, via the OpenAI compatible API.

Dev loop only. Groq's free tier serves open models, which is fine for
exercising the tool call loop and catching bugs in the adjudication logic, and
is not a substitute data point for the headline comparison. Numbers from a
Groq run are never reported. See docs/ARCHITECTURE.md section 9.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import deque

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

# The free tier limit for this account, measured rather than assumed: the API
# reports "tokens per minute (TPM): Limit 8000" when it refuses.
TOKENS_PER_MINUTE = 8_000

# Without this the client waits forever on a connection that never answers.
# Observed: one call sat for 26 minutes with no traffic and no way out, which
# stalls a run silently rather than failing it.
REQUEST_TIMEOUT_SECONDS = 120
MAX_RETRIES = 2

# Leave room, because the limit counts the request as well as the reply and
# the request size is only known after it is built.
HEADROOM = 0.80


class TokenPacer:
    """Waits before a call that would break the rate limit.

    Deliberately not a retry loop. CLAUDE.md section 6 rules those out because
    they burn quota silently on requests that were always going to be refused.
    This spends nothing: it holds the request back until the window has room,
    and says so on stderr so a long run does not look like a hang.
    """

    def __init__(self, tokens_per_minute: int = TOKENS_PER_MINUTE) -> None:
        self.budget = int(tokens_per_minute * HEADROOM)
        self._spent: deque[tuple[float, int]] = deque()

    def _used(self, now: float) -> int:
        while self._spent and now - self._spent[0][0] > 60:
            self._spent.popleft()
        return sum(tokens for _, tokens in self._spent)

    def wait_for(self, estimated_tokens: int) -> float:
        """Hold until the coming request fits in the window.

        Waits only long enough for as much spending as the request needs to
        fall out of the window, rather than for the oldest entry regardless.
        On a limit this tight the difference is minutes over a long run.
        """
        waited = 0.0
        while True:
            now = time.monotonic()
            used = self._used(now)
            if used + estimated_tokens <= self.budget:
                return waited

            # A request bigger than the whole budget can never fit, so waiting
            # for room is a loop with no exit. Clear the window once and let
            # the provider decide: refusing it here would be this class making
            # a call that belongs to the API.
            if estimated_tokens > self.budget and not self._spent:
                return waited

            pause = self._pause_until_room(now, used + estimated_tokens - self.budget)
            print(
                f"groq: {used} tokens used in the last minute, "
                f"waiting {pause:.0f}s before a {estimated_tokens} token request",
                file=sys.stderr,
            )
            time.sleep(pause)
            waited += pause

    def _pause_until_room(self, now: float, needed: int) -> float:
        """How long until `needed` tokens have aged out of the window."""
        freed = 0
        for timestamp, tokens in self._spent:
            freed += tokens
            if freed >= needed:
                return max(0.5, 61 - (now - timestamp))
        # Nothing in the window covers it, so the request is larger than the
        # budget itself. Wait the window out and let it through.
        return max(0.5, 61 - (now - self._spent[-1][0])) if self._spent else 0.5

    def record(self, tokens: int) -> None:
        self._spent.append((time.monotonic(), tokens))
# Groq decommissioned the Llama chat models this project was specified
# against. This is what the account is actually served, confirmed by querying
# the provider rather than guessed. Still an open weights model, so the
# reasoning in docs/ARCHITECTURE.md section 9 is unchanged.
DEFAULT_MODEL = "openai/gpt-oss-120b"


class GroqClient:
    provider = "groq"

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        pacer: "TokenPacer | None" = None,
    ) -> None:
        from openai import OpenAI

        key = api_key or os.environ.get("GROQ_API_KEY")
        if not key:
            raise ProviderError("GROQ_API_KEY is not set")
        self.model = model
        self.pacer = TokenPacer() if pacer is None else pacer
        self._client = OpenAI(
            api_key=key,
            base_url=BASE_URL,
            timeout=REQUEST_TIMEOUT_SECONDS,
            max_retries=MAX_RETRIES,
        )

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

        if self.pacer is not None:
            self.pacer.wait_for(_estimate_tokens(request))

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
        if self.pacer is not None:
            self.pacer.record(getattr(usage, "total_tokens", 0) or 0)
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


def _estimate_tokens(request: dict) -> int:
    """A rough size for the outgoing request, to pace against.

    Four characters to the token is close enough for a rate limiter, and the
    reply is allowed for on top.
    """
    return len(json.dumps(request)) // 4 + 600


def _parse_arguments(raw: str | None) -> dict:
    """Tool arguments arrive as a JSON string and are not always valid."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"__unparsed__": raw}
    return parsed if isinstance(parsed, dict) else {"__value__": parsed}


class RateLimited(ProviderError):
    """The provider refused because the account is over its rate limit.

    Its own class so a caller can tell it apart from a real failure and stop
    rather than hammer.
    """


def _translate_error(error: Exception, model: str) -> ProviderError:
    text = str(error).lower()
    if "does not exist" in text or "model_not_found" in text or "decommissioned" in text:
        return ModelNotAvailable(f"Groq will not serve {model!r}: {error}")
    if "rate_limit" in text or "rate limit" in text:
        return RateLimited(f"Groq rate limit reached: {error}")
    return ProviderError(f"Groq request failed: {error}")
