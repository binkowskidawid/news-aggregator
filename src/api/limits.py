"""A ceiling on how much request body this service will read.

Set here rather than in the reverse proxy, for the reason `headers.py` gives: an
installation's proxy is whatever the operator happens to run, and this has to hold on the
default deployment too. Starlette buffers a JSON body in memory before any handler sees it,
so without a ceiling one request decides how much memory the process uses.

Pure ASGI rather than ``BaseHTTPMiddleware``, because that class reads the body before it
hands the request on — a limit built there would run after the damage.
"""

from __future__ import annotations

from typing import Final

from starlette.datastructures import Headers
from starlette.requests import ClientDisconnect
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_BODY_BYTES: Final = 64 * 1024
"""The largest body any endpoint here has a use for.

The biggest real one is ``PUT /me/subscriptions``: a list of source slugs, tens of bytes
each. This leaves three orders of magnitude of headroom, so it can only be reached
deliberately.
"""

_TOO_LARGE_BODY: Final = b'{"detail":"Request body too large"}'


class RequestSizeLimit:
    """Reject bodies over ``MAX_BODY_BYTES`` with 413, before they are held in memory.

    Two checks, because either alone is bypassable. ``Content-Length`` catches the ordinary
    case at zero cost, but the sender writes that header and can omit it under
    ``Transfer-Encoding: chunked``; counting the bytes as they arrive catches the rest, and
    also catches a declared length that lied.
    """

    def __init__(self, app: ASGIApp, max_bytes: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = Headers(scope=scope).get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            await _reject(send)
            return

        received = 0
        exceeded = False

        async def receive_counted() -> Message:
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    exceeded = True
                    # Ends the body here. Starlette turns this into ClientDisconnect, which
                    # unwinds the handler; the 413 below is the reply that reaches the client.
                    return {"type": "http.disconnect"}
            return message

        async def send_unless_exceeded(message: Message) -> None:
            # Whatever the handler made of a body cut short is not an answer to the request
            # that was sent, so it does not go out.
            if not exceeded:
                await send(message)

        try:
            await self.app(scope, receive_counted, send_unless_exceeded)
        except ClientDisconnect:
            # Ours, if we cut the body. A real one belongs to the caller above.
            if not exceeded:
                raise

        if exceeded:
            await _reject(send)


async def _reject(send: Send) -> None:
    """Send the 413 as raw ASGI messages.

    A ``Response`` called as an application would need a scope and a receive channel that do
    not exist on the path where the length is rejected before the body is read.
    """
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(_TOO_LARGE_BODY)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": _TOO_LARGE_BODY})
