"""What an account holds: saved articles, followed categories, a copy of it, and the way out.

The delete and export endpoints are not courtesies. Whoever runs an installation of this is
the controller of the personal data in it, and requests under Articles 15, 17 and 20 GDPR
have to be answerable without a database console. ``ON DELETE CASCADE`` on every table
referencing ``users`` means one statement answers erasure completely; ``/me/export`` answers
access and portability in a format the Regulation asks for, addressed to the person rather
than to the operator, so neither has to wait on the other.
"""

from __future__ import annotations

import uuid
from asyncio import gather
from datetime import datetime

from fastapi import APIRouter, HTTPException, Response, status
from psycopg.errors import ForeignKeyViolation
from pydantic import BaseModel

from api.deps import CurrentUser, Pool
from api.schemas import FeedItem
from api.security import SESSION_COOKIE, close_all_sessions
from db import execute, execute_many, fetch_all, fetch_one
from domain.analysis import Category

router = APIRouter(prefix="/me", tags=["account"])

_SAVED = """
    SELECT a.id, a.title, a.lead, a.url, s.name AS source, a.published_at,
           l.category, l.overall_assessment,
           (SELECT count(*) FROM findings f WHERE f.analysis_id = l.analysis_id) AS finding_count
    FROM saved_articles sa
    JOIN articles a ON a.id = sa.article_id
    JOIN sources s ON s.id = a.source_id
    JOIN article_latest_analysis l ON l.article_id = a.id
    WHERE sa.user_id = %s
    ORDER BY sa.saved_at DESC
    """


class Subscriptions(BaseModel):
    categories: list[Category]


@router.get("/saved", response_model=list[FeedItem])
async def list_saved(principal: CurrentUser, connection_pool: Pool) -> list[FeedItem]:
    rows = await fetch_all(connection_pool, _SAVED, (principal.user_id,))
    return [FeedItem.model_validate(row) for row in rows]


@router.put("/saved/{article_id}", status_code=status.HTTP_204_NO_CONTENT)
async def save_article(
    article_id: uuid.UUID, principal: CurrentUser, connection_pool: Pool
) -> None:
    """PUT rather than POST: saving an article twice is the same as saving it once, and a
    reader double-tapping a bookmark should not get an error for it."""
    try:
        await execute(
            connection_pool,
            """
            INSERT INTO saved_articles (user_id, article_id) VALUES (%s, %s)
            ON CONFLICT (user_id, article_id) DO NOTHING
            """,
            (principal.user_id, article_id),
        )
    except ForeignKeyViolation:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no article with that id") from None


@router.delete("/saved/{article_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unsave_article(
    article_id: uuid.UUID, principal: CurrentUser, connection_pool: Pool
) -> None:
    await execute(
        connection_pool,
        "DELETE FROM saved_articles WHERE user_id = %s AND article_id = %s",
        (principal.user_id, article_id),
    )


@router.get("/subscriptions", response_model=Subscriptions)
async def list_subscriptions(principal: CurrentUser, connection_pool: Pool) -> Subscriptions:
    rows = await fetch_all(
        connection_pool,
        "SELECT category FROM subscriptions WHERE user_id = %s ORDER BY category",
        (principal.user_id,),
    )
    return Subscriptions(categories=[Category(row["category"]) for row in rows])


@router.put("/subscriptions", response_model=Subscriptions)
async def replace_subscriptions(
    subscriptions: Subscriptions, principal: CurrentUser, connection_pool: Pool
) -> Subscriptions:
    """The whole set at once. A settings screen sends what the person wants to end up with,
    and diffing it here costs one delete and one insert instead of a protocol."""
    await execute(
        connection_pool, "DELETE FROM subscriptions WHERE user_id = %s", (principal.user_id,)
    )
    await execute_many(
        connection_pool,
        "INSERT INTO subscriptions (user_id, category) VALUES (%s, %s)",
        [(principal.user_id, category.value) for category in set(subscriptions.categories)],
    )
    return await list_subscriptions(principal, connection_pool)


class Export(BaseModel):
    """Everything stored about one account, in one object.

    Sessions are described, never disclosed: the table holds SHA-256 digests of tokens, and
    a token is a live credential. Their timestamps answer "what do you know about my
    sign-ins"; the digests would answer nothing and hand a downloaded file the power to
    impersonate. The password hash is left out for the same reason.
    """

    email: str
    account_created_at: datetime
    sessions: list[dict[str, datetime]]
    saved_articles: list[FeedItem]
    subscriptions: list[Category]


@router.get("/export", response_model=Export)
async def export_account(principal: CurrentUser, connection_pool: Pool) -> Export:
    """A copy of the account, for Articles 15 and 20 GDPR.

    JSON because the Regulation asks for a structured, commonly used, machine-readable
    format, and because the alternative — the operator running a query by hand for every
    request — is the arrangement that makes a deadline get missed.
    """
    account, sessions, saved, subscriptions = await gather(
        fetch_one(
            connection_pool,
            "SELECT email, created_at FROM users WHERE id = %s",
            (principal.user_id,),
        ),
        fetch_all(
            connection_pool,
            """
            SELECT created_at, last_seen_at, expires_at FROM sessions
            WHERE user_id = %s ORDER BY created_at
            """,
            (principal.user_id,),
        ),
        list_saved(principal, connection_pool),
        list_subscriptions(principal, connection_pool),
    )

    if account is None:  # pragma: no cover — the session resolved, so the row exists
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such account")

    return Export(
        email=account["email"],
        account_created_at=account["created_at"],
        sessions=[dict(row) for row in sessions],
        saved_articles=saved,
        subscriptions=subscriptions.categories,
    )


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(response: Response, principal: CurrentUser, connection_pool: Pool) -> None:
    """Erasure, not deactivation. Sessions go first so that a request in flight on another
    device cannot act on an account that is halfway gone."""
    await close_all_sessions(connection_pool, principal.user_id)
    await execute(connection_pool, "DELETE FROM users WHERE id = %s", (principal.user_id,))
    response.delete_cookie(SESSION_COOKIE, path="/")
