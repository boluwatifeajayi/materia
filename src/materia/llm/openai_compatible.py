"""The half of an adapter that is the OpenAI wire format.

Groq serves an OpenAI compatible API, so both providers talk the same
protocol through the same SDK and differ only in where they point, what they
are rate limited to, and how they word a refusal. That shared half lives here
rather than being copied into each adapter, so a fix to the message
translation cannot land in one provider and not the other.

What stays provider specific: the base URL, the default model, whether a rate
limiter is needed, and the strings a provider uses when it refuses.
"""

from __future__ import annotations

import json
import re
import sys
import time
from collections import deque

from materia.llm.types import (
    AgentResponse,
    Message,
    ModelNotAvailable,  # noqa: F401 - re-exported to the provider adapters
    ProviderError,
    ToolCall,
    ToolDefinition,
    Usage,
)

# Without this the client waits forever on a connection that never answers.
# Observed: one call sat for 26 minutes with no traffic and no way out, which
# stalls a run silently rather than failing it.
REQUEST_TIMEOUT_SECONDS = 120
MAX_RETRIES = 2


class RateLimited(ProviderError):
    """The provider refused because the account is over its rate limit.

    Its own class so a caller can tell it apart from a real failure and stop
    rather than hammer.
    """


class TokenPacer:
    """Waits before a call that would break a tokens per minute limit.

    Deliberately not a retry loop. CLAUDE.md section 6 rules those out because
    they burn quota silently on requests that were always going to be refused.
    This spends nothing: it holds the request back until the window has room,
    and says so on stderr so a long run does not look like a hang.

    Only needed where a tier is tight enough that one request can exceed the
    window on its own.
    """

    # Leave room, because the limit counts the reply as well as the request
    # and the reply size is only known afterwards.
    HEADROOM = 0.80

    def __init__(self, tokens_per_minute: int) -> None:
        self.budget = int(tokens_per_minute * self.HEADROOM)
        self._spent: deque[tuple[float, int]] = deque()

    def _used(self, now: float) -> int:
        while self._spent and now - self._spent[0][0] > 60:
            self._spent.popleft()
        return sum(tokens for _, tokens in self._spent)

    def wait_for(self, estimated_tokens: int) -> float:
        """Hold until the coming request fits in the window.

        Waits only long enough for as much spending as the request needs to
        fall out of the window, rather than for the oldest entry regardless.
        On a tight limit the difference is minutes over a long run.
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
                f"pacing: {used} tokens used in the last minute, "
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
        return max(0.5, 61 - (now - self._spent[-1][0])) if self._spent else 0.5

    def record(self, tokens: int) -> None:
        self._spent.append((time.monotonic(), tokens))


def parse_arguments(raw: str | None) -> dict:
    """Tool arguments arrive as a JSON string and are not always valid."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"__unparsed__": raw}
    return parsed if isinstance(parsed, dict) else {"__value__": parsed}


def estimate_tokens(request: dict) -> int:
    """A rough size for the outgoing request, to pace against.

    Four characters to the token is close enough for a rate limiter, and the
    reply is allowed for on top.
    """
    return len(json.dumps(request)) // 4 + 600


_ACCOUNT_ID = re.compile(r"\borg_[A-Za-z0-9]{8,}")


def scrub(text: str) -> str:
    """Take the account identifier out of a provider error before it is traced.

    Rate limit messages name the organisation the key belongs to. That is not
    a credential, but trajectories are a published deliverable and there is no
    reason for an account id to be in one.
    """
    return _ACCOUNT_ID.sub("org_[redacted]", text)


class OpenAICompatibleClient:
    """Everything both providers do identically.

    A subclass supplies `provider`, `BASE_URL`, `DEFAULT_MODEL`, and a
    `_translate_error` for the way that provider words a refusal.
    """

    provider = "openai-compatible"
    BASE_URL: str | None = None
    DEFAULT_MODEL = ""
    API_KEY_VARIABLE = ""

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        pacer: TokenPacer | None = None,
    ) -> None:
        import os

        from openai import OpenAI

        key = api_key or os.environ.get(self.API_KEY_VARIABLE)
        if not key:
            raise ProviderError(self._missing_key_message())

        self.model = model or self.DEFAULT_MODEL
        self.pacer = pacer
        self._client = OpenAI(
            api_key=key,
            timeout=REQUEST_TIMEOUT_SECONDS,
            max_retries=MAX_RETRIES,
            **({"base_url": self.BASE_URL} if self.BASE_URL else {}),
        )

    def _missing_key_message(self) -> str:
        import os

        selected = os.environ.get("MATERIA_PROVIDER", "groq (the default)")
        return (
            f"{self.API_KEY_VARIABLE} is not set. MATERIA_PROVIDER is "
            f"{selected!r}. Set the key, or set MATERIA_PROVIDER to the other "
            "provider and its key instead."
        )

    # --- translation, identical for every OpenAI compatible endpoint ---

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

    def _translate_error(self, error: Exception) -> ProviderError:
        """Normalise a provider's refusal.

        No default. Providers word these differently and a generic guess would
        quietly classify a wrong model id as a transient failure, which is the
        one mistake this class exists to prevent. Each adapter says how its own
        provider phrases things.
        """
        raise NotImplementedError(
            f"{type(self).__name__} must say how {self.provider} words a refusal"
        )

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
            self.pacer.wait_for(estimate_tokens(request))

        try:
            completion = self._client.chat.completions.create(**request)
        except Exception as error:  # noqa: BLE001 - normalised below
            raise self._translate_error(error) from error

        choice = completion.choices[0]
        calls = tuple(
            ToolCall(
                id=call.id,
                name=call.function.name,
                arguments=parse_arguments(call.function.arguments),
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
