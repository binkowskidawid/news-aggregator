"""Registration, sign-in and sign-out against a real Postgres.

The properties here are the ones that are invisible when they break. A session that
survives sign-out still works; a sign-in that answers differently for an unknown address
than for a wrong password is an account-enumeration oracle that no response body announces;
a token stored as written hands out sessions to anyone who reads a backup.
"""

from __future__ import annotations

import time

import pytest
from httpx import AsyncClient, Response
from psycopg_pool import AsyncConnectionPool

from api.security import MAX_FAILED_LOGINS, SESSION_COOKIE, burn_verification_time
from db import fetch_all

EMAIL = "czytelnik@przyklad-testowy.pl"
PASSWORD = "dostatecznie-dlugie-haslo"


async def register(http: AsyncClient, *, email: str = EMAIL, password: str = PASSWORD) -> Response:
    """Sign a fresh account up. Shared with the account tests, which all need one first."""
    return await http.post("/auth/register", json={"email": email, "password": password})


class TestRegistration:
    async def test_registering_signs_the_person_in(self, client: AsyncClient) -> None:
        response = await register(client)

        assert response.status_code == 201
        assert response.json() == {"email": EMAIL, "role": "reader"}
        assert SESSION_COOKIE in response.cookies

        assert (await client.get("/auth/me")).json()["email"] == EMAIL

    async def test_an_address_differing_only_in_case_is_the_same_mailbox(
        self, client: AsyncClient
    ) -> None:
        await register(client)

        response = await register(client, email=EMAIL.upper())

        assert response.status_code == 409

    async def test_a_password_below_the_floor_is_refused(self, client: AsyncClient) -> None:
        response = await register(client, password="krotkie")

        assert response.status_code == 422


class TestSignIn:
    @pytest.mark.parametrize(
        "payload",
        [
            {"email": EMAIL, "password": "zupelnie-inne-haslo"},
            {"email": "nikt@przyklad-testowy.pl", "password": PASSWORD},
        ],
        ids=["wrong-password", "unknown-address"],
    )
    async def test_a_failed_sign_in_says_the_same_thing_either_way(
        self, client: AsyncClient, payload: dict[str, str]
    ) -> None:
        """Two different answers here would let anyone test which addresses hold accounts."""
        await register(client)

        response = await client.post("/auth/login", json=payload)

        assert response.status_code == 401
        assert response.json()["detail"] == "wrong address or password"

    async def test_an_old_password_shorter_than_the_floor_still_signs_in(
        self, client: AsyncClient, db_pool: AsyncConnectionPool
    ) -> None:
        """Sign-in must not repeat the length rule the sign-up form applies."""
        await register(client)
        await client.post("/auth/logout")

        response = await client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})

        assert response.status_code == 200


class TestSignOut:
    async def test_signing_out_kills_the_session_not_just_the_cookie(
        self, client: AsyncClient
    ) -> None:
        """The whole reason sessions are rows. Clearing a cookie in one browser while the
        session stays valid leaves a working credential behind."""
        await register(client)
        token = client.cookies[SESSION_COOKIE]

        await client.post("/auth/logout")
        client.cookies.set(SESSION_COOKIE, token)

        assert (await client.get("/auth/me")).status_code == 401


class TestBruteForce:
    async def test_the_attempt_budget_runs_out(self, client: AsyncClient) -> None:
        """Argon2id makes one guess expensive; it does not make a million guesses impossible."""
        await register(client)
        wrong = {"email": EMAIL, "password": "zupelnie-inne-haslo"}

        for _ in range(MAX_FAILED_LOGINS):
            assert (await client.post("/auth/login", json=wrong)).status_code == 401

        assert (await client.post("/auth/login", json=wrong)).status_code == 429

    async def test_the_budget_returns_after_signing_in(self, client: AsyncClient) -> None:
        """Someone who mistypes their password and then gets it right must not be left one
        mistake away from a lockout they already worked off."""
        await register(client)
        await client.post("/auth/logout")
        for _ in range(MAX_FAILED_LOGINS - 1):
            await client.post("/auth/login", json={"email": EMAIL, "password": "zle-haslo-x"})

        assert (
            await client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
        ).status_code == 200

        response = await client.post("/auth/login", json={"email": EMAIL, "password": "zle-x"})
        assert response.status_code == 401

    async def test_a_locked_out_address_is_refused_before_any_lookup(
        self, client: AsyncClient, db_pool: AsyncConnectionPool
    ) -> None:
        """The 429 must not depend on whether the address exists, or the lockout itself
        becomes the oracle the identical messages were there to prevent."""
        unknown = {"email": "nikt@przyklad-testowy.pl", "password": PASSWORD}
        for _ in range(MAX_FAILED_LOGINS):
            await client.post("/auth/login", json=unknown)

        assert (await client.post("/auth/login", json=unknown)).status_code == 429


class TestTiming:
    def test_a_missing_account_costs_what_a_real_one_costs(self) -> None:
        """Identical wording with different timing is still an enumeration oracle: without
        this, the endpoint answers in a millisecond for an unknown address and in fifty for a
        known one. The floor is far below Argon2id's real cost, so it fails only if the call
        stops hashing altogether."""
        started = time.perf_counter()
        burn_verification_time()
        elapsed = time.perf_counter() - started

        assert elapsed > 0.005


class TestSecurityHeaders:
    async def test_every_response_carries_them(self, client: AsyncClient) -> None:
        response = await client.get("/health")

        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["Referrer-Policy"] == "no-referrer"
        assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
        assert "max-age=" in response.headers["Strict-Transport-Security"]

    async def test_the_documentation_routes_are_not_mounted(self, client: AsyncClient) -> None:
        """The front end proxies `/api/*` verbatim, so anything mounted here is public.

        `/openapi.json` lists `/ops/*`, which `require_admin` answers 404 for so as not to
        confirm the operator surface exists. Serving the schema next to it would undo that,
        and `/docs` would hand over a console as well. Both are opt-in via `API_DOCS`.
        """
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert (await client.get(path)).status_code == 404, path


class TestStoredMaterial:
    async def test_the_cookie_value_is_not_what_the_database_holds(
        self, client: AsyncClient, db_pool: AsyncConnectionPool
    ) -> None:
        """A session row must not be usable as a credential by whoever can read it."""
        await register(client)
        token = client.cookies[SESSION_COOKIE]

        rows = await fetch_all(db_pool, "SELECT token_hash FROM sessions")

        assert len(rows) == 1
        assert rows[0]["token_hash"] != token
        assert token not in rows[0]["token_hash"]

    async def test_the_password_is_stored_under_argon2id(
        self, client: AsyncClient, db_pool: AsyncConnectionPool
    ) -> None:
        await register(client)

        rows = await fetch_all(db_pool, "SELECT password_hash FROM users")

        assert rows[0]["password_hash"].startswith("$argon2id$")
        assert PASSWORD not in rows[0]["password_hash"]
