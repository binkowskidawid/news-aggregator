"""OpenAI-compatible backend, used here against OpenRouter.

Serves as the cloud baseline: how much quality a local model gives up is not answerable
without one. OpenRouter fronts many vendors behind a single key, which also makes it
possible to test — rather than assume — whether commercial providers decline to assess
named political outlets. That refusal risk is the stronger of the two arguments for
self-hosting, and it deserves evidence.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from analyzer.prompts import ChatMessage
from analyzer.providers.base import (
    OUTPUT_TOKEN_LIMIT,
    REQUEST_TIMEOUT_S,
    TEMPERATURE,
    Completion,
    GrammarMode,
    ProviderError,
)

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


class OpenAICompatibleProvider:
    """Chat-completions client for OpenRouter and anything speaking the same protocol."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        pin_provider: bool = True,
    ) -> None:
        self._client = client
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._pin_provider = pin_provider

    @property
    def name(self) -> str:
        return "openrouter"

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        model: str,
        grammar_mode: GrammarMode,
        schema: dict[str, Any],
    ) -> Completion:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": TEMPERATURE,
            "max_tokens": OUTPUT_TOKEN_LIMIT,
            "response_format": self._response_format(grammar_mode, schema),
        }

        if self._pin_provider:
            # Structured-output support is a property of the endpoint, not of the model:
            # the same model served by two vendors may enforce the schema in one case and
            # silently ignore the parameter in the other. Left to its defaults, OpenRouter
            # routes to a vendor that ignores what it does not understand — which would
            # mean comparing constrained local generation against unconstrained cloud
            # generation while believing both were constrained. require_parameters turns
            # that into a routing error; disabling fallbacks keeps a run reproducible.
            payload["provider"] = {
                "require_parameters": True,
                "allow_fallbacks": False,
            }

        started = time.perf_counter()
        try:
            response = await self._client.post(
                f"{self._base_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=httpx.Timeout(REQUEST_TIMEOUT_S),
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(f"openrouter request failed for {model}: {exc}") from exc
        latency_ms = int((time.perf_counter() - started) * 1000)

        body = response.json()
        if "error" in body:
            # OpenRouter reports some upstream failures inside a 200 response.
            raise ProviderError(f"openrouter returned an error for {model}: {body['error']}")

        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise ProviderError(f"openrouter returned no choices for {model}") from exc
        if not isinstance(content, str):
            raise ProviderError(f"openrouter returned non-text content for {model}")

        usage = body.get("usage") or {}
        return Completion(
            content=content,
            model=body.get("model", model),
            latency_ms=latency_ms,
            tokens_in=usage.get("prompt_tokens"),
            tokens_out=usage.get("completion_tokens"),
        )

    @staticmethod
    def _response_format(grammar_mode: GrammarMode, schema: dict[str, Any]) -> dict[str, Any]:
        if grammar_mode == "json":
            return {"type": "json_object"}
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "manipulation_analysis",
                "strict": True,
                "schema": schema,
            },
        }
