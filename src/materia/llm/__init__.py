"""Provider abstraction. One interface, two adapters, one accountable model.

`MATERIA_PROVIDER` selects between them and is read once at startup. The
provider that produced a results directory is recorded next to it, so a dev
loop run can never be mistaken for a scored one.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from materia.llm.anthropic import AnthropicClient
from materia.llm.groq import GroqClient, RateLimited, TokenPacer
from materia.llm.types import (
    AgentResponse,
    LLMClient,
    Message,
    ModelNotAvailable,
    ProviderError,
    ToolCall,
    ToolDefinition,
    Usage,
)

PROVIDERS = {"groq": GroqClient, "anthropic": AnthropicClient}
DEFAULT_PROVIDER = "groq"
PROVENANCE_NAME = "provider.json"

# Read once, at import, exactly as docs/ARCHITECTURE.md section 9 says. A
# provider that could change part way through a run would make the results
# directory a mixture nobody could interpret.
SELECTED_PROVIDER = os.environ.get("MATERIA_PROVIDER", DEFAULT_PROVIDER).strip().lower()


def get_client(provider: str | None = None, **kwargs) -> LLMClient:
    """Build the configured client."""
    name = (provider or SELECTED_PROVIDER).strip().lower()
    if name not in PROVIDERS:
        raise ProviderError(
            f"unknown provider {name!r}. MATERIA_PROVIDER must be one of "
            f"{sorted(PROVIDERS)}"
        )
    return PROVIDERS[name](**kwargs)


def write_provenance(directory: str | Path, client: LLMClient) -> Path:
    """Record who produced a results directory.

    docs/ARCHITECTURE.md section 9: Groq numbers are never reported. This is
    what makes a stray dev loop run identifiable rather than indistinguishable
    from the scored one.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / PROVENANCE_NAME
    path.write_text(
        json.dumps(
            {
                "provider": client.provider,
                "model": client.model,
                "scored": client.provider == "anthropic",
                "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    return path


def read_provenance(directory: str | Path) -> dict | None:
    path = Path(directory) / PROVENANCE_NAME
    return json.loads(path.read_text()) if path.exists() else None


__all__ = [
    "AgentResponse",
    "AnthropicClient",
    "DEFAULT_PROVIDER",
    "GroqClient",
    "LLMClient",
    "Message",
    "ModelNotAvailable",
    "PROVIDERS",
    "RateLimited",
    "TokenPacer",
    "ProviderError",
    "SELECTED_PROVIDER",
    "ToolCall",
    "ToolDefinition",
    "Usage",
    "get_client",
    "read_provenance",
    "write_provenance",
]
