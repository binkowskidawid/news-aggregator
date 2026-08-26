"""Saved articles, followed categories, and erasure.

The erasure test is the one that matters legally rather than functionally: whoever runs an
installation is the controller of the personal data in it, and "delete my account" has to
leave nothing behind. A cascade that quietly stops working leaves rows nobody can see and
nobody deleted.
"""

from __future__ import annotations

import uuid

from httpx import AsyncClient
from psycopg_pool import AsyncConnectionPool

from db import execute, fetch_all, fetch_one
from tests.test_api_auth import EMAIL, register


async def _analysed_article(connection_pool: AsyncConnectionPool) -> uuid.UUID:
    """One article carrying a production-configuration analysis, so it reaches the feed."""
    source = await fetch_one(
        connection_pool,
        """
        INSERT INTO sources (name, base_url, rss_url, strategy)
        VALUES ('Test', 'https://t.test', 'https://t.test/rss', 'rss')
        RETURNING id
        """,
    )
    article = await fetch_one(
        connection_pool,
        """
        INSERT INTO articles (source_id, url, url_hash, title, fetch_level, content_hash,
                              status, category)
        VALUES (%s, 'https://t.test/1', %s, 'Tytuł', 1, %s, 'analyzed', 'polityka')
        RETURNING id
        """,
        (source["id"] if source else None, uuid.uuid4().hex, uuid.uuid4().hex),
    )
    article_id: uuid.UUID = article["id"] if article else uuid.uuid4()
    await fetch_one(
        connection_pool,
        """
        INSERT INTO analyses (article_id, run_id, provider, model_name, prompt_version,
                              input_variant, grammar_mode, category, overall_assessment)
        VALUES (%s, %s, 'ollama', 'gemma4:latest', 'v1.1.3', 'title', 'schema',
                'polityka', 'neutral')
        RETURNING id
        """,
        (article_id, uuid.uuid4()),
    )
    return article_id


class TestSavedArticles:
    async def test_saving_twice_is_the_same_as_saving_once(
        self, client: AsyncClient, db_pool: AsyncConnectionPool
    ) -> None:
        await register(client)
        article_id = await _analysed_article(db_pool)

        assert (await client.put(f"/me/saved/{article_id}")).status_code == 204
        assert (await client.put(f"/me/saved/{article_id}")).status_code == 204

        saved = (await client.get("/me/saved")).json()
        assert [item["id"] for item in saved] == [str(article_id)]

    async def test_saving_an_article_that_does_not_exist_is_a_404(
        self, client: AsyncClient
    ) -> None:
        await register(client)

        response = await client.put(f"/me/saved/{uuid.uuid4()}")

        assert response.status_code == 404

    async def test_the_saved_list_needs_an_account(self, client: AsyncClient) -> None:
        assert (await client.get("/me/saved")).status_code == 401


class TestSubscriptions:
    async def test_the_set_sent_is_the_set_stored(self, client: AsyncClient) -> None:
        await register(client)

        await client.put("/me/subscriptions", json={"categories": ["polityka", "sport"]})
        await client.put("/me/subscriptions", json={"categories": ["kultura"]})

        assert (await client.get("/me/subscriptions")).json() == {"categories": ["kultura"]}

    async def test_an_unknown_category_is_refused(self, client: AsyncClient) -> None:
        """The CHECK constraint and the enum say the same thing; neither is decorative."""
        await register(client)

        response = await client.put("/me/subscriptions", json={"categories": ["kryptowaluty"]})

        assert response.status_code == 422


class TestExport:
    async def test_the_copy_carries_what_the_account_holds(
        self, client: AsyncClient, db_pool: AsyncConnectionPool
    ) -> None:
        await register(client)
        article_id = await _analysed_article(db_pool)
        await client.put(f"/me/saved/{article_id}")
        await client.put("/me/subscriptions", json={"categories": ["polityka", "sport"]})

        export = (await client.get("/me/export")).json()

        assert export["email"] == EMAIL
        assert [item["id"] for item in export["saved_articles"]] == [str(article_id)]
        assert sorted(export["subscriptions"]) == ["polityka", "sport"]
        assert len(export["sessions"]) == 1

    async def test_no_credential_leaves_in_the_copy(self, client: AsyncClient) -> None:
        """The point of the export is to hand someone their data, not a way back into the
        account. A password hash is offline-attackable and a session digest is a live
        credential; neither answers a question the timestamps do not."""
        await register(client)

        export = (await client.get("/me/export")).json()

        assert set(export["sessions"][0]) == {"created_at", "last_seen_at", "expires_at"}
        assert "password_hash" not in export
        assert "token_hash" not in str(export)

    async def test_the_copy_needs_an_account(self, client: AsyncClient) -> None:
        assert (await client.get("/me/export")).status_code == 401


class TestOperatorSurface:
    """404 for everyone but an admin, and the same 404 whether or not they are signed in.

    Asserted here rather than in the browser tests, because what matters is the status code
    the API chooses. A 403 would confirm the panel is there to precisely the person
    `require_admin` declines to confirm it to.
    """

    PATHS = ("/ops/overview", "/ops/checks")

    async def test_a_signed_out_reader_is_told_to_sign_in(self, client: AsyncClient) -> None:
        for path in self.PATHS:
            assert (await client.get(path)).status_code == 401, path

    async def test_a_signed_in_reader_without_the_role_gets_404(self, client: AsyncClient) -> None:
        await register(client)

        for path in self.PATHS:
            assert (await client.get(path)).status_code == 404, path

    async def test_an_admin_gets_the_panel(
        self, client: AsyncClient, db_pool: AsyncConnectionPool
    ) -> None:
        await register(client)
        await execute(db_pool, "UPDATE users SET role = 'admin' WHERE email = %s", (EMAIL,))

        overview = await client.get("/ops/overview")

        assert overview.status_code == 200
        assert set(overview.json()) >= {"corpus", "prompt_versions", "queue", "finding_types"}
        assert (await client.get("/ops/checks")).status_code == 200


class TestErasure:
    async def test_deleting_an_account_leaves_nothing_behind(
        self, client: AsyncClient, db_pool: AsyncConnectionPool
    ) -> None:
        await register(client)
        article_id = await _analysed_article(db_pool)
        await client.put(f"/me/saved/{article_id}")
        await client.put("/me/subscriptions", json={"categories": ["polityka"]})

        assert (await client.delete("/me")).status_code == 204

        for table in ("users", "sessions", "saved_articles", "subscriptions"):
            rows = await fetch_all(db_pool, f"SELECT 1 FROM {table}")  # noqa: S608
            assert rows == [], f"{table} still holds rows after erasure"

    async def test_the_article_survives_the_reader_who_saved_it(
        self, client: AsyncClient, db_pool: AsyncConnectionPool
    ) -> None:
        """The cascade must reach the join rows and stop there. Corpus is not personal data
        and deleting a reader must not delete the press."""
        await register(client)
        article_id = await _analysed_article(db_pool)
        await client.put(f"/me/saved/{article_id}")

        await client.delete("/me")

        assert await fetch_all(db_pool, "SELECT 1 FROM articles") != []
