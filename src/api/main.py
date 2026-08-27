"""The HTTP service.

    make api                       # run it locally against the configured database
    docker compose --profile full up -d

One process, one pool, opened by the lifespan and closed with it. Scheduling, fetching and
analysis stay where they are: this layer only reads what they wrote.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Final

from fastapi import FastAPI

from api.headers import SecurityHeaders
from api.limits import RequestSizeLimit
from api.routers import auth, feed, me, ops
from config import Settings, load_dotenv
from db import pool

# A web process serves concurrent readers, unlike the one-pass commands the default was
# chosen for. Still small: the feed is two indexed reads and Postgres is not the bottleneck.
POOL_SIZE: Final = 10

logger = logging.getLogger("api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    load_dotenv()
    settings = Settings.from_env()
    async with pool(settings.database_url, max_size=POOL_SIZE) as connection_pool:
        app.state.pool = connection_pool
        app.state.settings = settings
        logger.info("api ready")
        yield


# No reader-facing text is produced here. Responses carry codes and facts — a finding type,
# a category, `ai_generated` — and the words a person reads are translated in the front end.
# That is what keeps this layer one language and the product several.
# Read before the app exists, because the documentation routes are decided at construction.
load_dotenv()
_DOCS = Settings.api_docs_enabled()

app = FastAPI(
    title="Press language analysis",
    summary=(
        "Reports fragments of headlines and leads together with the quote each refers to. "
        "Every finding comes from a language model and needs checking against the source."
    ),
    lifespan=lifespan,
    # None removes the route entirely rather than hiding it. `/api/*` is proxied verbatim by
    # the front end, so anything mounted here is served to every reader — and `/openapi.json`
    # lists `/ops/*`, undoing the 404 that exists so the operator surface is not confirmed.
    docs_url="/docs" if _DOCS else None,
    redoc_url="/redoc" if _DOCS else None,
    openapi_url="/openapi.json" if _DOCS else None,
)

# Added first, so it ends up inside SecurityHeaders: the last one added is the outermost.
# That order is deliberate — a 413 is still a response someone's browser reads, and it gets
# the same headers as any other. Nothing above it buffers the request body, so the limit
# still runs before anything holds one.
app.add_middleware(RequestSizeLimit)
app.add_middleware(SecurityHeaders)

app.include_router(feed.router)
app.include_router(auth.router)
app.include_router(me.router)
app.include_router(ops.router)


@app.get("/health", tags=["ops"])
async def health() -> dict[str, str]:
    """Liveness for the container. Deliberately does not touch the database: a health check
    that fails on a slow query restarts a process that was working."""
    return {"status": "ok"}
