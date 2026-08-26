"""The interface every LLM backend implements.

One protocol, two implementations, chosen by configuration. This exists so the choice
between a locally hosted model and a hosted API can be settled by measurement rather than
by argument, and so that a negative result about local models does not invalidate the
work already done.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from analyzer.prompts import ChatMessage

GrammarMode = Literal["schema", "json"]
"""How hard the backend is asked to constrain the output.

``schema``  the full JSON Schema, compiled into a sampling grammar. The model cannot emit
            a token that breaks the structure.
``json``    syntactic validity only; the shape is described in the prompt instead.

Both are measured. Constrained sampling guarantees the response's shape, not its content,
and on smaller models it can push generation into a worse region — structurally flawless
JSON containing a shallower analysis. Which effect dominates is an empirical question.
"""


class ProviderError(RuntimeError):
    """A request to the backend failed or returned something unusable."""


@dataclass(frozen=True, slots=True)
class Completion:
    """One raw model response, with the measurements needed to interpret it."""

    content: str
    """Response body, exactly as returned. Parsing happens downstream."""

    model: str
    """Model actually served. Providers may route elsewhere than requested."""

    latency_ms: int
    tokens_in: int | None
    tokens_out: int | None

    @property
    def hit_output_ceiling(self) -> bool:
        """Whether generation likely stopped at the token limit rather than finishing.

        The usual cause of an unparseable response under a grammar: the structure held
        until the budget ran out mid-object. Distinguishing it from a model failure is
        what stops a configuration mistake from being read as a capability result.
        """
        return self.tokens_out is not None and self.tokens_out >= OUTPUT_TOKEN_LIMIT


OUTPUT_TOKEN_LIMIT: int = 4096
"""Generous for one analysis of a headline and lead; small enough to bound a runaway.

Raised from 1024 on 2026-08-20. ``gemma4`` carries a ``thinking`` capability, and on
roughly one article in 150 it enters that mode: Ollama returns ``done_reason: "length"``
with the whole budget spent in ``message.thinking`` and ``message.content`` empty, so the
analysis fails to parse and the article is retired to ``failed``. Five of 738 production
articles died that way; all five recover at 4096.

The ceiling is a stop condition, not prompt content, so raising it cannot change a response
that finished below the old one. Measured rather than assumed: ten gold articles at 1024
against 4096 gave 8/10 byte-identical answers, the same figure the control arm produced by
calling the identical payload twice, and both differences were a decimal of
``category_confidence`` with the findings unchanged.

``think: false`` would have been the narrower fix and was rejected on measurement: it gave
6/10, and its three differences were substantive — two neutral articles acquired a finding.
It alters the chat template, so it changes behaviour on articles that never thought at all.
"""

CONTEXT_WINDOW: int = 16384
"""Room for the system prompt, the worked examples, the article, and the response.

Sized from measurement, not habit. The assembled prompt measures roughly 9,200 tokens, and
generation may add up to OUTPUT_TOKEN_LIMIT — so anything at or below 8,192 leaves the
model no room to answer. llama.cpp does not refuse in that situation: it slides the
window, silently discarding the oldest tokens, which are the opening of the system
prompt where the role and the restraint rules live. The result is a model that appears
to ignore instructions it was never shown.

Doubling the window costs on the order of a gigabyte of KV cache, which is cheap next to
the cost of misreading a configuration fault as a capability limit. Worth re-measuring on
the deployment host, where memory is scarcer — hence the environment override.
"""

TEMPERATURE: float = 0.1
"""Near-deterministic. Not zero: some implementations degenerate into loops there."""

REQUEST_TIMEOUT_S: float = 180.0
"""Generous — a large quantisation on CPU is slow, and a timeout would read as failure."""


class LLMProvider(Protocol):
    """Anything that can turn a message list into a completion."""

    @property
    def name(self) -> str:
        """Stable identifier stored with every analysis (``ollama``/``openrouter``)."""
        ...

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        model: str,
        grammar_mode: GrammarMode,
        schema: dict[str, Any],
    ) -> Completion:
        """Send one request and return the raw response."""
        ...
