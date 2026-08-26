"""Persisting what a pass fetched.

Deduplication is not implemented here, because it is already implemented in the schema:
``articles.url_hash`` is UNIQUE, so re-inserting an article the pipeline has seen before
is ``ON CONFLICT DO NOTHING``. The same hash function backs the gold loader, so an
evaluation article that ingest happens to pick up collapses onto the existing row instead
of appearing twice under two ids.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any, cast

from psycopg_pool import AsyncConnectionPool

from db import execute, execute_many, fetch_all, fetch_one
from ingest.fetch import FetchedArticle, FetchResult, Source, digest


async def load_sources(
    connection_pool: AsyncConnectionPool, names: Sequence[str] = ()
) -> list[Source]:
    """Read the active sources, optionally narrowed to a few by name."""
    query = """
        SELECT id, name, base_url, rss_url, strategy, selectors, last_etag, last_fetch_at
        FROM sources
        WHERE active
        """
    params: list[Any] = []
    if names:
        query += " AND name = ANY(%s)"
        params.append(list(names))
    query += " ORDER BY name"

    return [
        Source(
            id=row["id"],
            name=row["name"],
            base_url=row["base_url"],
            rss_url=row["rss_url"],
            strategy=row["strategy"],
            selectors=cast("dict[str, str]", row["selectors"] or {}),
            last_etag=row["last_etag"],
            last_fetch_at=row["last_fetch_at"],
        )
        for row in await fetch_all(connection_pool, query, params)
    ]


async def count_articles(connection_pool: AsyncConnectionPool) -> int:
    """Total rows in ``articles``.

    Taken before and after a pass to report how many articles were new. Cheaper in code
    than threading a RETURNING clause through a batch insert, and the number it produces
    is the one that matters — how much the corpus grew.
    """
    row = await fetch_one(connection_pool, "SELECT count(*) AS n FROM articles")
    return int(row["n"]) if row else 0


async def save_articles(
    connection_pool: AsyncConnectionPool, source: Source, articles: Sequence[FetchedArticle]
) -> None:
    """Insert everything the source yielded, skipping what is already stored."""
    await execute_many(
        connection_pool,
        """
        INSERT INTO articles
            (source_id, url, url_hash, title, lead, published_at, fetch_level, content_hash)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (url_hash) DO NOTHING
        """,
        [
            (
                source.id,
                article.url,
                digest(article.url),
                article.title,
                article.lead,
                article.published_at,
                article.fetch_level,
                # ponytail: computed and stored, never compared — and now deliberately so.
                # The analysis step exists, and the answer it gives is "nothing": ON
                # CONFLICT DO NOTHING never updates an existing row, so a stored article's
                # content_hash cannot change no matter how often a portal rewrites the
                # headline. Re-analysing edited articles would mean changing the upsert
                # first; the column is here to make that a one-line comparison when it
                # is asked for.
                digest(article.title, article.lead or ""),
            )
            for article in articles
        ],
    )


async def record_error(
    connection_pool: AsyncConnectionPool,
    source_id: uuid.UUID | None,
    url: str,
    error_type: str,
    message: str | None,
    raw_response: str | None = None,
) -> None:
    """Write one row to ``fetch_errors``."""
    await execute(
        connection_pool,
        """
        INSERT INTO fetch_errors (source_id, url, error_type, error_message, raw_response)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (source_id, url, error_type, message, raw_response),
    )


async def record_fetch_failure(
    connection_pool: AsyncConnectionPool, source: Source, result: FetchResult
) -> None:
    """Record whatever went wrong with one source's fetch."""
    await record_error(
        connection_pool,
        source.id,
        source.address,
        result.error_type or "unknown",
        result.error_message,
        result.raw_response,
    )


async def mark_fetched(
    connection_pool: AsyncConnectionPool, source_id: uuid.UUID, etag: str | None
) -> None:
    """Record a successful read, storing the validator for the next conditional GET.

    ``last_etag`` is assigned rather than coalesced: a portal that stops sending one must
    stop being sent a stale ``If-None-Match``, or it would answer 304 forever.
    """
    await execute(
        connection_pool,
        "UPDATE sources SET last_fetch_at = now(), last_etag = %s WHERE id = %s",
        (etag, source_id),
    )


async def mark_unchanged(connection_pool: AsyncConnectionPool, source_id: uuid.UUID) -> None:
    """Record a 304, keeping the existing validator.

    A 304 does not always repeat the ETag, and overwriting it with the absent one would
    turn every following pass back into a full download.
    """
    await execute(
        connection_pool,
        "UPDATE sources SET last_fetch_at = now() WHERE id = %s",
        (source_id,),
    )
