"""The queue and the writer, against a real Postgres.

Everything here is SQL that no unit test executes: a back-off expressed as a ``WHERE``
clause, a retirement expressed as a ``CASE``, an insert that spans two tables. A mistake in
any of them is silent — the pass simply analyses the wrong set of articles, or none.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from psycopg_pool import AsyncConnectionPool

from analyzer.analyze import Analysis, VerifiedFinding
from analyzer.prompts import InputVariant, Prompt
from analyzer.providers.base import Completion
from analyzer.store import (
    MAX_ATTEMPTS,
    bump_attempts,
    load_pending,
    mark_analyzed,
    save_analysis,
)
from db import execute, fetch_all
from domain.analysis import Assessment, Category, ManipulationType

TITLE = 'Szokujące kulisy afery. Politycy „zapomnieli" zgłosić luksusowe zakupy'


async def _source(connection_pool: AsyncConnectionPool) -> uuid.UUID:
    rows = await fetch_all(
        connection_pool,
        """
        INSERT INTO sources (name, base_url, rss_url, strategy)
        VALUES ('Test', 'https://example.test', 'https://example.test/rss', 'rss')
        RETURNING id
        """,
    )
    return rows[0]["id"]  # type: ignore[no-any-return]


async def _article(
    connection_pool: AsyncConnectionPool,
    source_id: uuid.UUID,
    *,
    title: str = TITLE,
    lead: str | None = None,
    attempts: int = 0,
    stale: bool = True,
) -> uuid.UUID:
    """Insert one pending article.

    ``stale`` decides whether its last attempt is old enough to be due again, which is the
    only thing the back-off clause looks at.
    """
    last_attempt = "now() - interval '48 hours'" if stale else "now()"
    rows = await fetch_all(
        connection_pool,
        f"""
        INSERT INTO articles
            (source_id, url, url_hash, title, lead, fetch_level, content_hash,
             attempts, last_attempt_at)
        VALUES (%s, %s, %s, %s, %s, 1, %s, %s,
                CASE WHEN %s = 0 THEN NULL ELSE {last_attempt} END)
        RETURNING id
        """,  # noqa: S608
        (
            source_id,
            f"https://example.test/{uuid.uuid4()}",
            uuid.uuid4().hex,
            title,
            lead,
            uuid.uuid4().hex,
            attempts,
            attempts,
        ),
    )
    return rows[0]["id"]  # type: ignore[no-any-return]


def _analysis(
    findings: tuple[VerifiedFinding, ...] = (),
    *,
    version: str = "v1.1.0",
    input_variant: InputVariant = "title",
    parse_error: str | None = None,
) -> Analysis:
    return Analysis(
        completion=Completion(
            content='{"category": "polityka"}',
            model="gemma4:latest",
            latency_ms=4300,
            tokens_in=900,
            tokens_out=120,
        ),
        prompt=Prompt(messages=[], nonce="abcd1234", version=version, input_variant=input_variant),
        grammar_mode="schema",
        category=Category.POLITYKA,
        category_confidence=0.9,
        # analyses_outcome_exclusive: a row carries a verdict or a parse error, never both.
        overall_assessment=None if parse_error else Assessment.MILDLY_LOADED,
        findings=findings,
        rejected_quotes=(),
        parse_error=parse_error,
    )


async def _status(connection_pool: AsyncConnectionPool, article_id: uuid.UUID) -> dict[str, Any]:
    rows = await fetch_all(
        connection_pool,
        "SELECT status, attempts, category FROM articles WHERE id = %s",
        (article_id,),
    )
    return rows[0]


class TestLoadPending:
    async def test_an_article_inside_its_backoff_is_not_due(
        self, db_pool: AsyncConnectionPool
    ) -> None:
        source_id = await _source(db_pool)
        due = await _article(db_pool, source_id, attempts=1, stale=True)
        await _article(db_pool, source_id, attempts=1, stale=False)

        pending = await load_pending(db_pool)

        assert [article.article_id for article in pending] == [due]

    async def test_gold_articles_never_enter_the_production_queue(
        self, db_pool: AsyncConnectionPool
    ) -> None:
        """They are the measuring instrument. A production run over them would add a
        phantom configuration row to the evaluation report."""
        source_id = await _source(db_pool)
        ordinary = await _article(db_pool, source_id)
        gold = await _article(db_pool, source_id)
        await execute(
            db_pool,
            """
            INSERT INTO gold_articles (article_id, expected_category, expected_assessment,
                                       kind, labeled_by)
            VALUES (%s, 'polityka', 'neutral', 'neutral', 'test')
            """,
            (gold,),
        )

        pending = await load_pending(db_pool)

        assert [article.article_id for article in pending] == [ordinary]

    async def test_the_lead_travels_with_the_article(self, db_pool: AsyncConnectionPool) -> None:
        """Production shows the model the lead where there is one; if it were dropped here
        the pass would silently fall back to the variant that fails the gate."""
        source_id = await _source(db_pool)
        await _article(db_pool, source_id, lead="Zdaniem ekspertów sprawa jest bezprecedensowa.")

        pending = await load_pending(db_pool)

        assert pending[0].lead == "Zdaniem ekspertów sprawa jest bezprecedensowa."


class TestBumpAttempts:
    async def test_an_article_is_retired_exactly_at_the_attempt_limit(
        self, db_pool: AsyncConnectionPool
    ) -> None:
        source_id = await _source(db_pool)
        article_id = await _article(db_pool, source_id, attempts=MAX_ATTEMPTS - 2)

        await bump_attempts(db_pool, [article_id])
        assert (await _status(db_pool, article_id))["status"] == "pending"

        await bump_attempts(db_pool, [article_id])
        record = await _status(db_pool, article_id)
        assert record["status"] == "failed_permanent"
        assert record["attempts"] == MAX_ATTEMPTS

    async def test_charging_nobody_touches_nothing(self, db_pool: AsyncConnectionPool) -> None:
        """The empty case is the one that runs after every healthy pass."""
        source_id = await _source(db_pool)
        article_id = await _article(db_pool, source_id)

        await bump_attempts(db_pool, [])

        assert (await _status(db_pool, article_id))["attempts"] == 0


class TestSaveAnalysis:
    async def test_a_stored_quote_is_the_span_the_offsets_point_at(
        self, db_pool: AsyncConnectionPool
    ) -> None:
        """The property the interface rests on: highlight by offset, render the quote, and
        the reader must see one thing rather than two."""
        source_id = await _source(db_pool)
        article_id = await _article(db_pool, source_id)
        start = TITLE.index("Szokujące kulisy")
        finding = VerifiedFinding(
            type=ManipulationType.EMOTIONAL_LOAD,
            quote=TITLE[start : start + len("Szokujące kulisy")],
            field="title",
            start=start,
            end=start + len("Szokujące kulisy"),
            fuzzy=False,
            neutral_alternative="Kulisy",
            neutral_similarity=50.0,
            explanation="wzmocnienie emocjonalne",
            confidence=0.85,
        )

        await save_analysis(
            db_pool,
            article_id=article_id,
            run_id=uuid.uuid4(),
            provider="ollama",
            model="gemma4:latest",
            analysis=_analysis((finding,)),
        )

        rows = await fetch_all(
            db_pool,
            """
            SELECT f.quote,
                   substring(a.title FROM f.quote_start + 1
                             FOR f.quote_end - f.quote_start) AS sliced
            FROM findings f
            JOIN analyses an ON an.id = f.analysis_id
            JOIN articles a ON a.id = an.article_id
            """,
        )

        assert rows[0]["quote"] == rows[0]["sliced"]

    async def test_one_call_writes_one_analysis_and_its_findings(
        self, db_pool: AsyncConnectionPool
    ) -> None:
        source_id = await _source(db_pool)
        article_id = await _article(db_pool, source_id)

        await save_analysis(
            db_pool,
            article_id=article_id,
            run_id=uuid.uuid4(),
            provider="ollama",
            model="gemma4:latest",
            analysis=_analysis(),
        )

        rows = await fetch_all(
            db_pool,
            """
            SELECT model_name, input_variant, grammar_mode, latency_ms, tokens_in,
                   consistency_error
            FROM analyses
            """,
        )
        assert len(rows) == 1
        assert rows[0]["model_name"] == "gemma4:latest"
        assert rows[0]["input_variant"] == "title"
        assert rows[0]["latency_ms"] == 4300
        assert rows[0]["consistency_error"] is None


class TestMarkAnalyzed:
    @pytest.mark.parametrize(
        ("category", "expected"), [("sport", "sport"), (None, "polityka")], ids=["set", "keep"]
    )
    async def test_a_missing_category_does_not_erase_the_stored_one(
        self, db_pool: AsyncConnectionPool, category: str | None, expected: str
    ) -> None:
        """``coalesce`` in the UPDATE. A parse failure carries no category, and wiping the
        one from an earlier successful pass would lose data the feed sorts on."""
        source_id = await _source(db_pool)
        article_id = await _article(db_pool, source_id)
        await mark_analyzed(db_pool, article_id, "analyzed", "polityka")

        await mark_analyzed(db_pool, article_id, "analyzed", category)

        assert (await _status(db_pool, article_id))["category"] == expected


class TestArticleLatestAnalysis:
    """The view the reader-facing layer reads.

    ``analyses`` keeps every call ever made of an article — production passes, evaluation
    sweeps, and however many prompt versions the corpus has lived through. Picking the
    wrong one of those is invisible: the feed renders a real analysis of the right article,
    just not the one production would produce today.
    """

    async def _store(
        self,
        connection_pool: AsyncConnectionPool,
        article_id: uuid.UUID,
        *,
        version: str = "v1.1.0",
        input_variant: InputVariant = "title",
        parse_error: str | None = None,
        aged_hours: int = 0,
    ) -> None:
        """Store one analysis, optionally backdated so recency is decided, not raced."""
        await save_analysis(
            connection_pool,
            article_id=article_id,
            run_id=uuid.uuid4(),
            provider="ollama",
            model="gemma4:latest",
            analysis=_analysis(
                version=version, input_variant=input_variant, parse_error=parse_error
            ),
        )
        if aged_hours:
            await execute(
                connection_pool,
                """
                UPDATE analyses SET created_at = now() - (interval '1 hour' * %s)
                WHERE article_id = %s AND created_at = (
                    SELECT max(created_at) FROM analyses WHERE article_id = %s)
                """,
                (aged_hours, article_id, article_id),
            )

    async def _view(self, connection_pool: AsyncConnectionPool) -> list[dict[str, Any]]:
        return await fetch_all(
            connection_pool,
            "SELECT article_id, prompt_version, input_variant FROM article_latest_analysis",
        )

    async def test_the_newest_analysis_is_the_one_the_reader_gets(
        self, db_pool: AsyncConnectionPool
    ) -> None:
        source_id = await _source(db_pool)
        article_id = await _article(db_pool, source_id)
        await self._store(db_pool, article_id, version="v1.1.0", aged_hours=48)
        await self._store(db_pool, article_id, version="v1.1.3")

        rows = await self._view(db_pool)

        assert len(rows) == 1
        assert rows[0]["prompt_version"] == "v1.1.3"

    async def test_the_title_arm_of_a_sweep_never_beats_the_production_variant(
        self, db_pool: AsyncConnectionPool
    ) -> None:
        """The rule this view exists for. `make eval` runs both input variants, so an
        article carrying a lead ends up with a title-only analysis too — newer than the
        production row and describing less of the article. Recency alone would pick it.
        """
        source_id = await _source(db_pool)
        article_id = await _article(db_pool, source_id, lead="Sprawa jest bezprecedensowa.")
        await self._store(db_pool, article_id, input_variant="title_lead", aged_hours=48)
        await self._store(db_pool, article_id, input_variant="title")

        rows = await self._view(db_pool)

        assert len(rows) == 1
        assert rows[0]["input_variant"] == "title_lead"

    async def test_an_article_without_a_lead_is_served_by_its_title_only_analysis(
        self, db_pool: AsyncConnectionPool
    ) -> None:
        """The other half of the same CASE: production shows the lead only where there is
        one, so for these articles `title` is the production variant, not a sweep artefact.
        """
        source_id = await _source(db_pool)
        article_id = await _article(db_pool, source_id, lead=None)
        await self._store(db_pool, article_id, input_variant="title")

        rows = await self._view(db_pool)

        assert [row["input_variant"] for row in rows] == ["title"]

    async def test_a_response_that_did_not_parse_is_not_served_at_all(
        self, db_pool: AsyncConnectionPool
    ) -> None:
        """It is recorded rather than repaired, so it stays in `analyses` forever. Newest
        and unusable is the one combination that must not reach a reader."""
        source_id = await _source(db_pool)
        article_id = await _article(db_pool, source_id)
        await self._store(db_pool, article_id, version="v1.1.3", aged_hours=48)
        await self._store(db_pool, article_id, version="v1.1.3", parse_error="unterminated string")

        rows = await self._view(db_pool)

        assert len(rows) == 1
