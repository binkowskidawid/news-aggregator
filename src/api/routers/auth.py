"""Registration, sign-in, sign-out.

Everything that decides who someone is. The account itself is thin — an address, a hash and
a role — because the reader-facing product works fine without one; accounts exist so that
saving an article and following a category have somewhere to live.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status
from psycopg.errors import UniqueViolation
from pydantic import BaseModel, EmailStr, Field, SecretStr

from api.deps import Config, CurrentUser, Pool
from api.security import (
    SESSION_COOKIE,
    SESSION_TTL,
    burn_verification_time,
    clear_failures,
    client_address,
    close_session,
    hash_password,
    needs_rehash,
    open_session,
    record_failure,
    too_many_failures,
    verify_password,
)
from db import execute, fetch_one

router = APIRouter(prefix="/auth", tags=["auth"])

# Long enough to matter, short enough that nobody invents a workaround. No composition
# rules: they push people towards `Password1!` and buy nothing a length floor does not.
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 200


class Credentials(BaseModel):
    email: EmailStr
    password: SecretStr = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)


class SignIn(BaseModel):
    """Sign-in does not repeat the length rule: an old password shorter than today's floor
    must still get its owner in, and rejecting it here would leak the rule to a guesser."""

    email: EmailStr
    password: SecretStr


class Account(BaseModel):
    email: str
    role: str


def _set_cookie(response: Response, token: str, *, secure: bool) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )


@router.post("/register", response_model=Account, status_code=status.HTTP_201_CREATED)
async def register(
    credentials: Credentials, connection_pool: Pool, settings: Config, response: Response
) -> Account:
    try:
        row = await fetch_one(
            connection_pool,
            "INSERT INTO users (email, password_hash) VALUES (%s, %s) RETURNING id, email, role",
            (
                str(credentials.email),
                await hash_password(credentials.password.get_secret_value()),
            ),
        )
    except UniqueViolation:
        # The address is already taken, and saying so is the honest answer: a sign-up form
        # that pretends otherwise still reveals it on the next sign-in attempt, having
        # meanwhile told the actual owner nothing.
        raise HTTPException(
            status.HTTP_409_CONFLICT, "an account with that address already exists"
        ) from None

    if row is None:  # pragma: no cover — RETURNING always yields on a successful INSERT
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "account was not created")

    token = await open_session(connection_pool, row["id"])
    _set_cookie(response, token, secure=settings.cookie_secure)
    return Account(email=row["email"], role=row["role"])


@router.post("/login", response_model=Account)
async def login(
    request: Request,
    credentials: SignIn,
    connection_pool: Pool,
    settings: Config,
    response: Response,
) -> Account:
    # Per address always, so that a single account cannot be ground down. Per client only
    # where the client can actually be told apart from the proxy in front of this service —
    # otherwise every reader shares one budget and ten wrong passwords lock out the whole
    # installation. `client_address` returns None unless the operator has said a reverse
    # proxy sets the header; see TRUST_PROXY_IP in .env.example and OPERATOR.md.
    identifiers = [str(credentials.email).lower()]
    caller = client_address(request, trust_proxy=settings.trust_proxy_ip)
    if caller is not None:
        identifiers.append(f"ip:{caller}")

    if await too_many_failures(connection_pool, identifiers):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many attempts; try again later")

    row = await fetch_one(
        connection_pool,
        "SELECT id, email, role, password_hash FROM users WHERE lower(email) = lower(%s)",
        (str(credentials.email),),
    )

    # One message for both "no such account" and "wrong password", and one duration too.
    # Identical wording with different timing is still an enumeration oracle.
    if row is None:
        await burn_verification_time()
        await record_failure(connection_pool, identifiers)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "wrong address or password")

    password = credentials.password.get_secret_value()
    if not await verify_password(row["password_hash"], password):
        await record_failure(connection_pool, identifiers)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "wrong address or password")

    # The one moment the plaintext is in hand and the account is known to be its owner's, so
    # it is the only moment a cost raised since this hash was written can be applied. Without
    # it, raising the Argon2 parameters would reach new accounts and leave every existing one
    # at the old cost forever.
    if needs_rehash(row["password_hash"]):
        await execute(
            connection_pool,
            "UPDATE users SET password_hash = %s WHERE id = %s",
            (await hash_password(password), row["id"]),
        )

    await clear_failures(connection_pool, identifiers)
    token = await open_session(connection_pool, row["id"])
    _set_cookie(response, token, secure=settings.cookie_secure)
    return Account(email=row["email"], role=row["role"])


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, connection_pool: Pool, response: Response) -> None:
    """Deletes the row, not just the cookie. A cookie cleared in one browser while the
    session stays valid is exactly the gap server-side sessions exist to close."""
    await close_session(connection_pool, request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/me", response_model=Account)
async def me(principal: CurrentUser) -> Account:
    return Account(email=principal.email, role=principal.role)
