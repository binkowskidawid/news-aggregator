"""Reading the analysis queue and persisting what came back.

There is no queue table. ``articles.status`` is the queue, ``attempts`` is the retry
counter, and the back-off is a WHERE clause — which is the whole mechanism, because a
row that is being worked on is simply one whose status has not changed yet.

``save_analysis`` is shared with the evaluation harness rather than duplicated in it. The
direction matters: production code must not import from ``evals/``, so the writer lives
here and ``run_eval.py`` calls it.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final

from psycopg_pool import AsyncConnectionPool

from analyzer.analyze import Analysis
from db import execute, execute_many, fetch_all

MAX_ATTEMPTS: Final = 3
"""Provider failures tolerated per article before it stops being retried."""

RETRY_BACKOFF_HOURS: Final = 1
"""Multiplied by the attempt count, so waits grow 1 h, 2 h, then the article is dropped."""

CONSECUTIVE_FAILURE_LIMIT: Final = 3
"""Failures in a row that mean the backend is down rather than the articles being bad."""


@dataclass(slots=True)
class FailureTracker:
    """Decides whether provider failures belong to the articles or to the provider.

    ``ProviderError`` is a single flat type: the providers wrap ``httpx.HTTPError``, so a
    backend that is not answering at all and a request this particular article got
    rejected for arrive as the same exception. Charging attempts blindly would mean an
    hour of Ollama downtime burning the entire corpus to ``failed_permanent``.

    The signal that separates them is not the exception but the pattern. Failures
    scattered among successes are about the articles; an unbroken run of them is about
    the backend, so the pass stops and forgets what it had collected.
    """

    consecutive: int = 0
    article_ids: list[uuid.UUID] = field(default_factory=list)

    def record_failure(self, article_id: uuid.UUID) -> bool:
        """Charge one failure. Returns True when the pass should abort."""
        self.consecutive += 1
        self.article_ids.append(article_id)
        if self.consecutive < CONSECUTIVE_FAILURE_LIMIT:
            return False
        # The backend is down, not these articles: drop the charges before aborting.
        self.article_ids.clear()
        return True

    def record_success(self) -> None:
        self.consecutive = 0


@dataclass(frozen=True, slots=True)
class PendingArticle:
    article_id: uuid.UUID
    title: str
    lead: str | None


async def load_pending(
    connection_pool: AsyncConnectionPool, limit: int | None = None
) -> list[PendingArticle]:
    """Read articles waiting for analysis, skipping those still inside their back-off.

    Gold articles are excluded. They are the measuring instrument, not the corpus:
    analysing them under a production ``run_id`` would add a phantom configuration row to
    the evaluation report, because ``metrics.score_all`` groups by ``run_id`` and this
    pass uses exactly the configuration the main sweep used. They stay ``pending``, which
    is the correct state for an instrument that is not being consumed.

    Ordered by id, which is uuidv7 and therefore fetch order — the oldest unanalysed
    article is the one most likely to fall out of a feed before anyone looks at it.
    """
    query = """
        SELECT a.id, a.title, a.lead
        FROM articles a
        WHERE a.status = 'pending'
          AND (a.last_attempt_at IS NULL
               OR a.last_attempt_at < now() - (interval '1 hour' * %s * a.attempts))
          AND NOT EXISTS (SELECT 1 FROM gold_articles g WHERE g.article_id = a.id)
        ORDER BY a.id
        """
    params: list[object] = [RETRY_BACKOFF_HOURS]
    if limit is not None:
        query += " LIMIT %s"
        params.append(limit)

    return [
        PendingArticle(article_id=row["id"], title=row["title"], lead=row["lead"])
        for row in await fetch_all(connection_pool, query, params)
    ]


def outcome(analysis: Analysis) -> tuple[str, str | None]:
    """The article status and category one finished analysis implies.

    A response that arrived but did not parse is terminal, not retryable. Under a grammar
    its causes are configuration faults — a clipped context window, an output ceiling —
    and retrying hides the signal that says which setting is wrong.
    """
    if analysis.parse_error is not None:
        return "failed", None
    return "analyzed", analysis.category.value if analysis.category else None


async def save_analysis(
    connection_pool: AsyncConnectionPool,
    *,
    article_id: uuid.UUID,
    run_id: uuid.UUID,
    provider: str,
    model: str,
    analysis: Analysis,
    source_label: str | None = None,
) -> None:
    """Persist one call and the findings that survived quote verification."""
    completion = analysis.completion
    rows = await fetch_all(
        connection_pool,
        """
        INSERT INTO analyses (
            article_id, run_id, provider, model_name, prompt_version, input_variant,
            grammar_mode, source_label, category, category_confidence, overall_assessment,
            raw_response, parse_error, consistency_error, latency_ms, tokens_in, tokens_out,
            quotes_total, quotes_rejected, quotes_fuzzy
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        ) RETURNING id
        """,
        (
            article_id,
            run_id,
            provider,
            model,
            analysis.prompt.version,
            analysis.prompt.input_variant,
            analysis.grammar_mode,
            source_label,
            analysis.category.value if analysis.category else None,
            analysis.category_confidence,
            analysis.overall_assessment.value if analysis.overall_assessment else None,
            # The verbatim reply, including quotes later rejected as unverifiable: the
            # difference between what the model said and what survived is itself a metric.
            json.dumps({"content": completion.content}, ensure_ascii=False),
            analysis.parse_error,
            analysis.consistency_error,
            completion.latency_ms,
            completion.tokens_in,
            completion.tokens_out,
            analysis.quotes_total,
            len(analysis.rejected_quotes),
            analysis.quotes_fuzzy,
        ),
    )
    analysis_id = rows[0]["id"]

    await execute_many(
        connection_pool,
        """
        INSERT INTO findings
            (analysis_id, type, quote, field, quote_start, quote_end, fuzzy_matched,
             neutral_alternative, neutral_similarity, explanation, confidence)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        [
            (
                analysis_id,
                finding.type.value,
                finding.quote,
                finding.field,
                finding.start,
                finding.end,
                finding.fuzzy,
                finding.neutral_alternative,
                finding.neutral_similarity,
                finding.explanation,
                finding.confidence,
            )
            for finding in analysis.findings
        ],
    )


async def mark_analyzed(
    connection_pool: AsyncConnectionPool,
    article_id: uuid.UUID,
    status: str,
    category: str | None,
) -> None:
    """Record the verdict on one article and take it out of the queue."""
    await execute(
        connection_pool,
        "UPDATE articles SET status = %s, category = coalesce(%s, category) WHERE id = %s",
        (status, category, article_id),
    )


async def bump_attempts(
    connection_pool: AsyncConnectionPool, article_ids: Sequence[uuid.UUID]
) -> None:
    """Charge a provider failure to each article, retiring those out of attempts.

    One statement for the whole batch, and the promotion to ``failed_permanent`` is a CASE
    rather than a second pass: the caller has already decided these failures belong to the
    articles, so the decision must not be split across two round trips that a crash could
    land between.
    """
    if not article_ids:
        return
    await execute(
        connection_pool,
        """
        UPDATE articles
        SET attempts = attempts + 1,
            last_attempt_at = now(),
            status = CASE WHEN attempts + 1 >= %s THEN 'failed_permanent' ELSE status END
        WHERE id = ANY(%s)
        """,
        (MAX_ATTEMPTS, list(article_ids)),
    )
