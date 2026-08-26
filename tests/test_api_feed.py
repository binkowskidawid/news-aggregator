"""The two endpoints the reader-facing front end is built on.

The last test here is the one the interface rests on. A finding carries ``start``/``end``
into the original ``title`` or ``lead`` so that a client slices rather than searches, and
the highlight a person sees is that slice. If the offsets survive the validator but not the
HTTP layer — encoded as bytes anywhere along the way, say — every headline with a Polish
diacritic before the span highlights the wrong words, and nothing in the response looks
wrong while it happens.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from psycopg_pool import AsyncConnectionPool

from db import execute, fetch_one


async def _source(connection_pool: AsyncConnectionPool) -> uuid.UUID:
    row = await fetch_one(
        connection_pool,
        """
        INSERT INTO sources (name, base_url, rss_url, strategy)
        VALUES ('Test', 'https://t.test', 'https://t.test/rss', 'rss')
        RETURNING id
        """,
    )
    return row["id"] if row else uuid.uuid4()


async def _article(
    connection_pool: AsyncConnectionPool,
    source_id: uuid.UUID,
    *,
    title: str = "Tytuł",
    lead: str | None = None,
    category: str = "polityka",
    published_at: datetime | None = None,
) -> uuid.UUID:
    """One article plus the analysis that makes it visible to the feed.

    ``input_variant`` follows the rule ``article_latest_analysis`` restates: the lead is
    shown whenever the article carries one. Seeding the other arm would produce a row the
    view refuses, which is the point of the view.
    """
    row = await fetch_one(
        connection_pool,
        """
        INSERT INTO articles (source_id, url, url_hash, title, lead, fetch_level,
                              content_hash, status, category, published_at)
        VALUES (%s, %s, %s, %s, %s, 1, %s, 'analyzed', %s, %s)
        RETURNING id
        """,
        (
            source_id,
            f"https://t.test/{uuid.uuid4().hex}",
            uuid.uuid4().hex,
            title,
            lead,
            uuid.uuid4().hex,
            category,
            published_at or datetime.now(UTC),
        ),
    )
    article_id: uuid.UUID = row["id"] if row else uuid.uuid4()

    await execute(
        connection_pool,
        """
        INSERT INTO analyses (article_id, run_id, provider, model_name, prompt_version,
                              input_variant, grammar_mode, category, overall_assessment)
        VALUES (%s, %s, 'ollama', 'gemma4:latest', 'v1.1.3', %s, 'schema', %s, 'neutral')
        """,
        (article_id, uuid.uuid4(), "title_lead" if lead else "title", category),
    )
    return article_id


async def _finding(
    connection_pool: AsyncConnectionPool,
    article_id: uuid.UUID,
    *,
    field: str,
    start: int,
    end: int,
    quote: str,
    type_: str = "emotional_load",
) -> None:
    await execute(
        connection_pool,
        """
        INSERT INTO findings (analysis_id, type, quote, field, quote_start, quote_end,
                              explanation, confidence)
        SELECT analysis_id, %s, %s, %s, %s, %s, 'bo tak', 0.90
        FROM article_latest_analysis WHERE article_id = %s
        """,
        (type_, quote, field, start, end, article_id),
    )


class TestFeed:
    async def test_newest_first_and_total_counts_the_whole_set(
        self, client: AsyncClient, db_pool: AsyncConnectionPool
    ) -> None:
        source = await _source(db_pool)
        now = datetime.now(UTC)
        for age in range(3):
            await _article(db_pool, source, title=f"T{age}", published_at=now - timedelta(days=age))

        page = (await client.get("/feed", params={"limit": 2})).json()

        assert [item["title"] for item in page["items"]] == ["T0", "T1"]
        assert page["total"] == 3, "total describes the set, not the page"

        second = (await client.get("/feed", params={"limit": 2, "offset": 2})).json()
        assert [item["title"] for item in second["items"]] == ["T2"]
        assert second["total"] == 3

    async def test_category_narrows_both_the_page_and_the_total(
        self, client: AsyncClient, db_pool: AsyncConnectionPool
    ) -> None:
        source = await _source(db_pool)
        await _article(db_pool, source, title="Polityka", category="polityka")
        await _article(db_pool, source, title="Sport", category="sport")

        page = (await client.get("/feed", params={"category": "sport"})).json()

        assert [item["title"] for item in page["items"]] == ["Sport"]
        assert page["total"] == 1

    async def test_an_unanalysed_article_never_reaches_the_feed(
        self, client: AsyncClient, db_pool: AsyncConnectionPool
    ) -> None:
        source = await _source(db_pool)
        await execute(
            db_pool,
            """
            INSERT INTO articles (source_id, url, url_hash, title, fetch_level, content_hash,
                                  status)
            VALUES (%s, 'https://t.test/pending', %s, 'Nieprzeanalizowany', 1, %s, 'pending')
            """,
            (source, uuid.uuid4().hex, uuid.uuid4().hex),
        )

        assert (await client.get("/feed")).json() == {
            "items": [],
            "total": 0,
            "limit": 20,
            "offset": 0,
        }


class TestHasFindingsFilter:
    """Both directions, because the toggle in the interface has both."""

    @pytest.fixture
    async def corpus(self, db_pool: AsyncConnectionPool) -> None:
        source = await _source(db_pool)
        reported = await _article(db_pool, source, title="Spektakularna akcja")
        await _finding(db_pool, reported, field="title", start=0, end=13, quote="Spektakularna")
        await _article(db_pool, source, title="Rada podjęła uchwałę")

    async def test_absent_by_default_the_feed_carries_everything(
        self, client: AsyncClient, corpus: None
    ) -> None:
        page = (await client.get("/feed")).json()

        assert page["total"] == 2
        assert sorted(item["finding_count"] for item in page["items"]) == [0, 1]

    async def test_true_keeps_only_reported_articles(
        self, client: AsyncClient, corpus: None
    ) -> None:
        page = (await client.get("/feed", params={"has_findings": "true"})).json()

        assert page["total"] == 1
        assert page["items"][0]["title"] == "Spektakularna akcja"

    async def test_false_keeps_only_the_ones_nothing_was_reported_in(
        self, client: AsyncClient, corpus: None
    ) -> None:
        page = (await client.get("/feed", params={"has_findings": "false"})).json()

        assert page["total"] == 1
        assert page["items"][0]["title"] == "Rada podjęła uchwałę"


class TestArticleDetail:
    async def test_an_article_without_a_production_analysis_is_a_404(
        self, client: AsyncClient, db_pool: AsyncConnectionPool
    ) -> None:
        """Not "no analysis yet": an empty verdict rendered by a client reads as a clean
        bill of health for an article nothing has looked at."""
        source = await _source(db_pool)
        row = await fetch_one(
            db_pool,
            """
            INSERT INTO articles (source_id, url, url_hash, title, fetch_level, content_hash,
                                  status)
            VALUES (%s, 'https://t.test/only', %s, 'Bez analizy', 1, %s, 'pending')
            RETURNING id
            """,
            (source, uuid.uuid4().hex, uuid.uuid4().hex),
        )

        response = await client.get(f"/articles/{row['id'] if row else uuid.uuid4()}")

        assert response.status_code == 404

    async def test_provenance_says_the_analysis_is_generated(
        self, client: AsyncClient, db_pool: AsyncConnectionPool
    ) -> None:
        source = await _source(db_pool)
        article_id = await _article(db_pool, source)

        provenance = (await client.get(f"/articles/{article_id}")).json()["provenance"]

        assert provenance["ai_generated"] is True
        assert provenance["human_verified"] is False
        assert provenance["prompt_version"] == "v1.1.3"

    async def test_the_span_slices_back_to_the_quote_through_http(
        self, client: AsyncClient, db_pool: AsyncConnectionPool
    ) -> None:
        """The property the whole interface rests on, asserted across the network boundary.

        Both strings carry diacritics before the span, so an offset counted in bytes rather
        than characters lands somewhere else and the assertion fails — which is exactly the
        failure that would otherwise reach a reader as a confidently highlighted phrase the
        model never reported.
        """
        source = await _source(db_pool)
        title = "Wstrząsająca relacja świadka"
        lead = "Zażądał wyjaśnień od ministra"
        article_id = await _article(db_pool, source, title=title, lead=lead)
        await _finding(db_pool, article_id, field="title", start=13, end=20, quote=title[13:20])
        await _finding(db_pool, article_id, field="lead", start=0, end=7, quote=lead[0:7])

        article = (await client.get(f"/articles/{article_id}")).json()

        assert len(article["findings"]) == 2
        for finding in article["findings"]:
            source_text = article[finding["field"]]
            assert source_text[finding["start"] : finding["end"]] == finding["quote"]
