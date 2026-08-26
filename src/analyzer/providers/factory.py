"""Turning configuration into a provider.

Deliberately not in ``providers/__init__.py``: ``config`` imports ``CONTEXT_WINDOW`` from
``providers.base``, so a factory that needs ``Settings`` in the package initialiser makes
the two modules import each other, and whether that succeeds depends on which one the
process happens to reach first. A submodule keeps the chain acyclic in both directions.
"""

from __future__ import annotations

import httpx

from analyzer.providers.base import LLMProvider
from analyzer.providers.ollama import OllamaProvider
from analyzer.providers.openai_compatible import OpenAICompatibleProvider
from config import Settings


def build_provider(
    client: httpx.AsyncClient, settings: Settings, name: str, num_gpu: int | None = None
) -> LLMProvider:
    """Return the backend named by ``name``, wired to the shared HTTP client."""
    if name == "ollama":
        return OllamaProvider(
            client,
            settings.ollama_host,
            context_window=settings.ollama_num_ctx,
            num_gpu=num_gpu,
        )
    return OpenAICompatibleProvider(client, settings.require_openrouter_key())
