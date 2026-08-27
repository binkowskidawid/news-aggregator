"""The ceiling on request bodies.

Both halves are tested because either alone is bypassable, and the bypass is silent: a
sender that omits ``Content-Length`` under chunked encoding gets no error, just an
unbounded read. The chunked case is the one worth having a test for, since nothing in
ordinary use produces it and a refactor could drop it without any endpoint noticing.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from httpx import AsyncClient

from api.limits import MAX_BODY_BYTES

EMAIL = "za-duzy@przyklad-testowy.pl"
PASSWORD = "dostatecznie-dlugie-haslo"


def oversized() -> bytes:
    """A syntactically valid registration whose padding puts it over the ceiling."""
    return json.dumps({"email": EMAIL, "password": "a" * (MAX_BODY_BYTES + 1)}).encode()


async def in_chunks(body: bytes) -> AsyncIterator[bytes]:
    """Streamed in pieces, which is what makes httpx send it chunked and omit the length."""
    for start in range(0, len(body), 8192):
        yield body[start : start + 8192]


class TestRequestSizeLimit:
    async def test_a_declared_length_over_the_ceiling_is_refused(self, client: AsyncClient) -> None:
        response = await client.post(
            "/auth/register", content=oversized(), headers={"content-type": "application/json"}
        )

        assert response.status_code == 413
        assert response.json() == {"detail": "Request body too large"}

    async def test_a_body_without_a_declared_length_is_counted_as_it_arrives(
        self, client: AsyncClient
    ) -> None:
        # No content-length at all, so the first check cannot see this one coming.
        response = await client.post(
            "/auth/register",
            content=in_chunks(oversized()),
            headers={"content-type": "application/json"},
        )

        assert "content-length" not in response.request.headers
        assert response.status_code == 413

    async def test_an_ordinary_body_is_untouched(self, client: AsyncClient) -> None:
        response = await client.post("/auth/register", json={"email": EMAIL, "password": PASSWORD})

        assert response.status_code == 201

    async def test_the_refusal_still_carries_the_security_headers(
        self, client: AsyncClient
    ) -> None:
        # Proves the middleware order: a 413 that skipped SecurityHeaders would be the one
        # response in the service served without them.
        response = await client.post(
            "/auth/register", content=oversized(), headers={"content-type": "application/json"}
        )

        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "no-referrer"
