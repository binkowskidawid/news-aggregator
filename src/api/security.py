"""Passwords and sessions.

Two things here are deliberate and worth not undoing:

The cookie carries a random token, never a database key, and the table stores only its
SHA-256. A session row is therefore not a credential — a leaked backup grants nothing.
SHA-256 rather than Argon2 for this one: the token is 256 bits of ``secrets`` output, so
there is no dictionary to attack and no reason to pay a work factor on every request.

Passwords go through Argon2id with the library's defaults, which track current guidance
better than a number pinned here would. ``check_needs_rehash`` means raising the cost later
re-hashes people as they log in rather than locking them out.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from psycopg_pool import AsyncConnectionPool

from db import execute, execute_many, fetch_one

SESSION_COOKIE: Final = "session"
SESSION_TTL: Final = timedelta(days=30)
TOKEN_BYTES: Final = 32

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


@dataclass(frozen=True, slots=True)
class Principal:
    """Who is making the request."""

    user_id: uuid.UUID
    email: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    try:
        _hasher.verify(stored_hash, password)
    except VerifyMismatchError:
        return False
    return True


def needs_rehash(stored_hash: str) -> bool:
    return _hasher.check_needs_rehash(stored_hash)


def burn_verification_time() -> None:
    """Spend what a real verification would spend, having found no account to verify.

    Called on the miss path so that the endpoint's timing carries no information about which
    addresses exist.
    """
    with suppress(VerifyMismatchError):
        _hasher.verify(_DUMMY_HASH, "")


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

    The expiry is checked in SQL rather than in Python so that a clock difference between
    the application and the database cannot let a session outlive its row.
    """
    if not token:
        return None

    row = await fetch_one(
        connection_pool,
        """
        UPDATE sessions SET last_seen_at = now()
        WHERE token_hash = %s AND expires_at > now()
        RETURNING user_id,
                  (SELECT email FROM users WHERE id = sessions.user_id) AS email,
                  (SELECT role FROM users WHERE id = sessions.user_id) AS role
        """,
        (_digest(token),),
    )
    if row is None:
        return None

    return Principal(user_id=row["user_id"], email=row["email"], role=row["role"])


async def close_session(connection_pool: AsyncConnectionPool, token: str | None) -> None:
    if not token:
        return
    await execute(connection_pool, "DELETE FROM sessions WHERE token_hash = %s", (_digest(token),))


async def close_all_sessions(connection_pool: AsyncConnectionPool, user_id: uuid.UUID) -> None:
    """Log one account out everywhere. The reason sessions are rows and not tokens."""
    await execute(connection_pool, "DELETE FROM sessions WHERE user_id = %s", (user_id,))
