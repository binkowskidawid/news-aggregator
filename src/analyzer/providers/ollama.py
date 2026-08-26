"""Ollama backend — a model running on hardware we control."""

from __future__ import annotations

import time
from typing import Any

import httpx

from analyzer.prompts import ChatMessage
from analyzer.providers.base import (
    CONTEXT_WINDOW,
    OUTPUT_TOKEN_LIMIT,
    REQUEST_TIMEOUT_S,
    TEMPERATURE,
    Completion,
    GrammarMode,
    ProviderError,
)


class OllamaProvider:
    """Talks to an Ollama server over its chat API.

    During local development that server runs natively on the host rather than in a
    container: Docker Desktop on macOS caps the VM's memory well below the host's and
    passes no GPU through, so the larger quantisations neither fit nor reach Metal.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        host: str,
        *,
        context_window: int = CONTEXT_WINDOW,
        num_gpu: int | None = None,
    ) -> None:
        self._client = client
        self._host = host.rstrip("/")
        self._context_window = context_window
        self._num_gpu = num_gpu
        """Layers to offload to the GPU. ``0`` forces CPU-only inference, which is how the
        deployment-cost question gets an answer without renting a server first. ``None``
        leaves the decision to Ollama, which is what every normal run wants."""

    @property
    def name(self) -> str:
        return "ollama"

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        model: str,
        grammar_mode: GrammarMode,
        schema: dict[str, Any],
    ) -> Completion:
        # Passing the full schema makes Ollama compile it into a grammar that constrains
        # sampling token by token; the string "json" only asks for valid syntax.
        response_format: Any = schema if grammar_mode == "schema" else "json"

        options: dict[str, Any] = {
            "temperature": TEMPERATURE,
            "num_ctx": self._context_window,
            "num_predict": OUTPUT_TOKEN_LIMIT,
        }
        if self._num_gpu is not None:
            options["num_gpu"] = self._num_gpu

        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "format": response_format,
            "options": options,
        }

        started = time.perf_counter()
        try:
            response = await self._client.post(
                f"{self._host}/api/chat",
                json=payload,
                timeout=httpx.Timeout(REQUEST_TIMEOUT_S),
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(f"ollama request failed for {model}: {exc}") from exc
        latency_ms = int((time.perf_counter() - started) * 1000)

        body = response.json()
        content = body.get("message", {}).get("content")
        if not isinstance(content, str):
            raise ProviderError(f"ollama returned no message content for {model}")

        tokens_in = body.get("prompt_eval_count")
        if isinstance(tokens_in, int) and tokens_in + OUTPUT_TOKEN_LIMIT > self._context_window:
            # Prompt plus a full-length answer cannot fit, so llama.cpp will slide the
            # window and drop the start of the system prompt. Loud, because the symptom
            # otherwise looks exactly like a model that ignores its instructions.
            raise ProviderError(
                f"prompt of {tokens_in} tokens leaves no room to generate "
                f"{OUTPUT_TOKEN_LIMIT} within num_ctx={self._context_window}; "
                "raise OLLAMA_NUM_CTX or shorten the prompt"
            )

        return Completion(
            content=content,
            model=body.get("model", model),
            latency_ms=latency_ms,
            # Ollama reports prompt/response counts under these keys; absent on some
            # builds, so they stay optional rather than defaulting to a misleading zero.
            tokens_in=tokens_in,
            tokens_out=body.get("eval_count"),
        )
