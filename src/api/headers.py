"""Response headers that cost nothing and close whole classes of attack.

Set here rather than in the reverse proxy, because an installation's proxy is whatever the
operator happens to run and this has to hold on the default deployment too. A proxy that
sets them again is harmless; one that never sets them is the case worth designing for.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Final

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# The API returns JSON, never markup, so it can afford the strictest policy there is: no
# scripts, no frames, no loading of anything. The front end ships its own, looser policy for
# the pages a person actually looks at.
HEADERS: Final[dict[str, str]] = {
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    # Browsers ignore this over plain HTTP, so it is safe to send unconditionally and wrong
    # to make configurable — an operator who forgets to turn it on is the whole risk.
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    # Nothing here needs a camera, a microphone or a location, and saying so means a future
    # dependency cannot quietly ask for one.
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), interest-cohort=()",
}


class SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        for header, value in HEADERS.items():
            response.headers.setdefault(header, value)
        return response
