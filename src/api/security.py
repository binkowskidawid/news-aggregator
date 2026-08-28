"""Passwords and sessions.

Three things here are deliberate and worth not undoing:

The cookie carries a random token, never a database key, and the table stores only its
SHA-256. A session row is therefore not a credential — a leaked backup grants nothing.
SHA-256 rather than Argon2 for this one: the token is 256 bits of ``secrets`` output, so
there is no dictionary to attack and no reason to pay a work factor on every request.

Passwords go through Argon2id with the library's defaults, which track current guidance
better than a number pinned here would. ``check_needs_rehash`` is called on every successful
sign-in, so raising the cost later re-hashes people as they log in rather than locking them
out — and hashing runs in a thread under :data:`ARGON2_CONCURRENCY`, because a 25 ms call
made from the event loop is 25 ms in which this process serves nobody.

:func:`client_address` answers ``None`` rather than naming the connection. That looks like a
weaker limit and is the opposite: the front end proxies ``/api/*`` here, so the connection
belongs to one container for every reader, and a budget counted against it is a single
bucket that ten wrong passwords empty for everybody. Guessing an address is worse than
admitting there isn't one.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

import anyio
from anyio import to_thread
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from psycopg_pool import AsyncConnectionPool
from starlette.requests import Request

from db import execute, execute_many, fetch_one

SESSION_COOKIE: Final = "session"
SESSION_TTL: Final = timedelta(days=30)
TOKEN_BYTES: Final = 32

SESSION_SEEN_INTERVAL: Final = timedelta(minutes=5)
"""How stale ``last_seen_at`` may get before a request refreshes it.

Every authenticated request resolves a session, and the layout and the page each ask
independently — so refreshing unconditionally wrote a dead tuple per page view for a column
nothing reads at that resolution. The read stays one query; the write happens at most this
often per session.
"""

# Enough to absorb a person mistyping a password, far below what guessing needs. Counted per
# address and per client address separately, so neither one account nor one attacker can use
# the other's budget.
MAX_FAILED_LOGINS: Final = 10
LOGIN_WINDOW: Final = timedelta(minutes=15)

_hasher = PasswordHasher()

# Verifying against a real hash when no account exists costs the same as verifying against a
# real account's. Without it the endpoint answers "unknown address" in a millisecond and
# "wrong password" in fifty, which is an account-enumeration oracle that identical response
# bodies do nothing to hide.
_DUMMY_HASH: Final = _hasher.hash(secrets.token_urlsafe(TOKEN_BYTES))

ARGON2_CONCURRENCY: Final = 4
"""How many password hashes may be computed at once.

Argon2id is deliberately expensive: measured here at 25 ms and 64 MiB per call with the
library's defaults. Two consequences, and the limiter answers both.

Called straight from an ``async def`` handler it blocks the event loop, so one sign-in
stalls every other request in the process — including the health check. Moved to a thread
and left unbounded it is worse: uvicorn's default pool is 40 threads, which is 2.5 GB of
Argon2 memory reachable from an endpoint that needs no account to call.

Four is the pair that holds: the loop stays free and the ceiling is 256 MiB. Raise it only
alongside a measurement of the host's memory, never to make a load test look better.
"""

_ARGON2_LIMITER: Final = anyio.CapacityLimiter(ARGON2_CONCURRENCY)


@dataclass(frozen=True, slots=True)
class Principal:
    """Who is making the request."""

    user_id: uuid.UUID
    email: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def _hash(password: str) -> str:
    return _hasher.hash(password)


def _verify(stored_hash: str, password: str) -> bool:
    try:
        _hasher.verify(stored_hash, password)
    except VerifyMismatchError:
        return False
    return True


def _burn() -> None:
    with suppress(VerifyMismatchError):
        _hasher.verify(_DUMMY_HASH, "")


async def hash_password(password: str) -> str:
    return await to_thread.run_sync(_hash, password, limiter=_ARGON2_LIMITER)


async def verify_password(stored_hash: str, password: str) -> bool:
    return await to_thread.run_sync(_verify, stored_hash, password, limiter=_ARGON2_LIMITER)


def needs_rehash(stored_hash: str) -> bool:
    """Whether this hash predates the current cost parameters.

    Cheap — it parses the encoded parameters out of the string and compares them, without
    hashing anything — so it stays synchronous and off the limiter.
    """
    return _hasher.check_needs_rehash(stored_hash)


async def burn_verification_time() -> None:
    """Spend what a real verification would spend, having found no account to verify.

    Called on the miss path so that the endpoint's timing carries no information about which
    addresses exist. It goes through the same limiter as a real verification, or the miss
    path would be the cheap one again under load.
    """
    await to_thread.run_sync(_burn, limiter=_ARGON2_LIMITER)


def client_address(request: Request, *, trust_proxy: bool) -> str | None:
    """The caller's own address, or ``None`` when nothing here can establish it.

    This is the question the per-address budget rests on, and getting it wrong inverts the
    budget into an outage. The front end proxies every ``/api/*`` path to this service, so
    ``request.client.host`` is that one container for every reader — count failures against
    it and ten wrong passwords from anybody lock out everybody.

    ``None`` is therefore the honest answer by default, and the caller drops the per-address
    budget rather than counting against a shared bucket. An operator who has put a reverse
    proxy in front sets ``TRUST_PROXY_IP=1``, and only then is the header believed.

    The **rightmost** entry of ``X-Forwarded-For`` is the one taken: it is what the hop
    closest to this service wrote. Everything to its left arrived from the caller and is not
    evidence about anything — Next's own rewrite preserves a client-supplied header rather
    than replacing it, so the proxy in front has to be the one appending or overwriting.
    """
    if not trust_proxy:
        return None
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.rsplit(",", 1)[-1].strip() or None
    return request.client.host if request.client else None


async def too_many_failures(connection_pool: AsyncConnectionPool, identifiers: list[str]) -> bool:
    """Whether any of these identifiers has spent its budget of failed attempts.

    Expired rows are deleted here rather than by a scheduled job: the check already touches
    the table, the delete is bounded by the same index, and a retention rule nobody has to
    remember to run is a retention rule that actually happens.
    """
    await execute(
        connection_pool,
        "DELETE FROM login_attempts WHERE attempted_at < %s",
        (datetime.now(UTC) - LOGIN_WINDOW,),
    )
    row = await fetch_one(
        connection_pool,
        """
        SELECT identifier FROM login_attempts
        WHERE identifier = ANY(%s) AND attempted_at > %s
        GROUP BY identifier HAVING count(*) >= %s
        LIMIT 1
        """,
        (identifiers, datetime.now(UTC) - LOGIN_WINDOW, MAX_FAILED_LOGINS),
    )
    return row is not None


async def record_failure(connection_pool: AsyncConnectionPool, identifiers: list[str]) -> None:
    await execute_many(
        connection_pool,
        "INSERT INTO login_attempts (identifier) VALUES (%s)",
        [(identifier,) for identifier in identifiers],
    )


async def clear_failures(connection_pool: AsyncConnectionPool, identifiers: list[str]) -> None:
    """A successful sign-in returns the budget. Otherwise a person who mistyped their password
    nine times and then got it right stays one mistake away from a lockout they earned."""
    await execute(
        connection_pool, "DELETE FROM login_attempts WHERE identifier = ANY(%s)", (identifiers,)
    )


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def open_session(connection_pool: AsyncConnectionPool, user_id: uuid.UUID) -> str:
    """Create a session and return the token to put in the cookie.

    The raw token is returned once and never stored, so it exists in the response and in
    that browser and nowhere else.
    """
    token = secrets.token_urlsafe(TOKEN_BYTES)
    await execute(
        connection_pool,
        "INSERT INTO sessions (user_id, token_hash, expires_at) VALUES (%s, %s, %s)",
        (user_id, _digest(token), datetime.now(UTC) + SESSION_TTL),
    )
    return token


async def resolve_session(
    connection_pool: AsyncConnectionPool, token: str | None
) -> Principal | None:
    """Look up who a cookie belongs to, refusing anything expired.

    Both the expiry and the staleness of ``last_seen_at`` are decided in SQL rather than in
    Python, so a clock difference between the application and the database can neither let a
    session outlive its row nor decide how often the column is refreshed.

    A read, and a write only when there is something to write — see
    :data:`SESSION_SEEN_INTERVAL`. Refreshing inside the lookup made every authenticated
    request an ``UPDATE``, which is a dead tuple per page view on a column read once, by an
    operator, on the account page.
    """
    if not token:
        return None

    digest = _digest(token)
    row = await fetch_one(
        connection_pool,
        """
        SELECT s.user_id, u.email, u.role,
               s.last_seen_at < now() - %s AS stale
        FROM sessions s
        JOIN users u ON u.id = s.user_id
        WHERE s.token_hash = %s AND s.expires_at > now()
        """,
        (SESSION_SEEN_INTERVAL, digest),
    )
    if row is None:
        return None

    if row["stale"]:
        await execute(
            connection_pool,
            "UPDATE sessions SET last_seen_at = now() WHERE token_hash = %s",
            (digest,),
        )

    return Principal(user_id=row["user_id"], email=row["email"], role=row["role"])


async def close_session(connection_pool: AsyncConnectionPool, token: str | None) -> None:
    if not token:
        return
    await execute(connection_pool, "DELETE FROM sessions WHERE token_hash = %s", (_digest(token),))


async def close_all_sessions(connection_pool: AsyncConnectionPool, user_id: uuid.UUID) -> None:
    """Log one account out everywhere. The reason sessions are rows and not tokens."""
    await execute(connection_pool, "DELETE FROM sessions WHERE user_id = %s", (user_id,))
