"""What every handler is given: the pool, the settings, and who is asking.

One pool for the process, opened by the lifespan and handed to handlers by reference. A
connection per request is the failure mode `db.pool` was written to avoid, and a web
process is where it actually bites.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from psycopg_pool import AsyncConnectionPool

from api.security import SESSION_COOKIE, Principal, resolve_session
from config import Settings


def get_pool(request: Request) -> AsyncConnectionPool:
    connection_pool: AsyncConnectionPool = request.app.state.pool
    return connection_pool


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


Pool = Annotated[AsyncConnectionPool, Depends(get_pool)]
Config = Annotated[Settings, Depends(get_settings)]


async def optional_user(request: Request, connection_pool: Pool) -> Principal | None:
    """Who is asking, if anyone. The feed serves readers who are not logged in."""
    return await resolve_session(connection_pool, request.cookies.get(SESSION_COOKIE))


async def require_user(
    principal: Annotated[Principal | None, Depends(optional_user)],
) -> Principal:
    if principal is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "sign in first")
    return principal


async def require_admin(
    principal: Annotated[Principal, Depends(require_user)],
) -> Principal:
    """404 rather than 403: the operator panel does not confirm its own existence to
    someone who may not use it."""
    if not principal.is_admin:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    return principal


MaybeUser = Annotated[Principal | None, Depends(optional_user)]
CurrentUser = Annotated[Principal, Depends(require_user)]
AdminUser = Annotated[Principal, Depends(require_admin)]
