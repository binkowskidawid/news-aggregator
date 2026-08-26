"""Fixtures for the tests that need a real database.

The queue is SQL: the back-off is a ``WHERE`` clause, the retirement of an article after
three provider failures is a ``CASE`` inside one ``UPDATE``, and the analysis writer spans
two tables. None of that is exercised by a unit test with a fake connection, which is why
these fixtures exist rather than a mock.

A separate database, not a transaction rolled back on the working one. The working
database holds the gold set, which is the project's measuring instrument; a test that
truncates the wrong table there costs an annotation session, and no amount of care in the
test body is worth more than not pointing at it in the first place.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from httpx import ASGITransport, AsyncClient
from psycopg_pool import AsyncConnectionPool

from config import ConfigError, Settings, load_dotenv
from db import pool

TEST_DATABASE: Final = "news_test"
MIGRATIONS: Final = Path(__file__).resolve().parents[1] / "migrations"

# Every table the migrations create, asked for rather than listed. A hand-written list goes
# stale the moment a migration adds a table, and the failure is not an error: rows simply
# survive into the next test, where they look like a bug in whatever that test asserts.
# Twice already. `schema_migrations` is the ledger that says the schema exists at all.
TRUNCATE: Final = """
    DO $$
    DECLARE tables text;
    BEGIN
        SELECT string_agg(quote_ident(tablename), ', ') INTO tables
        FROM pg_tables
        WHERE schemaname = 'public' AND tablename <> 'schema_migrations';

        IF tables IS NOT NULL THEN
            EXECUTE 'TRUNCATE ' || tables || ' RESTART IDENTITY CASCADE';
        END IF;
    END $$;
    """


def _with_database(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path=f"/{name}"))


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """Create ``news_test`` from the migrations, hand back its URL, drop it afterwards.

    Skips rather than fails when Postgres is not reachable: the unit suite is the part that
    has to run on a laptop without Docker, and a red bar there would train people to ignore
    it.
    """
    load_dotenv()
    try:
        configured = Settings.from_env().database_url
    except ConfigError:
        pytest.skip("DATABASE_URL is not set; copy .env.example to .env")

    try:
        admin = psycopg.connect(
            _with_database(configured, "postgres"), autocommit=True, connect_timeout=3
        )
    except psycopg.OperationalError as exc:
        pytest.skip(f"postgres unavailable ({exc.__class__.__name__}); run `make up`")

    with admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DATABASE}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{TEST_DATABASE}"')

        test_url = _with_database(configured, TEST_DATABASE)
        with psycopg.connect(test_url, autocommit=True) as connection:
            for migration in sorted(MIGRATIONS.glob("*.sql")):
                connection.execute(migration.read_text(encoding="utf-8"))

        yield test_url

        admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DATABASE}" WITH (FORCE)')


@pytest.fixture
async def db_pool(database_url: str) -> AsyncIterator[AsyncConnectionPool]:
    """An empty database and a pool onto it, opened through the same helper production uses."""
    async with pool(database_url) as connection_pool:
        async with connection_pool.connection() as connection:
            await connection.execute(TRUNCATE)
        yield connection_pool


@pytest.fixture
async def client(db_pool: AsyncConnectionPool) -> AsyncIterator[AsyncClient]:
    """The application, wired to the test database instead of the working one.

    Overriding the dependencies rather than running the lifespan: the lifespan opens a pool
    from DATABASE_URL, which points at the corpus, and the gold set lives there.
    """
    from api.deps import get_pool, get_settings
    from api.main import app

    settings = replace(Settings.from_env(), cookie_secure=False)
    app.dependency_overrides[get_pool] = lambda: db_pool
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as http:
            yield http
    finally:
        app.dependency_overrides.clear()
