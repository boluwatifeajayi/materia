"""Groq adapter.

Dev loop only. Groq's free tier serves open models, which is fine for
exercising the tool call loop and catching bugs in the adjudication logic, and
is not a substitute data point for the headline comparison. Numbers from a
Groq run are never reported. See docs/ARCHITECTURE.md section 9.

Groq speaks the OpenAI wire format, so everything except the base URL, the
free tier rate limit and the wording of a refusal comes from
`openai_compatible.py`.
"""

from __future__ import annotations

from materia.llm.openai_compatible import (
    MAX_RETRIES,
    REQUEST_TIMEOUT_SECONDS,
    ModelNotAvailable,
    OpenAICompatibleClient,
    ProviderError,
    RateLimited,
    TokenPacer,
    estimate_tokens,
    parse_arguments,
    scrub,
)

BASE_URL = "https://api.groq.com/openai/v1"

# The free tier limit for this account, measured rather than assumed: the API
# reports "tokens per minute (TPM): Limit 8000" when it refuses. Tight enough
# that a single adjudication exceeds it, hence the pacer.
TOKENS_PER_MINUTE = 8_000

# Groq decommissioned the Llama chat models this project was specified
# against. This is what the account is actually served, confirmed by querying
# the provider rather than guessed.
DEFAULT_MODEL = "openai/gpt-oss-120b"


class GroqClient(OpenAICompatibleClient):
    provider = "groq"
    BASE_URL = BASE_URL
    DEFAULT_MODEL = DEFAULT_MODEL
    API_KEY_VARIABLE = "GROQ_API_KEY"

    def __init__(self, model: str | None = None, api_key: str | None = None, pacer=None):
        # The free tier is tight enough that an unpaced client fails on its
        # second call, so one is fitted unless a caller says otherwise.
        super().__init__(
            model, api_key, TokenPacer(TOKENS_PER_MINUTE) if pacer is None else pacer
        )

    def _translate_error(self, error: Exception) -> ProviderError:
        text = str(error).lower()
        message = scrub(str(error))
        if "does not exist" in text or "model_not_found" in text or "decommissioned" in text:
            return ModelNotAvailable(f"Groq will not serve {self.model!r}: {message}")
        if "rate_limit" in text or "rate limit" in text:
            return RateLimited(f"Groq rate limit reached: {message}")
        return ProviderError(f"Groq request failed: {message}")


def _translate_error(error: Exception, model: str) -> ProviderError:
    """Kept as a module level helper because the tests reach for it directly."""
    client = GroqClient.__new__(GroqClient)
    client.model = model
    return client._translate_error(error)


_parse_arguments = parse_arguments
_estimate_tokens = estimate_tokens

__all__ = [
    "BASE_URL",
    "DEFAULT_MODEL",
    "GroqClient",
    "MAX_RETRIES",
    "REQUEST_TIMEOUT_SECONDS",
    "RateLimited",
    "TOKENS_PER_MINUTE",
    "TokenPacer",
]
