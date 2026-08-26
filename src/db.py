"""Database access: one pool, opened once per process.

The evaluation harness writes a few hundred rows per sweep and reads them back as
aggregates. That is small, but a connection per call would still be wrong here for a
reason that outlives the harness: these code paths become the ingest and API layers,
where a connection per article is the difference between a working service and a
Postgres instance out of backends.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool


@asynccontextmanager
async def pool(database_url: str, *, max_size: int = 4) -> AsyncGenerator[AsyncConnectionPool]:
    """Open a pool for the lifetime of one command.

    ``open=False`` followed by an explicit ``open()`` avoids psycopg's deprecation warning
    about pools that connect inside ``__init__``. pytest is configured to turn warnings
    into errors, so the shortcut would fail the suite rather than merely print.
    """
    connection_pool = AsyncConnectionPool(database_url, min_size=1, max_size=max_size, open=False)
    await connection_pool.open(wait=True)
    try:
        yield connection_pool
    finally:
        await connection_pool.close()


async def fetch_all(
    connection_pool: AsyncConnectionPool, query: str, params: Sequence[Any] = ()
) -> list[dict[str, Any]]:
    """Run one read and return rows as dictionaries."""
    async with connection_pool.connection() as conn, conn.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(query, params)
        return list(await cursor.fetchall())


async def fetch_one(
    connection_pool: AsyncConnectionPool, query: str, params: Sequence[Any] = ()
) -> dict[str, Any] | None:
    """Run one read expected to return at most a single row."""
    rows = await fetch_all(connection_pool, query, params)
    return rows[0] if rows else None


async def execute(
    connection_pool: AsyncConnectionPool, query: str, params: Sequence[Any] = ()
) -> None:
    """Run one statement that returns nothing."""
    async with connection_pool.connection() as conn:
        await conn.execute(query, params)


async def execute_many(
    connection_pool: AsyncConnectionPool, query: str, rows: Sequence[Sequence[Any]]
) -> None:
    """Insert a batch in one round trip.

    Exists so that writing N findings costs one statement rather than N. The harness runs
    inside a loop over articles, and a query per finding inside that loop is exactly the
    pattern the project's rules single out.
    """
    if not rows:
        return
    async with connection_pool.connection() as conn, conn.cursor() as cursor:
        await cursor.executemany(query, rows)
